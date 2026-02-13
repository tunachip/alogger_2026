from __future__ import annotations

import json
import shlex
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from typing import Any, Callable

from .config import IngesterConfig


class PipelineError(RuntimeError):
    pass


def _extract_video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    values = query.get("v")
    if values and values[0]:
        return values[0]
    if "youtu.be" in parsed.netloc:
        token = parsed.path.strip("/")
        return token or None
    return None


def _parse_existing_paths_from_stdout(stdout: str) -> list[Path]:
    paths: list[Path] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        candidate = Path(line)
        if candidate.exists():
            paths.append(candidate)
    return paths


def _fallback_paths(media_dir: Path, video_id: str | None) -> list[Path]:
    if not video_id:
        return []
    return sorted(
        [
            path
            for path in media_dir.glob(f"{video_id}*")
            if path.is_file() and not path.name.endswith(".part")
        ]
    )


def _select_primary_media(paths: list[Path]) -> Path:
    if not paths:
        raise PipelineError("No downloaded media files were found")
    # Prefer likely video containers, then largest file.
    ext_rank = {
        ".mp4": 4,
        ".mkv": 3,
        ".webm": 2,
        ".mov": 2,
        ".m4v": 2,
        ".m4a": 1,
        ".mp3": 1,
        ".opus": 1,
    }
    return sorted(
        paths,
        key=lambda p: (ext_rank.get(p.suffix.lower(), 0), p.stat().st_size),
        reverse=True,
    )[0]


def _media_has_audio_stream(video_path: Path) -> bool | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    streams = payload.get("streams", [])
    return any(str(s.get("codec_type")) == "audio" for s in streams if isinstance(s, dict))


def _resolve_whisper_output(output_dir: Path, video_path: Path) -> Path:
    # Whisper usually writes "<stem>.json", but filename behavior can vary by codec/container.
    primary = output_dir / f"{video_path.stem}.json"
    if primary.exists():
        return primary
    json_files = sorted([p for p in output_dir.glob("*.json") if p.is_file()])
    if len(json_files) == 1:
        return json_files[0]
    if json_files:
        return sorted(json_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    raise PipelineError(
        f"Whisper output missing in {output_dir}. "
        "No JSON files were produced."
    )


def run_cmd(
    cmd: list[str],
    *,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if on_process:
        on_process(proc)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain_stream(stream: Any, chunks: list[str]) -> None:
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
                chunks.append(data)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    stdout_thread = threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_chunks), daemon=True)
    stderr_thread = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_chunks), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    while proc.poll() is None:
        if should_terminate and should_terminate():
            proc.kill()
            break
        time.sleep(0.1)

    stdout_thread.join()
    stderr_thread.join()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    completed = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    if completed.returncode != 0:
        command = " ".join(shlex.quote(c) for c in cmd)
        raise PipelineError(
            f"Command failed ({completed.returncode}): {command}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def fetch_video_metadata(
    config: IngesterConfig,
    url: str,
    *,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    cmd = [
        config.yt_dlp_binary,
        "--no-warnings",
        "--dump-single-json",
        "--skip-download",
        url,
    ]
    proc = run_cmd(cmd, on_process=on_process, should_terminate=should_terminate)
    return json.loads(proc.stdout)


def download_video(
    config: IngesterConfig,
    url: str,
    video_id: str,
    *,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> Path:
    out_template = config.media_dir / f"{video_id}.%(ext)s"
    cmd = [
        config.yt_dlp_binary,
        "--no-warnings",
        "--newline",
        "--ffmpeg-location",
        config.ffmpeg_binary,
        "-S",
        "res:1080,fps",
        "-f",
        "bestvideo*+bestaudio/best",
        "--merge-output-format",
        "mp4",
        "-o",
        str(out_template),
        url,
    ]
    run_cmd(cmd, on_process=on_process, should_terminate=should_terminate)

    mp4_path = config.media_dir / f"{video_id}.mp4"
    if mp4_path.exists():
        return mp4_path

    matches = _fallback_paths(config.media_dir, video_id)
    if not matches:
        raise PipelineError(f"Downloaded file not found for {video_id}")
    return _select_primary_media(matches)


def transcribe_video(
    config: IngesterConfig,
    video_path: Path,
    video_id: str,
    *,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> Path:
    output_dir = config.transcript_dir / video_id
    output_dir.mkdir(parents=True, exist_ok=True)

    has_audio = _media_has_audio_stream(video_path)
    if has_audio is False:
        raise PipelineError(
            f"Input media has no audio stream: {video_path}. "
            "Use a merged A/V file or an audio-containing stream."
        )

    cmd = [
        config.whisper_binary,
        str(video_path),
        "--model",
        config.whisper_model,
        "--model_dir",
        str(config.whisper_model_dir),
        "--language",
        config.whisper_language,
        "--output_format",
        "json",
        "--output_dir",
        str(output_dir),
        "--verbose",
        "False",
    ]
    run_cmd(cmd, on_process=on_process, should_terminate=should_terminate)

    return _resolve_whisper_output(output_dir, video_path)


def load_whisper_segments(transcript_json_path: Path) -> list[dict[str, Any]]:
    with transcript_json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise PipelineError("Whisper output JSON missing segment list")
    return segments


def download_url_only(config: IngesterConfig, url: str) -> Path:
    out_template = config.media_dir / "%(id)s.%(ext)s"
    cmd = [
        config.yt_dlp_binary,
        "--no-warnings",
        "--no-progress",
        "--newline",
        "--ffmpeg-location",
        config.ffmpeg_binary,
        "-S",
        "res:1080,fps",
        "-f",
        "bestvideo*+bestaudio/best",
        "--merge-output-format",
        "mp4",
        "--print",
        "after_move:filepath",
        "-o",
        str(out_template),
        url,
    ]
    proc = run_cmd(cmd)
    parsed_paths = _parse_existing_paths_from_stdout(proc.stdout)
    if parsed_paths:
        return _select_primary_media(parsed_paths)

    video_id = _extract_video_id_from_url(url)
    fallback = _fallback_paths(config.media_dir, video_id)
    if fallback:
        return _select_primary_media(fallback)

    raise PipelineError(
        "Download finished but output file could not be resolved. "
        "Check ffmpeg availability and yt-dlp output."
    )

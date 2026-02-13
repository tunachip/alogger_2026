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


def _select_primary_video(paths: list[Path]) -> Path:
    if not paths:
        raise PipelineError("No video-capable media files were found")
    ext_rank = {
        ".mkv": 5,
        ".mp4": 4,
        ".webm": 3,
        ".mov": 2,
        ".m4v": 2,
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


def _media_has_video_stream(video_path: Path) -> bool | None:
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
    return any(str(s.get("codec_type")) == "video" for s in streams if isinstance(s, dict))


def _decode_smoke_test(ffmpeg_bin: str, media_path: Path) -> bool:
    cmd = [
        ffmpeg_bin,
        "-v",
        "error",
        "-t",
        "2",
        "-i",
        str(media_path),
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0


def _pick_largest(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=lambda p: p.stat().st_size, reverse=True)[0]


def _ensure_audio_ready_media(
    config: IngesterConfig,
    video_id: str,
    candidates: list[Path],
    *,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> Path:
    if not candidates:
        raise PipelineError(f"Downloaded file not found for {video_id}")

    with_audio: list[Path] = []
    with_video: list[Path] = []
    with_audio_and_video: list[Path] = []
    for path in candidates:
        has_audio = _media_has_audio_stream(path)
        has_video = _media_has_video_stream(path)
        if has_audio is True:
            with_audio.append(path)
        if has_video is True:
            with_video.append(path)
        if has_audio is True and has_video is True:
            with_audio_and_video.append(path)

    if with_audio_and_video:
        return _select_primary_media(with_audio_and_video)

    if with_audio:
        # Transcription can still proceed on audio-only files.
        return _pick_largest(with_audio) or with_audio[0]

    # Fall back to previous primary selection if stream detection failed.
    return _select_primary_media(candidates)


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
    if mp4_path.exists() and _media_has_audio_stream(mp4_path) is True:
        return mp4_path

    matches = _fallback_paths(config.media_dir, video_id)
    return _ensure_audio_ready_media(
        config,
        video_id,
        matches,
        on_process=on_process,
        should_terminate=should_terminate,
    )


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


def resolve_playback_media_path(
    config: IngesterConfig,
    *,
    video_id: str,
    preferred_path: Path | None = None,
) -> Path:
    candidates: list[Path] = []
    if preferred_path and preferred_path.exists():
        candidates.append(preferred_path)
    candidates.extend(_fallback_paths(config.media_dir, video_id))

    unique: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        r = c.resolve()
        if r in seen:
            continue
        seen.add(r)
        unique.append(c)

    with_video = [p for p in unique if _media_has_video_stream(p) is True]
    with_audio_video = [p for p in with_video if _media_has_audio_stream(p) is True]
    if with_audio_video:
        return _select_primary_video(with_audio_video)

    # Try to generate a merged playback container on demand.
    merged = merge_streams_for_playback(config, video_id=video_id)
    if merged is not None and merged.exists():
        return merged

    if not with_video:
        raise PipelineError(
            f"No playable video stream found for video_id={video_id}. "
            "Check downloaded media files."
        )
    return _select_primary_video(with_video)


def merge_streams_for_playback(
    config: IngesterConfig,
    *,
    video_id: str,
    on_process: Callable[[subprocess.Popen[str]], None] | None = None,
    should_terminate: Callable[[], bool] | None = None,
) -> Path | None:
    candidates = _fallback_paths(config.media_dir, video_id)
    if not candidates:
        return None

    with_video = [p for p in candidates if _media_has_video_stream(p) is True]
    with_audio = [p for p in candidates if _media_has_audio_stream(p) is True]
    with_audio_video = [p for p in with_video if _media_has_audio_stream(p) is True]
    if with_audio_video:
        return _select_primary_video(with_audio_video)

    video_source = _pick_largest(with_video)
    audio_only = [
        p for p in with_audio if p not in with_video and _media_has_video_stream(p) is False
    ]
    audio_source = _pick_largest(audio_only)
    if not video_source or not audio_source:
        return None

    merged_mp4 = config.media_dir / f"{video_id}.merged.mp4"
    merged_mkv = config.media_dir / f"{video_id}.merged.mkv"

    # 1) Preferred: MP4 stream-copy remux (fastest).
    cmd_copy_mp4 = [
        config.ffmpeg_binary,
        "-y",
        "-i",
        str(video_source),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        str(merged_mp4),
    ]
    proc = subprocess.run(cmd_copy_mp4, capture_output=True, text=True)
    if (
        proc.returncode == 0
        and merged_mp4.exists()
        and _media_has_audio_stream(merged_mp4) is True
        and _media_has_video_stream(merged_mp4) is True
        and _decode_smoke_test(config.ffmpeg_binary, merged_mp4)
    ):
        return merged_mp4

    # 2) MP4 with audio transcode fallback (keeps video stream).
    cmd_aac_mp4 = [
        config.ffmpeg_binary,
        "-y",
        "-i",
        str(video_source),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(merged_mp4),
    ]
    proc = subprocess.run(cmd_aac_mp4, capture_output=True, text=True)
    if (
        proc.returncode == 0
        and merged_mp4.exists()
        and _media_has_audio_stream(merged_mp4) is True
        and _media_has_video_stream(merged_mp4) is True
        and _decode_smoke_test(config.ffmpeg_binary, merged_mp4)
    ):
        return merged_mp4

    # 3) Last resort: MKV stream-copy remux.
    cmd_copy_mkv = [
        config.ffmpeg_binary,
        "-y",
        "-i",
        str(video_source),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        str(merged_mkv),
    ]
    run_cmd(cmd_copy_mkv, on_process=on_process, should_terminate=should_terminate)
    if (
        merged_mkv.exists()
        and _media_has_audio_stream(merged_mkv) is True
        and _media_has_video_stream(merged_mkv) is True
        and _decode_smoke_test(config.ffmpeg_binary, merged_mkv)
    ):
        return merged_mkv

    # 4) compatibility transcode fallback for difficult codecs/containers.
    compat_mp4 = config.media_dir / f"{video_id}.playback.mp4"
    cmd_compat = [
        config.ffmpeg_binary,
        "-y",
        "-i",
        str(video_source),
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(compat_mp4),
    ]
    run_cmd(cmd_compat, on_process=on_process, should_terminate=should_terminate)
    if (
        compat_mp4.exists()
        and _media_has_audio_stream(compat_mp4) is True
        and _media_has_video_stream(compat_mp4) is True
        and _decode_smoke_test(config.ffmpeg_binary, compat_mp4)
    ):
        return compat_mp4
    return None

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .pipeline import PipelineError


def _format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def build_fzf_lines(segments: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for idx, seg in enumerate(segments):
        text = str(seg.get("text", "")).strip().replace("\n", " ")
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        line = (
            f"{start:.3f}\t"
            f"#{idx:05d}\t"
            f"{_format_hms(start)}-{_format_hms(end)}\t"
            f"{text}"
        )
        lines.append(line)
    if not lines:
        raise PipelineError("No transcript segments with text were found")
    return lines


def pick_segment_with_fzf(
    segments: list[dict[str, Any]],
    *,
    fzf_bin: str = "fzf",
    initial_query: str | None = None,
) -> dict[str, Any] | None:
    if shutil.which(fzf_bin) is None:
        raise PipelineError(f"fzf binary not found: {fzf_bin}")

    lines = build_fzf_lines(segments)
    cmd = [
        fzf_bin,
        "--ansi",
        "--delimiter",
        "\t",
        "--with-nth",
        "2,3,4",
        "--layout",
        "reverse",
        "--prompt",
        "segment> ",
        "--height",
        "80%",
    ]
    if initial_query:
        cmd.extend(["--query", initial_query])

    proc = subprocess.run(cmd, input="\n".join(lines), text=True, capture_output=True)
    if proc.returncode == 130:
        return None
    if proc.returncode != 0:
        raise PipelineError(f"fzf failed with code {proc.returncode}: {proc.stderr.strip()}")

    selected = proc.stdout.strip()
    if not selected:
        return None

    fields = selected.split("\t", 3)
    if not fields:
        return None
    try:
        start_sec = float(fields[0])
    except ValueError as exc:
        raise PipelineError(f"Unable to parse selected segment time: {selected}") from exc

    return {"start_sec": start_sec, "raw_line": selected}


def launch_vlc_at_time(media_path: Path, start_sec: float, *, vlc_bin: str = "vlc") -> int:
    if shutil.which(vlc_bin) is None:
        raise PipelineError(f"vlc binary not found: {vlc_bin}")
    if not media_path.exists():
        raise PipelineError(f"media file not found: {media_path}")

    cmd = [
        vlc_bin,
        f"--start-time={max(0.0, start_sec):.3f}",
        str(media_path),
    ]
    proc = subprocess.Popen(cmd)
    return int(proc.pid)


def pick_db_match_with_fzf(
    matches: list[dict[str, Any]],
    *,
    fzf_bin: str = "fzf",
    initial_query: str | None = None,
) -> dict[str, Any] | None:
    if shutil.which(fzf_bin) is None:
        raise PipelineError(f"fzf binary not found: {fzf_bin}")
    if not matches:
        return None

    lines: list[str] = []
    for i, row in enumerate(matches):
        start_ms = int(row.get("start_ms", 0) or 0)
        start_sec = max(0.0, start_ms / 1000.0)
        title = str(row.get("title") or row.get("video_id") or "untitled").replace("\n", " ").strip()
        if len(title) > 90:
            title = title[:87] + "..."
        caption_full = str(row.get("text") or "").replace("\n", " ").strip()
        caption_short = caption_full if len(caption_full) <= 140 else caption_full[:137] + "..."
        # Keep full caption in a hidden field for reliable searching; show short caption in main picker.
        lines.append(f"{i}\t{title}\t{_format_hms(start_sec)}\t{caption_full}\t{caption_short}")

    cmd = [
        fzf_bin,
        "--delimiter",
        "\t",
        "--with-nth",
        "2,3,5",
        "--layout",
        "reverse",
        "--prompt",
        "db> ",
        "--height",
        "80%",
        "--no-hscroll",
        "--preview",
        "echo {4}",
        "--preview-window",
        "down,35%,wrap",
    ]
    if initial_query:
        cmd.extend(["--query", initial_query])

    proc = subprocess.run(cmd, input="\n".join(lines), text=True, capture_output=True)
    if proc.returncode == 130:
        return None
    if proc.returncode != 0:
        raise PipelineError(f"fzf failed with code {proc.returncode}: {proc.stderr.strip()}")

    selected = proc.stdout.strip()
    if not selected:
        return None
    first = selected.split("\t", 1)[0]
    try:
        idx = int(first)
    except ValueError as exc:
        raise PipelineError("Failed to parse selected database row from fzf output") from exc
    if idx < 0 or idx >= len(matches):
        raise PipelineError("Selected database row index is out of range")
    return matches[idx]

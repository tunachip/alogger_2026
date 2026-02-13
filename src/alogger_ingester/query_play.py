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

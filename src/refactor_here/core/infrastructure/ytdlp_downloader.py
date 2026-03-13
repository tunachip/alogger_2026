from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..entities import (
    DownloadBatchResult,
    DownloadDefaults,
    DownloadRequest,
    DownloadedMedia
)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")

class YtDlpDownloader:
    def download(
        self,
        request: DownloadRequest,
        defaults: DownloadDefaults,
    ) -> DownloadBatchResult:
        result = DownloadBatchResult()
        defaults.output_dir.mkdir(parents=True, exist_ok=True)
        for url in request.urls:
            cmd = self._build_cmd(url=url, defaults=defaults)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                reason = (proc.stderr or proc.stdout or 'yt-dlp failed').strip()
                result.failed.append((url, reason))
                continue
            video_id, media_path = self._parse_stdout(proc.stdout, defaults.output_dir)
            if not video_id or not media_path:
                result.failed.append((url, 'unable to parse yt-dlp output'))
                continue
            if not media_path.exists():
                result.failed.append((url, f'downloaded file missing: {media_path}'))
                continue
            metadata_path = defaults.output_dir / f"{video_id}.info.json"
            result.items.append(
                DownloadedMedia(
                    url=url,
                    video_id=video_id,
                    media_path=media_path,
                    metadata_path = metadata_path if metadata_path.exists() else None,
                )
            )
        return result

    def _build_cmd(self, *, url: str, defaults: DownloadDefaults) -> list[str]:
        cmd = [
            defaults.yt_dlp_bin,
            "--no-warnings",
            "--newline",
            "--no-progress",
            "--ffmpeg-location",
            defaults.ffmpeg_bin,
            "-S",
            defaults.sort_selector,
            "-f",
            defaults.format_selector,
            "--merge-output-format",
            defaults.merge_output_format,
            "--retries",
            str(defaults.retries),
            "-o",
            str(defaults.output_dir / "%(id)s.%(ext)s"),
            "--print",
            "id",
            "--print",
            "after_move:%(filepath)s",
        ]
        if defaults.write_info_json:
            cmd.append("--write-info-json")
        if defaults.write_thumbnail:
            cmd.append("--write-thumbnail")
        cmd.append(url)
        return cmd
    
    def _parse_stdout(self, strout: str, output_dir: Path) -> tuple[str | None, Path | None]:
        video_id: str | None = None
        media_path: Path | None = None
        for raw in strout.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("after_move:"):
                media_path = Path(line.removeprefix("after_move:")).expanduser()
                continue
            if _ID_RE.match(line):
                video_id = line
        if media_path is None and video_id:
            candidates = sorted(p for p in output_dir.glob(f"{video_id}*") if p.is_file())
            media_path = candidates[0] if candidates else None
        return video_id, media_path


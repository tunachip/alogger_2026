from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..entities import (
    TranscribeBatchResult,
    TranscribeDefaults,
    TranscribeRequest,
    TranscribedVideo,
)

class WhisperTranscriber:
    def transcribe(
        self,
        request: TranscribeRequest,
        defaults: TranscribeDefaults,
    ) -> TranscribeBatchResult:
        result = TranscribeBatchResult()
        defaults.output_dir.mkdir(parents=True, exist_ok=True)
        for item in request.media:
            per_video_dir = defaults.output_dir / item.video_id
            per_video_dir.mkdir(parents=True, exist_ok=True)
            cmd = self._build_cmd(
                media_path=item.media_path,
                defaults=defaults,
                output_dir=per_video_dir
            )
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                reason = (proc.stderr or proc.stdout or 'whisper failed').strip()
                result.failed.append((item.video_id, reason))
                continue
            transcript_json = per_video_dir / f"{item.media_path.stem}.json"
            if not transcript_json.exists():
                result.failed.append((item.video_id, f"transcript missing: {transcript_json}"))
                continue
            segment_count = self._count_segments(transcript_json)
            result.items.append(
                TranscribedVideo(
                    video_id=item.video_id,
                    media_path=item.media_path,
                    transcript_json_path=transcript_json,
                    segment_count=segment_count,
                )
            )
        return result

    def _build_cmd(
        self,
        *,
        media_path: Path,
        defaults: TranscribeDefaults,
        output_dir: Path,
    ) -> list[str]:
        cmd = [
            defaults.whisper_bin,
            str(media_path),
            "--model",
            defaults.model,
            "--language",
            defaults.language,
            "--output-dir",
            str(output_dir),
            "--output-format",
            defaults.output_format,
            "--fp16",
            "False",
        ]
        if defaults.model_dir is not None:
            cmd.append("--model-dir")
            cmd.append(str(defaults.model_dir))
        return cmd

    def _count_segments(self, transcript_json: Path) -> int:
        payload = json.loads(transcript_json.read_text(encoding="utf-8"))
        segments = payload.get("segments", [])
        return len(segments) if isinstance(segments, list) else 0


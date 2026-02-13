from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class IngesterConfig:
    db_path: Path = Path("./data/alogger.db")
    media_dir: Path = Path("./data/media")
    transcript_dir: Path = Path("./data/transcripts")
    whisper_model: str = "base"
    whisper_model_dir: Path = Path("./data/whisper_models")
    whisper_language: str = "en"
    whisper_binary: str = "whisper"
    yt_dlp_binary: str = "yt-dlp"
    ffmpeg_binary: str = "ffmpeg"
    worker_count: int = 2
    poll_interval_sec: float = 1.0
    webhook_url: str | None = None

    @classmethod
    def from_env(cls) -> "IngesterConfig":
        return cls(
            db_path=Path(os.getenv("ALOGGER_DB_PATH", "./data/alogger.db")),
            media_dir=Path(os.getenv("ALOGGER_MEDIA_DIR", "./data/media")),
            transcript_dir=Path(os.getenv("ALOGGER_TRANSCRIPT_DIR", "./data/transcripts")),
            whisper_model=os.getenv("ALOGGER_WHISPER_MODEL", "base"),
            whisper_model_dir=Path(os.getenv("ALOGGER_WHISPER_MODEL_DIR", "./data/whisper_models")),
            whisper_language=os.getenv("ALOGGER_WHISPER_LANGUAGE", "en"),
            whisper_binary=os.getenv("ALOGGER_WHISPER_BIN", "whisper"),
            yt_dlp_binary=os.getenv("ALOGGER_YTDLP_BIN", "yt-dlp"),
            ffmpeg_binary=os.getenv("ALOGGER_FFMPEG_BIN", "ffmpeg"),
            worker_count=int(os.getenv("ALOGGER_WORKER_COUNT", "2")),
            poll_interval_sec=float(os.getenv("ALOGGER_POLL_INTERVAL_SEC", "1.0")),
            webhook_url=os.getenv("ALOGGER_WEBHOOK_URL"),
        )

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.whisper_model_dir.mkdir(parents=True, exist_ok=True)

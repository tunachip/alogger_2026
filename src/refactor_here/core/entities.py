from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# === Downloading ===

@dataclass(frozen=True, slots=True)
class DownloadDefaults:
    output_dir: Path
    format_selector: str = "bestvideo*+bestaudio/best"
    sort_selector: str = "res:1080,fps"
    merge_output_format: str = "mp4"
    write_info_json: bool = True
    write_thumbnail: bool = False
    retries: int = 3
    yt_dlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"

@dataclass(frozen=True, slots=True)
class DownloadRequest:
    urls: Sequence[str]
    output_dir: Path | None = None
    format_selector: str | None = None
    sort_selector: str | None = None
    merge_output_format: str | None = None
    write_info_json: bool | None = None
    write_thumbnail: bool | None = None
    retries: int | None = None

@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    url: str
    video_id: str
    media_path: Path
    metadata_path: Path | None = None

@dataclass(frozen=True, slots=True)
class DownloadBatchResult:
    items: list[DownloadedMedia] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

# === Transcribing ===

@dataclass(frozen=True, slots=True)
class TranscribeDefaults:
    whisper_bin: str = "whisper"
    model: str = "base"
    language: str = "en"
    model_dir: Path | None = None
    output_dir: Path = Path("./data/transcripts")
    output_format: str = "json"

@dataclass(frozen=True, slots=True)
class TranscribeRequest:
    media: Sequence[DownloadedMedia]
    model: str | None = None
    language: str | None = None
    model_dir: Path | None = None
    output_dir: Path | None = None

@dataclass(frozen=True, slots=True)
class TranscribedVideo:
    video_id: str
    media_path: Path
    transcript_json_path: Path
    segment_count: int

@dataclass(frozen=True, slots=True)
class TranscribeBatchResult:
    items: list[TranscribedVideo] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


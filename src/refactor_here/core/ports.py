from typing import Protocol
from .entities import (
    DownloadRequest,
    DownloadBatchResult,
    DownloadDefaults,
    TranscribeDefaults,
    TranscribeRequest,
    TranscribeBatchResult
)

class Downloader(Protocol):
    def download(
        self,
        request: DownloadRequest,
        defaults: DownloadDefaults
    ) -> DownloadBatchResult:
        ...

class Transcriber(Protocol):
    def transcribe(
        self,
        request: TranscribeRequest,
        defaults: TranscribeDefaults,
    ) -> TranscribeBatchResult:
        ...

class MediaCatalog(Protocol):
    def init_schema(self) -> None: ...

    def create_from_url(
        self,
        *,
        video_url: str,
        canonical_id: str | None = None,
        video_title: str | None = None,
        video_creator: str | None = None,
        video_upload_date: str | None = None,
        metadata: dict | None = None,
        queue_download: bool = True,
        queue_transcribe: bool = True,
    ) -> int:
        ...

    def create_from_local(
        self,
        *,
        video_path: str,
        canonical_id: str | None = None,
        video_title: str | None = None,
        video_creator: str | None = None,
        video_upload_date: str | None = None,
        metadata: dict | None = None,
        queue_transcribe: bool = True,
    ) -> int:
        ...

    def mark_download_done(self, record_id: int, *, video_path: str) -> None: ...
    def mark_download_failed(self, record_id: int, *, error_text: str) -> None: ...
    def mark_download_skipped(self, record_id: int, *, reason: str | None = None) -> None: ...
    def mark_transcribe_done(self, record_id: int, *, transcript_path: str) -> None: ...
    def mark_transcribe_failed(self, record_id: int, *, error_text: str) -> None: ...
    def mark_transcribe_skipped(self, record_id: int, *, reason: str | None = None) -> None: ...
    def get_media_locator(self, record_id: int) -> dict[str, str | int | None] | None: ...
    def get_media_locator_by_input(self, value: str) -> dict[str, str | int | None] | None: ...
    def replace_transcript_segments(self, record_id: int, transcript_json_path: str) -> int: ...
    def search_transcript_text(
        self,
        query: str,
        *,
        limit: int = 100,
        record_id: int | None = None,
    ) -> list: ...
    def record_usage(
        self,
        record_id: int,
        *,
        actor: str = "program",
        action: str = "open",
        source: str | None = None,
    ) -> None: ...
    def top_videos_by_usage(
        self,
        *,
        limit: int = 25,
        action: str | None = None,
        since_iso: str | None = None,
    ) -> list: ...

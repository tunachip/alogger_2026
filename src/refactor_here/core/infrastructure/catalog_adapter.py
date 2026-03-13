from __future__ import annotations

from pathlib import Path
from typing import Any

from .sqlite_media_catalog import SQLiteMediaCatalog

class SQLiteCatalogAdapter:
    def __init__(self, db_path: Path) -> None:
        self._catalog = SQLiteMediaCatalog(db_path=db_path)

    def init_schema(self) -> None:
        self._catalog.init_schema()

    def create_from_url(
        self,
        *,
        video_url: str,
        canonical_id: str | None = None,
        video_title: str | None = None,
        video_creator: str | None = None,
        video_upload_date: str | None = None,
        metadata: dict[str, Any] | None = None,
        queue_download: bool = True,
        queue_transcribe: bool = True,
    ) -> int:
        return self._catalog.create_from_url(
            video_url=video_url,
            canonical_id=canonical_id,
            video_title=video_title,
            video_creator=video_creator,
            video_upload_date=video_upload_date,
            metadata=metadata,
            queue_download=queue_download,
            queue_transcribe=queue_transcribe,
        )

    def create_from_local(
        self,
        *,
        video_path: str,
        canonical_id: str | None = None,
        video_title: str | None = None,
        video_creator: str | None = None,
        video_upload_date: str | None = None,
        metadata: dict[str, Any] | None = None,
        queue_transcribe: bool = True,
    ) -> int:
        return self._catalog.create_from_local(
            video_path=video_path,
            canonical_id=canonical_id,
            video_title=video_title,
            video_creator=video_creator,
            video_upload_date=video_upload_date,
            metadata=metadata,
            queue_transcribe=queue_transcribe,
        )

    def mark_download_done(self, record_id: int, *, video_path: str) -> None:
        self._catalog.mark_download_done(record_id, video_path=video_path)

    def mark_download_failed(self, record_id: int, *, error_text: str) -> None:
        self._catalog.mark_download_failed(record_id, error_text=error_text)

    def mark_download_skipped(self, record_id: int, *, reason: str | None = None) -> None:
        self._catalog.mark_download_skipped(record_id, reason=reason)

    def mark_transcribe_done(self, record_id: int, *, transcript_path: str) -> None:
        self._catalog.mark_transcribe_done(record_id, transcript_path=transcript_path)

    def mark_transcribe_failed(self, record_id: int, *, error_text: str) -> None:
        self._catalog.mark_transcribe_failed(record_id, error_text=error_text)

    def mark_transcribe_skipped(self, record_id: int, *, reason: str | None = None) -> None:
        self._catalog.mark_transcribe_skipped(record_id, reason=reason)

    def get_media_locator(self, record_id: int) -> dict[str, str | int | None] | None:
        return self._catalog.get_media_locator(record_id)

    def get_media_locator_by_input(self, value: str) -> dict[str, str | int | None] | None:
        return self._catalog.get_media_locator_by_input(value)

    def replace_transcript_segments(self, record_id: int, transcript_json_path: str) -> int:
        return self._catalog.replace_transcript_segments(record_id, transcript_json_path)

    def search_transcript_text(
        self,
        query: str,
        *,
        limit: int = 100,
        record_id: int | None = None,
    ) -> list:
        return self._catalog.search_transcript_text(
            query,
            limit=limit,
            record_id=record_id,
        )

    def record_usage(
        self,
        record_id: int,
        *,
        actor: str = "program",
        action: str = "open",
        source: str | None = None,
    ) -> None:
        self._catalog.record_usage(
            record_id,
            actor=actor,
            action=action,
            source=source,
        )

    def top_videos_by_usage(
        self,
        *,
        limit: int = 25,
        action: str | None = None,
        since_iso: str | None = None,
    ) -> list:
        return self._catalog.top_videos_by_usage(
            limit=limit,
            action=action,
            since_iso=since_iso,
        )


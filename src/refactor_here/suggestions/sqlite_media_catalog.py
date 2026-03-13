from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

class IngestSource(StrEnum):
    YOUTUBE = "youtube"
    LOCAL   = "local"

class DownloadState(StrEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED  = "queued"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    SKIPPED = "skipped"

class TranscribeState(StrEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED  = "queued"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    SKIPPED = "skipped"

INSERT_TEMPLATE = """
    INSERT INTO media_catalog(
        ingest_source,
        video_url,
        video_path,
        download_state,
        transcribe_state,
        video_title,
        video_creator,
        video_upload_date,
        canonical_id,
        metadata_json,
        created_at,
        updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

@dataclass(slots=True)
class MediaRecord:
    id: int
    ingest_source: IngestSource
    video_url: str | None
    download_state: DownloadState
    transcribe_state: TranscribeState
    video_path: str | None
    transcript_path: str | None
    video_title: str | None
    video_creator: str | None
    video_upload_date: str | None
    canonical_id: str | None
    metadata: dict[str, Any]
    last_error: str | None
    created_at: str
    updated_at: str

class SQLiteMediaCatalog:
    """
    Suggested flexible catalog layer.

    Supports two ingest entrypoints:
    - YouTube URL ingest (download optional)
    - Local-file ingest (download bypassed)

    Stores the fields requested by you directly on one record:
    video_url, download_state, transcribe_state, video_path, transcript_path,
    video_title, video_creator, video_upload_date.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ingest_source TEXT NOT NULL CHECK(ingest_source IN ('youtube', 'local')),
                    video_url TEXT,
                    download_state TEXT NOT NULL CHECK(download_state IN (
                        'not_requested','queued','running','done','failed','skipped'
                    )),
                    transcribe_state TEXT NOT NULL CHECK(transcribe_state IN (
                        'not_requested','queued','running','done','failed','skipped'
                    )),
                    video_path TEXT,
                    transcript_path TEXT,
                    video_title TEXT,
                    video_creator TEXT,
                    video_upload_date TEXT,
                    canonical_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(video_url IS NOT NULL OR video_path IS NOT NULL)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_media_catalog_canonical
                ON media_catalog(canonical_id)
                WHERE canonical_id IS NOT NULL;

                CREATE INDEX IF NOT EXISTS idx_media_catalog_url
                ON media_catalog(video_url);

                CREATE INDEX IF NOT EXISTS idx_media_catalog_video_path
                ON media_catalog(video_path);

                CREATE INDEX IF NOT EXISTS idx_media_catalog_states
                ON media_catalog(download_state, transcribe_state, updated_at);

                CREATE INDEX IF NOT EXISTS idx_media_catalog_creator
                ON media_catalog(video_creator);

                CREATE INDEX IF NOT EXISTS idx_media_catalog_upload_date
                ON media_catalog(video_upload_date);
                """
            )

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
        now = _utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                INSERT_TEMPLATE,
                (
                    IngestSource.YOUTUBE.value,
                    video_url,
                    None,
                    (
                        DownloadState.QUEUED.value
                        if queue_download
                        else DownloadState.NOT_REQUESTED.value),
                    (
                        TranscribeState.QUEUED.value
                        if queue_transcribe
                        else TranscribeState.NOT_REQUESTED.value),
                    video_title,
                    video_creator,
                    video_upload_date,
                    canonical_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

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
        now = _utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                INSERT_TEMPLATE,
                (
                    IngestSource.LOCAL.value,
                    None,
                    video_path,
                    DownloadState.NOT_REQUESTED.value,
                    (
                        TranscribeState.QUEUED.value
                        if queue_transcribe
                        else TranscribeState.NOT_REQUESTED.value),
                    video_title,
                    video_creator,
                    video_upload_date,
                    canonical_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def mark_download_running(self, record_id: int) -> None:
        self._update_states(record_id, download_state=DownloadState.RUNNING)

    def mark_download_done(self, record_id: int, *, video_path: str) -> None:
        self._update_record(
            record_id,
            download_state=DownloadState.DONE.value,
            video_path=video_path,
            last_error=None,
            allow_none_fields={"last_error"},
        )

    def mark_download_skipped(self, record_id: int, *, reason: str | None = None) -> None:
        self._update_record(
            record_id,
            download_state=DownloadState.SKIPPED.value,
            last_error=reason,
        )

    def mark_download_failed(self, record_id: int, *, error_text: str) -> None:
        self._update_record(
            record_id,
            download_state=DownloadState.FAILED.value,
            last_error=error_text,
        )

    def mark_transcribe_running(self, record_id: int) -> None:
        self._update_states(record_id, transcribe_state=TranscribeState.RUNNING)

    def mark_transcribe_done(self, record_id: int, *, transcript_path: str) -> None:
        self._update_record(
            record_id,
            transcribe_state=TranscribeState.DONE.value,
            transcript_path=transcript_path,
            last_error=None,
            allow_none_fields={"last_error"},
        )

    def mark_transcribe_skipped(self, record_id: int, *, reason: str | None = None) -> None:
        self._update_record(
            record_id,
            transcribe_state=TranscribeState.SKIPPED.value,
            last_error=reason,
        )

    def mark_transcribe_failed(self, record_id: int, *, error_text: str) -> None:
        self._update_record(
            record_id,
            transcribe_state=TranscribeState.FAILED.value,
            last_error=error_text,
        )

    def update_metadata(
        self,
        record_id: int,
        *,
        video_title: str | None = None,
        video_creator: str | None = None,
        video_upload_date: str | None = None,
        video_url: str | None = None,
        canonical_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._update_record(
            record_id,
            video_title=video_title,
            video_creator=video_creator,
            video_upload_date=video_upload_date,
            video_url=video_url,
            canonical_id=canonical_id,
            metadata_json=(
                json.dumps(metadata, ensure_ascii=False)
                if metadata is not None
                else None),
        )

    def get(self, record_id: int) -> MediaRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_catalog WHERE id=? LIMIT 1",
                (record_id,),
            ).fetchone()
        return _row_to_media_record(row) if row else None

    def find_by_input(self, value: str) -> MediaRecord | None:
        """
        Flexible lookup for user/program/agent:
        - URL input => match by video_url
        - path-like input => match by video_path
        - otherwise => match by canonical_id
        """
        with self.connect() as conn:
            def _query(where: str) -> str:
                return f"""
                SELECT *
                FROM media_catalog
                WHERE {where}=?
                ORDER BY updated_at DESC
                LIMIT 1
                """
            where = "canonical_id"
            if _looks_like_url(value):
                where = "video_url"
            elif _looks_like_path(value):
                where = "video_path"
            row = conn.execute(_query(where), (value,)).fetchone()
        return _row_to_media_record(row) if row else None

    def list_recent(self, limit: int = 50) -> list[MediaRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM media_catalog
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_media_record(row) for row in rows]

    def query(
        self,
        *,
        text: str | None = None,
        download_state: DownloadState | None = None,
        transcribe_state: TranscribeState | None = None,
        limit: int = 100,
    ) -> list[MediaRecord]:
        where: list[str] = []
        args: list[Any] = []

        if text:
            where.append(
                """
                (video_title LIKE ?
                OR video_creator LIKE ?
                OR video_url LIKE ?
                OR video_path LIKE ?)
                """
            )
            needle = f"%{text}%"
            args.extend([needle, needle, needle, needle])

        if download_state is not None:
            where.append("download_state=?")
            args.append(download_state.value)

        if transcribe_state is not None:
            where.append("transcribe_state=?")
            args.append(transcribe_state.value)

        sql = "SELECT * FROM media_catalog"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_media_record(row) for row in rows]

    def _update_states(
        self,
        record_id: int,
        *,
        download_state: DownloadState | None = None,
        transcribe_state: TranscribeState | None = None,
    ) -> None:
        self._update_record(
            record_id,
            download_state = (
                download_state.value
                if download_state is not None
                else None),
            transcribe_state = (
                transcribe_state.value
                if transcribe_state is not None
                else None),
        )

    def _update_record(
        self,
        record_id: int,
        *,
        allow_none_fields: set[str] | None = None,
        **fields: object,
    ) -> None:
        allowed_nulls = allow_none_fields or set()
        filtered = {
            k: v for k, v in fields.items()
            if v is not None or k in allowed_nulls
        }
        if not filtered:
            return
        filtered["updated_at"] = _utc_now_iso()
        columns = ", ".join(f"{name}=?" for name in filtered)
        params = list(filtered.values()) + [record_id]
        with self.connect() as conn:
            conn.execute(f"UPDATE media_catalog SET {columns} WHERE id=?", params)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _looks_like_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)

def _looks_like_path(value: str) -> bool:
    return ("/" in value or
            "\\" in value or
            value.startswith("."))

def _row_to_media_record(row: sqlite3.Row) -> MediaRecord:
    raw_metadata = row["metadata_json"]
    try:
        metadata = (
            json.loads(raw_metadata)
            if raw_metadata
            else {})
    except json.JSONDecodeError:
        metadata = {}

    return MediaRecord(
        id = int(row["id"]),
        ingest_source = IngestSource(str(row["ingest_source"])),
        video_url = (
            str(row["video_url"])
            if row["video_url"] is not None
            else None),
        download_state = DownloadState(str(row["download_state"])),
        transcribe_state = TranscribeState(str(row["transcribe_state"])),
        video_path = (
            str(row["video_path"])
            if row["video_path"] is not None
            else None),
        transcript_path = (
            str(row["transcript_path"])
            if row["transcript_path"] is not None
            else None),
        video_title = (
            str(row["video_title"])
            if row["video_title"] is not None
            else None),
        video_creator = (
            str(row["video_creator"])
            if row["video_creator"] is not None
            else None),
        video_upload_date = (
            str(row["video_upload_date"])
            if row["video_upload_date"] is not None
            else None),
        canonical_id = (
            str(row["canonical_id"])
            if row["canonical_id"] is not None
            else None),
        metadata = metadata,
        last_error = (
            str(row["last_error"])
            if row["last_error"] is not None
            else None),
        created_at = str(row["created_at"]),
        updated_at = str(row["updated_at"]),
    )

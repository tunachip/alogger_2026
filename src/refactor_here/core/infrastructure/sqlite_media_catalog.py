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


@dataclass(slots=True)
class TranscriptMatch:
    media_id: int
    segment_index: int
    start_ms: int
    end_ms: int
    text: str
    video_url: str | None
    video_path: str | None
    video_title: str | None
    video_creator: str | None


@dataclass(slots=True)
class UsageRank:
    media_id: int
    video_url: str | None
    video_path: str | None
    video_title: str | None
    video_creator: str | None
    usage_count: int
    last_used_at: str | None


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

                CREATE TABLE IF NOT EXISTS transcript_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL,
                    segment_index INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY(media_id) REFERENCES media_catalog(id) ON DELETE CASCADE,
                    UNIQUE(media_id, segment_index)
                );

                CREATE INDEX IF NOT EXISTS idx_transcript_segments_media_time
                ON transcript_segments(media_id, start_ms);

                CREATE VIRTUAL TABLE IF NOT EXISTS transcript_segments_fts
                USING fts5(text, content='transcript_segments', content_rowid='id');

                CREATE TRIGGER IF NOT EXISTS transcript_segments_ai
                AFTER INSERT ON transcript_segments BEGIN
                    INSERT INTO transcript_segments_fts(rowid, text)
                    VALUES (new.id, new.text);
                END;

                CREATE TRIGGER IF NOT EXISTS transcript_segments_ad
                AFTER DELETE ON transcript_segments BEGIN
                    INSERT INTO transcript_segments_fts(transcript_segments_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                END;

                CREATE TRIGGER IF NOT EXISTS transcript_segments_au
                AFTER UPDATE ON transcript_segments BEGIN
                    INSERT INTO transcript_segments_fts(transcript_segments_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                    INSERT INTO transcript_segments_fts(rowid, text)
                    VALUES (new.id, new.text);
                END;

                CREATE TABLE IF NOT EXISTS media_usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'program',
                    action TEXT NOT NULL DEFAULT 'open',
                    source TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(media_id) REFERENCES media_catalog(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_media_usage_media_time
                ON media_usage_events(media_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_media_usage_action
                ON media_usage_events(action, created_at);
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
                        else TranscribeState.NOT_REQUESTED.value
                    ),
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
                else None
            ),
        )

    def get(self, record_id: int) -> MediaRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_catalog WHERE id=? LIMIT 1",
                (record_id,),
            ).fetchone()
        return _row_to_media_record(row) if row else None

    def find_by_input(self, value: str) -> MediaRecord | None:
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

    def get_media_locator(self, record_id: int) -> dict[str, str | int | None] | None:
        record = self.get(record_id)
        if record is None:
            return None
        return {
            "id": record.id,
            "canonical_id": record.canonical_id,
            "video_url": record.video_url,
            "video_path": record.video_path,
            "transcript_path": record.transcript_path,
            "video_title": record.video_title,
            "video_creator": record.video_creator,
        }

    def get_media_locator_by_input(self, value: str) -> dict[str, str | int | None] | None:
        record = self.find_by_input(value)
        if record is None:
            return None
        return {
            "id": record.id,
            "canonical_id": record.canonical_id,
            "video_url": record.video_url,
            "video_path": record.video_path,
            "transcript_path": record.transcript_path,
            "video_title": record.video_title,
            "video_creator": record.video_creator,
        }

    def replace_transcript_segments(self, record_id: int, transcript_json_path: str | Path) -> int:
        transcript_path = Path(transcript_json_path)
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        raw_segments = payload.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ValueError("Transcript JSON missing valid 'segments' list")

        rows: list[tuple[int, int, int, str]] = []
        for i, seg in enumerate(raw_segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip().replace("\n", " ")
            if not text:
                continue
            start_sec = float(seg.get("start", 0.0))
            end_sec = float(seg.get("end", start_sec))
            rows.append(
                (
                    i,
                    max(0, int(start_sec * 1000.0)),
                    max(0, int(end_sec * 1000.0)),
                    text,
                )
            )

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM transcript_segments WHERE media_id=?",
                (record_id,),
            )
            conn.executemany(
                """
                INSERT INTO transcript_segments(media_id, segment_index, start_ms, end_ms, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(record_id, idx, start_ms, end_ms, text) for idx, start_ms, end_ms, text in rows],
            )
            conn.execute("COMMIT")

        return len(rows)

    def search_transcript_text(
        self,
        query: str,
        *,
        limit: int = 100,
        record_id: int | None = None,
    ) -> list[TranscriptMatch]:
        where: list[str] = []
        args: list[Any] = []
        needle = f"%{query}%"
        where.append("ts.text LIKE ?")
        args.append(needle)
        if record_id is not None:
            where.append("ts.media_id=?")
            args.append(record_id)

        sql = """
            SELECT
                ts.media_id,
                ts.segment_index,
                ts.start_ms,
                ts.end_ms,
                ts.text,
                mc.video_url,
                mc.video_path,
                mc.video_title,
                mc.video_creator
            FROM transcript_segments ts
            JOIN media_catalog mc ON mc.id = ts.media_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts.media_id ASC, ts.start_ms ASC LIMIT ?"
        args.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()

        return [
            TranscriptMatch(
                media_id=int(row["media_id"]),
                segment_index=int(row["segment_index"]),
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                text=str(row["text"]),
                video_url=str(row["video_url"]) if row["video_url"] is not None else None,
                video_path=str(row["video_path"]) if row["video_path"] is not None else None,
                video_title=str(row["video_title"]) if row["video_title"] is not None else None,
                video_creator=str(row["video_creator"]) if row["video_creator"] is not None else None,
            )
            for row in rows
        ]

    def record_usage(
        self,
        record_id: int,
        *,
        actor: str = "program",
        action: str = "open",
        source: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO media_usage_events(media_id, actor, action, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, actor, action, source, _utc_now_iso()),
            )

    def top_videos_by_usage(
        self,
        *,
        limit: int = 25,
        action: str | None = None,
        since_iso: str | None = None,
    ) -> list[UsageRank]:
        where: list[str] = []
        args: list[Any] = []
        if action:
            where.append("u.action=?")
            args.append(action)
        if since_iso:
            where.append("u.created_at >= ?")
            args.append(since_iso)

        sql = """
            SELECT
                mc.id AS media_id,
                mc.video_url,
                mc.video_path,
                mc.video_title,
                mc.video_creator,
                COUNT(u.id) AS usage_count,
                MAX(u.created_at) AS last_used_at
            FROM media_catalog mc
            LEFT JOIN media_usage_events u ON u.media_id = mc.id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += """
            GROUP BY mc.id
            ORDER BY usage_count DESC, last_used_at DESC
            LIMIT ?
        """
        args.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()

        return [
            UsageRank(
                media_id=int(row["media_id"]),
                video_url=str(row["video_url"]) if row["video_url"] is not None else None,
                video_path=str(row["video_path"]) if row["video_path"] is not None else None,
                video_title=str(row["video_title"]) if row["video_title"] is not None else None,
                video_creator=str(row["video_creator"]) if row["video_creator"] is not None else None,
                usage_count=int(row["usage_count"] or 0),
                last_used_at=str(row["last_used_at"]) if row["last_used_at"] is not None else None,
            )
            for row in rows
        ]

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

    def _update_record(self, record_id: int, *, allow_none_fields: set[str] | None = None, **fields: object,) -> None:
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
    return (
        "/" in value or
        "\\" in value or
        value.startswith(".")
    )

def _row_to_media_record(row: sqlite3.Row) -> MediaRecord:
    raw_metadata = row["metadata_json"]
    try:
        metadata = (
            json.loads(raw_metadata)
            if raw_metadata
            else {}
        )
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


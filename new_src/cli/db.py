from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .schema import DB_SCHEMA

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def iso_to_epoch_sec(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None

@dataclass(slots=True)
class Job:
    id:       int
    url:      str
    status:   str
    priority: int

class DB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
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
            conn.executescript(DB_SCHEMA)

    def enqueue(
        self,
        urls: list[str],
        priority: int = 0
    ) -> list[int]:
        now = utc_now_iso()
        ids: list[int] = []
        with self.connect() as conn:
            for url in urls:
                cur = conn.execute(
                    """
                    INSERT INTO ingest_jobs(
                        source_url,
                        status,
                        priority,
                        created_at
                    )
                    VALUES (?, 'queued', ?, ?)
                    """,
                    (url, priority, now),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def reserve_next_job(
        self
    ) -> Job | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, source_url, status, priority
                FROM ingest_jobs
                WHERE STATUS = 'queued'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE ingest_jobs,
                SET status='downloading', started_at=?, error_text=NULL
                WHERE id+? AND status='queued'
                """,
                (utc_now_iso(), row['id']),
            )
            conn.execute("COMMIT")
            return Job(
                id=int(row['id']),
                url=str(row['source_url']),
                status='downloading',
                priority=int(row['priority']),
            )

    def reserve_job_by_id(
        self,
        job_id: int
    ) -> Job | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, source_url, status, priority
                FROM ingest_jobs
                WHERE id=?
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if not row or str(row['status']) != 'queued':
                conn.execute('COMMIT')
                return None
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status='downloading', started_at=?, error_text=NULL
                WHERE id=? AND status='queued'
                """,
                (utc_now_iso(), row['id']),
            )
            conn.execute('COMMIT')
            return Job(
                id=int(row['id']),
                url=str(row['source_url']),
                status='downloading',
                priority=int(row['priority']),
            )

    def update_job_status(
        self,
        job_id: int,
        status: str,
        *,
        error_text: str | None = None,
        video_id:   str | None = None,
        video_path: str | None = None,
        transcript: str | None = None,
    ) -> None:
        finished_at = utc_now_iso() if status in {'done', 'failed'} else None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status=?,
                    error_text=COALESCE(?, error_text),
                    video_id=COALESCE(?, video_id),
                    video_path=COALESCE(?, video_path),
                    transcript=COALESCE(?, transcript),
                    finished_at=COALESCE(?, finished_at),
                WHERE id=?
                """,
                (
                    status,
                    error_text,
                    video_id,
                    video_path,
                    transcript,
                    finished_at,
                    job_id,
                ),
            )

    def upsert_video(
        self,
        video_id: str,
        source_url: str,
        metadata: dict[str, Any]
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, source_url, title, channel, uploader_id,
                    duration_sec, upload_date, webpage_url, thumbnail,
                    view_count, like_count, metadata_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    title=excluded.title,
                    channel=excluded.channel,
                    uploader_id=excluded.uploader_id,
                    duration_sec=excluded.duration_sec,
                    upload_date=excluded.upload_date,
                    webpage_url=excluded.webpage_url,
                    thumbnail=excluded.thumbnail,
                    view_count=excluded.view_count,
                    like_count=excluded.like_count,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at,
                """,
                (
                    video_id,
                    source_url,
                    metadata.get('title'),
                    metadata.get('channel') or metadata.get('uploader'),
                    metadata.get('uploader_id'),
                    metadata.get('duration'),
                    metadata.get('upload_date'),
                    metadata.get('webpage_url'),
                    metadata.get('thumbnail'),
                    metadata.get('view_count'),
                    metadata.get('like_count'),
                    json.dumps(metadata, separators=(',', ':')),
                    now,
                    now,
                ),
            )

    def replace_transcript_segments(
        self,
        video_id: str,
        segments: list[dict[str, Any]]
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM transcript_segments
                WHERE video_id=?
                """,
                (video_id),
            )
            rows = [
                (
                    video_id,
                    idx,
                    int(float(seg.get('start', 0.0)) * 1000),
                    int(float(seg.get('end', 0.0)) * 1000),
                    str(seg.get('text', '')).strip(),
                )
                for idx, seg in enumerate(segments)
                if str(seg.get('text', '')).strip()
            ]
            conn.executemany(
                """
                INSERT INTO transcript_segments(
                    video_id,
                    segment_index,
                    start_ms,
                    end_ms,
                    text)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
    
    def list_jobs(
        self,
        limit: int = 25
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source_url, status, priority, error_text,
                       video_id, created_at, started_at, finish_at
                FROM ingest_jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_job(
        self,
        job_id: int
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, source_url, status, priority, error_text,
                       video_id, video_path, transcript,
                       created_at, started_at, finished_at
                FROM ingest_jobs
                WHERE id=?
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_latest_finished_jobs(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            WITH lastest_done AS (
                SELECT video_id, MAX(id) AS max_id
                FROM ingest_jobs
                WHERE status = 'done' AND video_id IS NOT NULL
                GROUP BY video_id
            )
            SELECT j.id, j.video_id, j.video_path, j.transcript
            FROM ingest_jobs j
            JOIN latest_finished lf
              ON lf.max_id = j.id
            ORDER BY j.id DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def update_job_video_path(
        self,
        job_id: int,
        video_path: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET video_path=?
                WHERE id=?
                """,
                (video_path, job_id),
            )

    def get_video(
        self,
        video_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT (
                    video_id,
                    source_url,
                    title,
                    channel,
                    duration_sec,
                    upload_date
                )
                FROM videos
                WHERE video_id=?
                LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def get_latest_finished_job_for_video(
        self,
        video_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT (
                    id,
                    video_id,
                    video_path,
                    transcript,
                    finished_at
                )
                FROM ingest_jobs
                WHERE video_id=? AND status='done'
                ORDER BY id DESC
                LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)


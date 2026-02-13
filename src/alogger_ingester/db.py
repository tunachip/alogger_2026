from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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
    id: int
    url: str
    status: str
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingest_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'queued','downloading','transcribing','done','failed'
                    )),
                    priority INTEGER NOT NULL DEFAULT 0,
                    retries INTEGER NOT NULL DEFAULT 0,
                    error_text TEXT,
                    video_id TEXT,
                    local_video_path TEXT,
                    transcript_json_path TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status_created
                ON ingest_jobs(status, created_at);

                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    title TEXT,
                    channel TEXT,
                    uploader_id TEXT,
                    duration_sec INTEGER,
                    upload_date TEXT,
                    webpage_url TEXT,
                    thumbnail TEXT,
                    view_count INTEGER,
                    like_count INTEGER,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel);
                CREATE INDEX IF NOT EXISTS idx_videos_upload_date ON videos(upload_date);

                CREATE TABLE IF NOT EXISTS transcript_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY(video_id) REFERENCES videos(video_id) ON DELETE CASCADE,
                    UNIQUE(video_id, segment_index)
                );

                CREATE INDEX IF NOT EXISTS idx_transcript_video_time
                ON transcript_segments(video_id, start_ms);

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
                """
            )

    def enqueue(self, urls: list[str], priority: int = 0) -> list[int]:
        now = utc_now_iso()
        ids: list[int] = []
        with self.connect() as conn:
            for url in urls:
                cur = conn.execute(
                    """
                    INSERT INTO ingest_jobs(source_url, status, priority, created_at)
                    VALUES (?, 'queued', ?, ?)
                    """,
                    (url, priority, now),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def reserve_next_job(self) -> Job | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, source_url, status, priority
                FROM ingest_jobs
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status='downloading', started_at=?, error_text=NULL
                WHERE id=? AND status='queued'
                """,
                (utc_now_iso(), row["id"]),
            )
            conn.execute("COMMIT")
            return Job(
                id=int(row["id"]),
                url=str(row["source_url"]),
                status="downloading",
                priority=int(row["priority"]),
            )

    def reserve_job_by_id(self, job_id: int) -> Job | None:
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
            if not row:
                conn.execute("COMMIT")
                return None
            if str(row["status"]) != "queued":
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status='downloading', started_at=?, error_text=NULL
                WHERE id=? AND status='queued'
                """,
                (utc_now_iso(), job_id),
            )
            conn.execute("COMMIT")
            return Job(
                id=int(row["id"]),
                url=str(row["source_url"]),
                status="downloading",
                priority=int(row["priority"]),
            )

    def update_job_status(
        self,
        job_id: int,
        status: str,
        *,
        error_text: str | None = None,
        video_id: str | None = None,
        local_video_path: str | None = None,
        transcript_json_path: str | None = None,
    ) -> None:
        finished_at = utc_now_iso() if status in {"done", "failed"} else None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status=?,
                    error_text=COALESCE(?, error_text),
                    video_id=COALESCE(?, video_id),
                    local_video_path=COALESCE(?, local_video_path),
                    transcript_json_path=COALESCE(?, transcript_json_path),
                    finished_at=COALESCE(?, finished_at)
                WHERE id=?
                """,
                (
                    status,
                    error_text,
                    video_id,
                    local_video_path,
                    transcript_json_path,
                    finished_at,
                    job_id,
                ),
            )

    def upsert_video(self, video_id: str, source_url: str, metadata: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos(
                    video_id, source_url, title, channel, uploader_id,
                    duration_sec, upload_date, webpage_url, thumbnail,
                    view_count, like_count, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at=excluded.updated_at
                """,
                (
                    video_id,
                    source_url,
                    metadata.get("title"),
                    metadata.get("channel") or metadata.get("uploader"),
                    metadata.get("uploader_id"),
                    metadata.get("duration"),
                    metadata.get("upload_date"),
                    metadata.get("webpage_url"),
                    metadata.get("thumbnail"),
                    metadata.get("view_count"),
                    metadata.get("like_count"),
                    json.dumps(metadata, separators=(",", ":")),
                    now,
                    now,
                ),
            )

    def replace_transcript_segments(
        self, video_id: str, segments: list[dict[str, Any]]
    ) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM transcript_segments WHERE video_id=?", (video_id,))
            rows = [
                (
                    video_id,
                    idx,
                    int(float(seg.get("start", 0.0)) * 1000),
                    int(float(seg.get("end", 0.0)) * 1000),
                    str(seg.get("text", "")).strip(),
                )
                for idx, seg in enumerate(segments)
                if str(seg.get("text", "")).strip()
            ]
            conn.executemany(
                """
                INSERT INTO transcript_segments(video_id, segment_index, start_ms, end_ms, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def list_jobs(self, limit: int = 25) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source_url, status, priority, error_text,
                       video_id, created_at, started_at, finished_at
                FROM ingest_jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, source_url, status, priority, error_text,
                       video_id, local_video_path, transcript_json_path,
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

    def list_latest_done_jobs(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        query = """
            WITH latest_done AS (
                SELECT video_id, MAX(id) AS max_id
                FROM ingest_jobs
                WHERE status = 'done' AND video_id IS NOT NULL
                GROUP BY video_id
            )
            SELECT j.id, j.video_id, j.local_video_path, j.transcript_json_path
            FROM ingest_jobs j
            JOIN latest_done ld
              ON ld.max_id = j.id
            ORDER BY j.id DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def update_job_local_video_path(self, job_id: int, local_video_path: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET local_video_path=?
                WHERE id=?
                """,
                (local_video_path, job_id),
            )

    def get_dashboard_snapshot(self, *, sample_size: int = 100) -> dict[str, Any]:
        with self.connect() as conn:
            count_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM ingest_jobs
                GROUP BY status
                """
            ).fetchall()
            counts = {str(r["status"]): int(r["n"]) for r in count_rows}

            active_rows = conn.execute(
                """
                SELECT id, source_url, status, created_at, started_at
                FROM ingest_jobs
                WHERE status IN ('downloading', 'transcribing')
                ORDER BY started_at ASC
                """
            ).fetchall()

            done_rows = conn.execute(
                """
                SELECT started_at, finished_at
                FROM ingest_jobs
                WHERE status = 'done'
                  AND started_at IS NOT NULL
                  AND finished_at IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (sample_size,),
            ).fetchall()

        now_sec = datetime.now(timezone.utc).timestamp()
        active_jobs: list[dict[str, Any]] = []
        for row in active_rows:
            started_sec = iso_to_epoch_sec(row["started_at"])
            elapsed_sec = max(0.0, now_sec - started_sec) if started_sec is not None else None
            active_jobs.append(
                {
                    "id": int(row["id"]),
                    "source_url": str(row["source_url"]),
                    "status": str(row["status"]),
                    "created_at": row["created_at"],
                    "started_at": row["started_at"],
                    "elapsed_sec": elapsed_sec,
                }
            )

        durations: list[float] = []
        for row in done_rows:
            started_sec = iso_to_epoch_sec(row["started_at"])
            finished_sec = iso_to_epoch_sec(row["finished_at"])
            if started_sec is None or finished_sec is None:
                continue
            duration = finished_sec - started_sec
            if duration > 0:
                durations.append(duration)

        avg_duration_sec = sum(durations) / len(durations) if durations else None
        median_duration_sec = None
        if durations:
            sorted_vals = sorted(durations)
            m = len(sorted_vals) // 2
            if len(sorted_vals) % 2 == 0:
                median_duration_sec = (sorted_vals[m - 1] + sorted_vals[m]) / 2.0
            else:
                median_duration_sec = sorted_vals[m]

        return {
            "counts": {
                "queued": counts.get("queued", 0),
                "downloading": counts.get("downloading", 0),
                "transcribing": counts.get("transcribing", 0),
                "done": counts.get("done", 0),
                "failed": counts.get("failed", 0),
            },
            "active_jobs": active_jobs,
            "avg_duration_sec": avg_duration_sec,
            "median_duration_sec": median_duration_sec,
            "sample_size": len(durations),
        }

    def search_transcript_segments(self, query_text: str, *, limit: int = 200) -> list[dict[str, Any]]:
        needle = query_text.strip().lower()
        if not needle:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH latest_done AS (
                    SELECT video_id, MAX(id) AS max_id
                    FROM ingest_jobs
                    WHERE status = 'done' AND video_id IS NOT NULL
                    GROUP BY video_id
                )
                SELECT
                    ts.video_id,
                    ts.start_ms,
                    ts.end_ms,
                    ts.text,
                    v.title,
                    v.source_url,
                    j.local_video_path,
                    j.transcript_json_path
                FROM transcript_segments ts
                JOIN videos v
                  ON v.video_id = ts.video_id
                LEFT JOIN latest_done ld
                  ON ld.video_id = ts.video_id
                LEFT JOIN ingest_jobs j
                  ON j.id = ld.max_id
                WHERE LOWER(ts.text) LIKE ?
                ORDER BY ts.video_id ASC, ts.start_ms ASC
                LIMIT ?
                """,
                (f"%{needle}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def search_videos_by_transcript(self, query_text: str, *, limit: int = 100) -> list[dict[str, Any]]:
        needle = query_text.strip().lower()
        if not needle:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH latest_done AS (
                    SELECT video_id, MAX(id) AS max_id
                    FROM ingest_jobs
                    WHERE status = 'done' AND video_id IS NOT NULL
                    GROUP BY video_id
                )
                SELECT
                    ts.video_id,
                    COALESCE(v.title, ts.video_id) AS title,
                    COUNT(*) AS match_count,
                    MIN(ts.start_ms) AS first_start_ms,
                    j.local_video_path,
                    j.transcript_json_path
                FROM transcript_segments ts
                JOIN videos v
                  ON v.video_id = ts.video_id
                LEFT JOIN latest_done ld
                  ON ld.video_id = ts.video_id
                LEFT JOIN ingest_jobs j
                  ON j.id = ld.max_id
                WHERE LOWER(ts.text) LIKE ?
                GROUP BY ts.video_id, v.title, j.local_video_path, j.transcript_json_path
                ORDER BY match_count DESC, first_start_ms ASC
                LIMIT ?
                """,
                (f"%{needle}%", limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_jobs_summary(self, limit: int = 25) -> dict[str, Any]:
        return {
            "counts": self.get_dashboard_snapshot()["counts"],
            "jobs": self.list_jobs(limit=limit),
        }

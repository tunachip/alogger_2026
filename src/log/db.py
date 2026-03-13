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
    auto_transcribe: int | None = None


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
                    auto_transcribe INTEGER,
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

                CREATE TABLE IF NOT EXISTS channel_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_key TEXT NOT NULL UNIQUE,
                    source_ref TEXT NOT NULL,
                    channel_title TEXT,
                    feed_url TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    auto_transcribe INTEGER,
                    last_seen_video_id TEXT,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_channel_subscriptions_active
                ON channel_subscriptions(active, updated_at);
                """
            )
            self._ensure_column(conn, "ingest_jobs", "auto_transcribe", "INTEGER")
            self._ensure_column(conn, "channel_subscriptions", "auto_transcribe", "INTEGER")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        column_type_sql: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type_sql}")

    def enqueue(
        self,
        urls: list[str],
        priority: int = 0,
        *,
        auto_transcribe: bool | None = None,
    ) -> list[int]:
        now = utc_now_iso()
        ids: list[int] = []
        with self.connect() as conn:
            for url in urls:
                cur = conn.execute(
                    """
                    INSERT INTO ingest_jobs(source_url, status, auto_transcribe, priority, created_at)
                    VALUES (?, 'queued', ?, ?, ?)
                    """,
                    (url, None if auto_transcribe is None else (1 if auto_transcribe else 0), priority, now),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def reserve_next_job(self) -> Job | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, source_url, status, priority, auto_transcribe
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
                auto_transcribe=(None if row["auto_transcribe"] is None else int(row["auto_transcribe"])),
            )

    def reserve_job_by_id(self, job_id: int) -> Job | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, source_url, status, priority, auto_transcribe
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
                auto_transcribe=(None if row["auto_transcribe"] is None else int(row["auto_transcribe"])),
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
                       auto_transcribe, video_id, created_at, started_at, finished_at
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
                       auto_transcribe, video_id, local_video_path, transcript_json_path,
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

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT video_id, source_url, title, channel, duration_sec, upload_date
                FROM videos
                WHERE video_id=?
                LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_video_asset_paths(self, video_id: str) -> dict[str, list[str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT local_video_path, transcript_json_path
                FROM ingest_jobs
                WHERE video_id=?
                ORDER BY id DESC
                """,
                (video_id,),
            ).fetchall()
        media_paths: list[str] = []
        transcript_paths: list[str] = []
        for row in rows:
            media = str(row["local_video_path"] or "").strip()
            transcript = str(row["transcript_json_path"] or "").strip()
            if media:
                media_paths.append(media)
            if transcript:
                transcript_paths.append(transcript)
        return {"media_paths": media_paths, "transcript_paths": transcript_paths}

    def delete_video_records(self, video_id: str) -> dict[str, int]:
        with self.connect() as conn:
            seg_count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM transcript_segments WHERE video_id=?",
                (video_id,),
            ).fetchone()
            jobs_deleted = conn.execute(
                "DELETE FROM ingest_jobs WHERE video_id=?",
                (video_id,),
            ).rowcount
            videos_deleted = conn.execute(
                "DELETE FROM videos WHERE video_id=?",
                (video_id,),
            ).rowcount
        return {
            "jobs_deleted": int(jobs_deleted or 0),
            "videos_deleted": int(videos_deleted or 0),
            "segments_deleted": int(seg_count_row["n"]) if seg_count_row else 0,
        }

    def get_latest_done_job_for_video(self, video_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, video_id, local_video_path, transcript_json_path, finished_at
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

    def search_videos_by_title(self, query_text: str, *, limit: int = 200) -> list[dict[str, Any]]:
        needle = query_text.strip().lower()
        where_clause = ""
        params: list[Any] = []
        if needle:
            where_clause = "WHERE LOWER(COALESCE(v.title, v.video_id)) LIKE ?"
            params.append(f"%{needle}%")
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH latest_playable AS (
                    SELECT video_id, MAX(id) AS max_id
                    FROM ingest_jobs
                    WHERE
                      video_id IS NOT NULL
                      AND local_video_path IS NOT NULL
                      AND status IN ('transcribing', 'done')
                    GROUP BY video_id
                )
                SELECT
                    v.video_id,
                    COALESCE(v.title, v.video_id) AS title,
                    j.local_video_path,
                    j.transcript_json_path
                FROM videos v
                JOIN latest_playable ld
                  ON ld.video_id = v.video_id
                JOIN ingest_jobs j
                  ON j.id = ld.max_id
                {where_clause}
                ORDER BY LOWER(COALESCE(v.title, v.video_id)) ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_jobs_summary(self, limit: int = 25) -> dict[str, Any]:
        return {
            "counts": self.get_dashboard_snapshot()["counts"],
            "jobs": self.list_jobs(limit=limit),
        }

    def delete_jobs_by_status(self, statuses: list[str]) -> int:
        states = [s.strip().lower() for s in statuses if s and s.strip()]
        if not states:
            return 0
        placeholders = ",".join("?" for _ in states)
        with self.connect() as conn:
            n = conn.execute(
                f"DELETE FROM ingest_jobs WHERE status IN ({placeholders})",
                tuple(states),
            ).rowcount
            return int(n or 0)

    def fail_jobs_by_status(self, statuses: list[str], *, error_text: str | None = None) -> int:
        states = [s.strip().lower() for s in statuses if s and s.strip()]
        if not states:
            return 0
        placeholders = ",".join("?" for _ in states)
        with self.connect() as conn:
            n = conn.execute(
                f"""
                UPDATE ingest_jobs
                SET status='failed',
                    error_text=COALESCE(?, error_text),
                    finished_at=COALESCE(finished_at, ?)
                WHERE status IN ({placeholders})
                """,
                (error_text, utc_now_iso(), *states),
            ).rowcount
            return int(n or 0)

    def upsert_channel_subscription(
        self,
        *,
        channel_key: str,
        source_ref: str,
        feed_url: str,
        channel_title: str | None = None,
        active: bool = True,
        auto_transcribe: bool | None = None,
        last_seen_video_id: str | None = None,
    ) -> int:
        now = utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO channel_subscriptions(
                    channel_key, source_ref, channel_title, feed_url,
                    active, auto_transcribe, last_seen_video_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_key) DO UPDATE SET
                    source_ref=excluded.source_ref,
                    channel_title=excluded.channel_title,
                    feed_url=excluded.feed_url,
                    active=excluded.active,
                    auto_transcribe=COALESCE(excluded.auto_transcribe, channel_subscriptions.auto_transcribe),
                    last_seen_video_id=COALESCE(excluded.last_seen_video_id, channel_subscriptions.last_seen_video_id),
                    updated_at=excluded.updated_at
                """,
                (
                    channel_key,
                    source_ref,
                    channel_title,
                    feed_url,
                    1 if active else 0,
                    None if auto_transcribe is None else (1 if auto_transcribe else 0),
                    last_seen_video_id,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM channel_subscriptions WHERE channel_key=? LIMIT 1",
                (channel_key,),
            ).fetchone()
            if not row:
                raise RuntimeError("failed to resolve channel subscription row id")
            return int(row["id"])

    def list_channel_subscriptions(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE active=1" if active_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    id,
                    channel_key,
                    source_ref,
                    channel_title,
                    feed_url,
                    active,
                    auto_transcribe,
                    last_seen_video_id,
                    last_checked_at,
                    created_at,
                    updated_at
                FROM channel_subscriptions
                {where}
                ORDER BY LOWER(COALESCE(channel_title, channel_key)) ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_channel_subscription(self, *, channel_key: str) -> int:
        with self.connect() as conn:
            n = conn.execute(
                "DELETE FROM channel_subscriptions WHERE channel_key=?",
                (channel_key,),
            ).rowcount
            return int(n or 0)

    def update_channel_subscription(
        self,
        *,
        channel_key: str,
        active: bool | None = None,
        auto_transcribe: bool | None = None,
    ) -> int:
        sets: list[str] = []
        params: list[Any] = []
        if active is not None:
            sets.append("active=?")
            params.append(1 if active else 0)
        if auto_transcribe is not None:
            sets.append("auto_transcribe=?")
            params.append(1 if auto_transcribe else 0)
        if not sets:
            return 0
        sets.append("updated_at=?")
        params.append(utc_now_iso())
        params.append(channel_key)
        with self.connect() as conn:
            n = conn.execute(
                f"UPDATE channel_subscriptions SET {', '.join(sets)} WHERE channel_key=?",
                tuple(params),
            ).rowcount
            return int(n or 0)

    def clear_channel_subscription_auto_transcribe(self, *, channel_key: str) -> int:
        with self.connect() as conn:
            n = conn.execute(
                """
                UPDATE channel_subscriptions
                SET auto_transcribe=NULL, updated_at=?
                WHERE channel_key=?
                """,
                (utc_now_iso(), channel_key),
            ).rowcount
            return int(n or 0)

    def count_videos(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()
            return int(row["n"]) if row else 0

    def update_subscription_poll_state(
        self,
        *,
        channel_key: str,
        last_seen_video_id: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE channel_subscriptions
                SET last_seen_video_id=COALESCE(?, last_seen_video_id),
                    last_checked_at=?,
                    updated_at=?
                WHERE channel_key=?
                """,
                (last_seen_video_id, utc_now_iso(), utc_now_iso(), channel_key),
            )

DB_SCHEMA = """
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

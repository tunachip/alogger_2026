# alogger_2026

Bottom-up build of a high-performance YouTube ingest and query system.

## Ingester Service (Scaffold)

The ingester accepts one or many YouTube URLs and runs:
1. Download video (prefers 1080p with highest fps via `yt-dlp` sorting)
2. Transcribe audio with `whisper` to JSON timestamps
3. Merge split A/V streams into a playback-ready file (no re-encode, `ffmpeg -c copy`)
4. Persist metadata + transcript segments to SQLite
5. Mark job `done` and emit completion event (stdout + optional webhook)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install runtime binaries:
- `yt-dlp`
- `ffmpeg`
- `whisper` CLI (from `openai-whisper` package)
- VLC (desktop app/libvlc) for player commands

### Commands

```bash
# initialize DB + folders
PYTHONPATH=src python -m alogger_ingester init-db

# enqueue one URL
PYTHONPATH=src python -m alogger_ingester enqueue --url "https://www.youtube.com/watch?v=..."

# enqueue from file (one URL per line)
PYTHONPATH=src python -m alogger_ingester enqueue --file ./urls.txt

# download only test (skip metadata + transcription)
PYTHONPATH=src python -m alogger_ingester download-test --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# metadata only test (skip download + transcription)
PYTHONPATH=src python -m alogger_ingester metadata-test --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# single-shot ingest test (enqueue + download + transcribe + index)
PYTHONPATH=src python -m alogger_ingester single-shot-test --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# single-shot ingest without live stage lines
PYTHONPATH=src python -m alogger_ingester single-shot-test --url "https://www.youtube.com/watch?v=nID9gWrUfN4" --quiet-progress

# backfill old done jobs so local_video_path points to merged playback A/V
PYTHONPATH=src python -m alogger_ingester backfill-merge

# transcript query + VLC launch test
PYTHONPATH=src python -m alogger_ingester search-play-test \
  --transcript-json data/transcripts/nID9gWrUfN4_test/nID9gWrUfN4.f251.json \
  --media-path data/media/nID9gWrUfN4.f399.mp4

# full DB transcript search -> open custom player at selected timestamp
PYTHONPATH=src python -m alogger_ingester db-search-play --query "verify identification"

# custom keyboard player test (video left, transcript right)
PYTHONPATH=src python -m alogger_ingester player-test \
  --transcript-json data/transcripts/nID9gWrUfN4_test/nID9gWrUfN4.f251.json \
  --video-path data/media/nID9gWrUfN4.f399.mp4 \
  --audio-path data/media/nID9gWrUfN4.f251.webm

# launch player with no preloaded media (then Ctrl-F to pick from DB)
PYTHONPATH=src python -m alogger_ingester player-db

# launch player with built-in ingest workers (no separate run process needed)
PYTHONPATH=src python -m alogger_ingester player-db --workers 2

# run workers
PYTHONPATH=src python -m alogger_ingester run --workers 4

# inspect recent jobs
PYTHONPATH=src python -m alogger_ingester jobs --limit 50

# live TUI dashboard
PYTHONPATH=src python -m alogger_ingester tui --refresh-sec 1.0 --workers 4
```

### Data Layout

- `data/alogger.db`: SQLite state + metadata + transcript segments + FTS index
- `data/media/`: downloaded media files
- `data/transcripts/<video_id>/`: Whisper JSON outputs

### Core Tables

- `ingest_jobs`: queue and worker state (`queued/downloading/transcribing/done/failed`)
- `videos`: metadata for future filterable querying
- `transcript_segments`: timestamped transcript chunks per video
- `transcript_segments_fts`: FTS5 index for fast text search

### Key Env Vars

- `ALOGGER_DB_PATH`
- `ALOGGER_MEDIA_DIR`
- `ALOGGER_TRANSCRIPT_DIR`
- `ALOGGER_WHISPER_MODEL`
- `ALOGGER_WHISPER_MODEL_DIR`
- `ALOGGER_WHISPER_LANGUAGE`
- `ALOGGER_WORKER_COUNT`
- `ALOGGER_WEBHOOK_URL`

### TUI Controls

- `j` / `k`: move selected worker row
- `Enter`: on an empty worker row, prompt for a YouTube URL and enqueue it
- `Space`: pause/resume selected worker (active subprocesses use `SIGSTOP` / `SIGCONT`)
- `dd`: kill selected worker's active process and prompt whether to delete partial files
- `q`: quit TUI

### TUI Refresh Behavior

- When no worker has an active job, the UI refreshes only on user input.
- When at least one worker is active, the UI auto-refreshes on the configured interval.
- Worker rows show coarse stage-based progress percentages instead of time estimates.

### Player Controls

- Left panel: embedded VLC video playback
- Right panel: precise text filter over transcript segments (substring match, not fuzzy)
- `Up/Down`: move hovered transcript option
- `Enter`: jump video to selected segment start time
- `Ctrl-Space`: toggle play/pause
- `Left/Right`: skim backward/forward (default 5s)
- `Ctrl-F`: open full-DB transcript search popup, then load selected video with query prefilled
- `Ctrl-N`: open ingest popup and enqueue URL(s)
- `Ctrl-I`: toggle ingest-jobs progress popup
- `Esc` twice quickly: close player

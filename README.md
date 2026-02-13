# alogger_2026

Bottom-up build of a high-performance YouTube ingest and query system.

## Ingest Service

Accepts one or many YouTube URLs and then:
1. Downloads Video (1080p with highest fps via `yt-dlp` sorting)
2. Transcribes Audio (whisper) to JSON timestamps
3. Merges A/V into single file (no re-encode, `ffmpeg -c copy`)
4. Pushes Transcript Segments & Video IRL to SQLite DB

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
PYTHONPATH=src python -m alog init-db

# enqueue one URL
PYTHONPATH=src python -m alog enqueue \
    --url "https://www.youtube.com/watch?v=..."

# enqueue from file (one URL per line)
PYTHONPATH=src python -m alog enqueue \
    --file ./urls.txt

# download only test (skip metadata + transcription)
PYTHONPATH=src python -m alog download-test \
    --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# metadata only test (skip download + transcription)
PYTHONPATH=src python -m alog metadata-test \
    --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# single-shot ingest test (enqueue + download + transcribe + index)
PYTHONPATH=src python -m alog single-shot-test \
    --url "https://www.youtube.com/watch?v=nID9gWrUfN4"

# single-shot ingest without live stage lines
PYTHONPATH=src python -m alog single-shot-test \
    --url "https://www.youtube.com/watch?v=nID9gWrUfN4" \
    --quiet-progress

# backfill old done jobs so local_video_path points to merged playback A/V
PYTHONPATH=src python -m alog backfill-merge

# transcript query + VLC launch test
PYTHONPATH=src python -m alog search-play-test \
  --transcript-json data/transcripts/nID9gWrUfN4_test/nID9gWrUfN4.f251.json \
  --media-path data/media/nID9gWrUfN4.f399.mp4

# full DB transcript search -> open custom player at selected timestamp
PYTHONPATH=src python -m alog db-search-play \
    --query "verify identification"

# custom keyboard player test (video left, transcript right)
PYTHONPATH=src python -m alog player-test \
  --transcript-json data/transcripts/nID9gWrUfN4_test/nID9gWrUfN4.f251.json \
  --video-path data/media/nID9gWrUfN4.f399.mp4 \
  --audio-path data/media/nID9gWrUfN4.f251.webm

# launch player with no preloaded media (then Ctrl-F to pick from DB)
PYTHONPATH=src python -m alog player-db

# launch player with built-in ingest workers (no separate run process needed)
PYTHONPATH=src python -m alog player-db \
    --workers 2

# run workers
PYTHONPATH=src python -m alog run \
    --workers 4

# inspect recent jobs
PYTHONPATH=src python -m alog jobs \
    --limit 50

# live TUI dashboard
PYTHONPATH=src python -m alog tui \
    --refresh-sec 1.0 \
    --workers 4
```

### Player Controls

- Left panel: embedded VLC video playback
- Right panel: precise text filter over transcript segments
- `Enter`: jump video to selected segment start time
- `Up/Down`: move hovered transcript option
- `Left/Right`: skim backward/forward
- `Ctrl-P`: toggle play/pause
- `Ctrl-O`: open video, search by title
- `Ctrl-F`: open video, search by transcripts
- `Ctrl-N`: open ingest popup and enqueue URL(s)
- `Ctrl-I`: toggle ingest-jobs progress popup
- `Ctrl-Q`: close player

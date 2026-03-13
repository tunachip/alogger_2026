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

### GUI-Only Release

```bash
# local GUI entrypoint (no ingest CLI parser)
PYTHONPATH=src python -m alogger_player

# create a source payload with only GUI surface
./release/gui-only/export_gui_only.sh

# optional: build single-file GUI binary
./release/gui-only/build_gui_binary.sh
```

The exported payload is written to `release/gui-only/upload_payload`.
For Windows in that payload, run `install_windows.bat` then `run_gui.bat`.

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

# run local HTTP bridge for browser extension handoff
PYTHONPATH=src python -m alog bridge \
    --workers 2

# run workers
PYTHONPATH=src python -m alog run \
    --workers 4

# inspect recent jobs
PYTHONPATH=src python -m alog jobs \
    --limit 50

# list recent videos from channel URL/@handle/name
PYTHONPATH=src python -m alog channel-list \
    --channel "@veritasium" \
    --limit 20

# subscribe channel RSS for auto-ingest of new uploads
PYTHONPATH=src python -m alog subscribe-add \
    --channel "@veritasium"

# inspect subscriptions
PYTHONPATH=src python -m alog subscribe-list

# poll subscriptions immediately once
PYTHONPATH=src python -m alog subscribe-poll

# live TUI dashboard
PYTHONPATH=src python -m alog tui \
    --refresh-sec 1.0 \
    --workers 4
```

### Player Controls

- Left panel: embedded VLC video playback
- Right panel: precise text filter over transcript segments
- Global popup routing uses one-window-at-a-time semantics.
- `Ctrl-P`: command menu
- `Ctrl-N`: ingest popup (`Ingest`, `Browse`, `Subscribe`)
- `Ctrl-I`: workers popup (create/retire/pause/resume commands)
- `Ctrl-O`: open video by title
- `Ctrl-F`: finder (transcript search)
- `Ctrl-A`: AI popup (defaults to Ollama; can target OpenAI-compatible API)
- `Ctrl-S`: skim mode toggle
- `Ctrl-M`: settings popup
- `Ctrl-Q`: close player
- `Enter`: jump video to selected segment start time
- `Up/Down/Home/End/PgUp/PgDown`: navigate filtered transcript list
- `Left/Right`: move filter query cursor
- `Ctrl-Space`: toggle play/pause
- `Ctrl-Left/Right`: seek backward/forward by skim step
- `Ctrl-Up/Down`: jump to previous/next filtered transcript
- `Ctrl-H/J/K/L`: vim alternatives for `Ctrl-Left/Down/Up/Right`
- `Ctrl-T`: toggle transcript log
- `Ctrl-D`: toggle details panel
- `Delete` in `Ctrl-O` video picker: delete selected video + transcript assets + DB records
- Click transcript text: rough seek inside the segment by click position
- Click video panel: toggle pause/resume

### Browser Extension (Chrome + Firefox)

Extension source lives in `browser_extension/`.

1. Start the local bridge:
```bash
PYTHONPATH=src python -m alog bridge --workers 2
```
2. Load the extension folder:
- Chrome/Chromium: `chrome://extensions` -> Developer Mode -> Load unpacked -> `browser_extension/`
- Firefox: `about:debugging` -> This Firefox -> Load Temporary Add-on -> select `browser_extension/manifest.json`
3. Use either:
- Toolbar button on a YouTube page.
- Right-click YouTube link -> `Open Link In Alogger`.

Bridge API:
- `POST http://127.0.0.1:17373/api/open`
- Body: `{ "url": "https://www.youtube.com/watch?v=...", "autoplay": true }`

Flow:
- URL is enqueued.
- Ingest begins immediately.
- Player auto-opens as soon as download finishes (transcription continues in background).

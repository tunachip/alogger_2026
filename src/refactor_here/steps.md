# Refactor Catch-Up Checklist

Goal: bring `refactor_here/` to feature parity (or intentional supersets) of the original app flow.

## 1. Core Architecture & Contracts
- [ ] Freeze core boundaries: `entities`, `ports`, `actions`, `infrastructure`, `gui`.
- [ ] Ensure no UI code imports in core action/use-case modules.
- [ ] Ensure all external dependencies are behind ports (`Downloader`, `Transcriber`, `MediaCatalog`).
- [ ] Add/confirm typed result objects for query flows (not ad-hoc dicts where possible).

## 2. Database & Query Layer
- [ ] Keep one source of truth for catalog implementation in `refactor_here/core/infrastructure/sqlite_media_catalog.py`.
- [ ] Remove or archive stale duplicate implementation in `refactor_here/suggestions/sqlite_media_catalog.py`.
- [ ] Verify schema migration behavior for existing DB files (create-if-missing indexes/tables).
- [ ] Confirm transcript indexing path after successful transcribe.
- [ ] Confirm query methods for:
- [ ] video locator by `id`/`url`/`path`/`canonical_id`
- [ ] transcript text -> timestamp matches
- [ ] top-used ranking (with optional action/time filters)
- [ ] Add utility action wrappers for query use-cases (if not already wired).

## 3. Ingest Pipeline Parity
- [ ] URL ingest action: download -> optional transcribe -> DB update -> transcript indexing.
- [ ] Local ingest action: local file -> optional transcribe -> DB update -> transcript indexing.
- [ ] Ensure state transitions are coherent:
- [ ] `download_state`: queued/running/done/failed/skipped
- [ ] `transcribe_state`: queued/running/done/failed/skipped
- [ ] Ensure failure clears/sets `last_error` predictably.
- [ ] Add idempotency strategy for duplicate URLs/canonical IDs.

## 4. GUI Foundation (Tkinter)
- [x] Single global status bar at bottom.
- [ ] Stable package entrypoint for GUI (e.g., `python -m refactor_here.core.gui ...`).
- [ ] Transcript list/filter/selection UX polish in refactor GUI.
- [ ] Keyboard bindings parity table documented.

## 5. Video Playback Integration (Major Gap)
- [ ] Embed VLC player in `refactor_here` GUI left pane.
- [ ] Implement media load lifecycle and state detection.
- [ ] Add play/pause toggle, seek left/right, jump to selected caption.
- [ ] Implement startup retry/fallback behavior for load failures.
- [ ] Restore playback clock/progress updates.
- [ ] Show current caption based on playhead time.

## 6. DB-Driven GUI Flows
- [ ] “Open from DB” picker (title/path/url search).
- [ ] “Search transcript in DB” picker with timestamp selection.
- [ ] Load selected session (media + transcript) into GUI.
- [ ] Graceful handling when media file is missing but URL exists (or vice versa).

## 7. Ingest/Jobs UI Parity
- [ ] Add ingest popup/form for URL submission.
- [ ] Add jobs panel/list with live refresh.
- [ ] Surface job state + errors in UI.
- [ ] Optional parity: pause/kill controls for active jobs.

## 8. CLI/TUI Integration to Refactor Core
- [ ] Add CLI commands that call refactor actions (not legacy modules).
- [ ] Expose query commands:
- [ ] locate media
- [ ] transcript search (with timestamps)
- [ ] top-used ranking
- [ ] Decide migration path for existing TUI (reuse/adapt/replace).

## 9. Migration & Compatibility
- [ ] Define whether old DB is reused or migrated.
- [ ] If migrated, provide one migration script with rollback strategy.
- [ ] Confirm path compatibility for existing media/transcript directories.
- [ ] Update docs for config precedence: defaults < config/env < per-call args.

## 10. End-to-End Parity Validation
- [ ] Flow A: URL ingest (download+transcribe) -> DB -> GUI open/play.
- [ ] Flow B: local ingest (optional transcribe) -> DB -> GUI open/play.
- [ ] Flow C: transcript query -> timestamp result -> seek/open playback.
- [ ] Flow D: usage tracking and ranking reflects real usage.
- [ ] Flow E: recover from missing files, failed download, failed transcribe.

## 11. Cleanup & Cutover
- [ ] Remove dead code paths from legacy `player/` and `alogger_player/` usage points.
- [ ] Keep temporary compatibility layer only where necessary.
- [ ] Mark old modules deprecated in docs.
- [ ] Set final entrypoints and update README usage examples.

## Suggested Execution Order
1. Finish playback integration in refactor GUI.
2. Complete local-ingest action + GUI DB loading flows.
3. Wire query commands/GUI transcript search to new core.
4. Run end-to-end parity validation list.
5. Decommission legacy wiring.

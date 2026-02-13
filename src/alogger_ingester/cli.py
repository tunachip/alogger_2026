from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import IngesterConfig
from .pipeline import (
    download_url_only,
    fetch_video_metadata,
    load_whisper_segments,
    resolve_playback_media_path,
    transcribe_video,
)
from .query_play import launch_vlc_at_time, pick_db_match_with_fzf, pick_segment_with_fzf
from .service import IngesterService
from .tui import run_tui


def _read_urls(url: str | None, file_path: str | None) -> list[str]:
    urls: list[str] = []
    if url:
        urls.append(url.strip())
    if file_path:
        for line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alogger ingester service")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize sqlite schema")

    enqueue = sub.add_parser("enqueue", help="Queue youtube URL(s) for ingest")
    enqueue.add_argument("--url", help="Single youtube URL")
    enqueue.add_argument("--file", help="Path to text file containing one URL per line")
    enqueue.add_argument("--priority", type=int, default=0)

    run = sub.add_parser("run", help="Run worker loop")
    run.add_argument("--workers", type=int, help="Override worker count")

    jobs = sub.add_parser("jobs", help="List recent ingest jobs")
    jobs.add_argument("--limit", type=int, default=25)

    tui = sub.add_parser("tui", help="Show live ingest dashboard")
    tui.add_argument("--refresh-sec", type=float, default=1.0)
    tui.add_argument("--workers", type=int, help="Override worker count for the TUI worker pool")

    download = sub.add_parser("download-test", help="Download one YouTube URL only (no metadata/transcription)")
    download.add_argument("--url", required=True, help="YouTube URL to download")

    metadata = sub.add_parser("metadata-test", help="Fetch metadata JSON only (no download/transcription)")
    metadata.add_argument("--url", required=True, help="YouTube URL to inspect")
    metadata.add_argument(
        "--full-json",
        action="store_true",
        help="Print full yt-dlp JSON payload instead of a summarized view",
    )

    transcribe = sub.add_parser(
        "transcribe-test",
        help="Transcribe one local media file only (no download/metadata)",
    )
    transcribe.add_argument("--video-path", required=True, help="Path to local media file")
    transcribe.add_argument(
        "--video-id",
        help="Optional video id for transcript folder naming (defaults to file stem)",
    )

    oneshot = sub.add_parser(
        "single-shot-test",
        help="Enqueue one URL and process that exact job end-to-end immediately",
    )
    oneshot.add_argument("--url", required=True, help="YouTube URL to process")
    oneshot.add_argument("--priority", type=int, default=0)
    oneshot.add_argument("--worker-id", type=int, default=99)
    oneshot.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable stage-by-stage live progress lines",
    )

    backfill_merge = sub.add_parser(
        "backfill-merge",
        help="Backfill merged A/V playback paths for latest completed videos",
    )
    backfill_merge.add_argument("--limit", type=int, help="Optional max number of videos to scan")
    backfill_merge.add_argument("--dry-run", action="store_true", help="Show what would change, don't write DB")
    backfill_merge.add_argument(
        "--quiet-progress",
        action="store_true",
        help="Disable per-item progress lines",
    )

    search_play = sub.add_parser(
        "search-play-test",
        help="Search transcript with fzf and launch VLC at chosen segment time",
    )
    search_play.add_argument("--transcript-json", required=True, help="Path to Whisper JSON transcript")
    search_play.add_argument("--media-path", required=True, help="Path to local media file for VLC playback")
    search_play.add_argument("--query", help="Optional initial fzf query text")
    search_play.add_argument("--fzf-bin", default="fzf", help="fzf binary (default: fzf)")
    search_play.add_argument("--vlc-bin", default="vlc", help="VLC binary (default: vlc)")

    db_search_play = sub.add_parser(
        "db-search-play",
        help="Search all transcript segments in DB and open player at selected match",
    )
    db_search_play.add_argument("--query", required=True, help="Precise text query (substring match)")
    db_search_play.add_argument("--limit", type=int, default=300, help="Max matched segments to load")
    db_search_play.add_argument("--fzf-bin", default="fzf", help="fzf binary (default: fzf)")
    db_search_play.add_argument("--skim-seconds", type=float, default=5.0, help="Seek step for left/right keys")

    player = sub.add_parser(
        "player-test",
        help="Launch split player UI (video left, transcript list right)",
    )
    player.add_argument("--transcript-json", required=True, help="Path to Whisper JSON transcript")
    player.add_argument("--video-path", required=True, help="Path to video file")
    player.add_argument("--audio-path", help="Optional separate audio file")
    player.add_argument("--skim-seconds", type=float, default=5.0, help="Seek step for left/right keys")
    player.add_argument("--workers", type=int, default=0, help="Run ingest workers in player process")

    player_db = sub.add_parser(
        "player-db",
        help="Launch player with no preloaded video; use Ctrl-F to pick from DB",
    )
    player_db.add_argument("--skim-seconds", type=float, default=5.0, help="Seek step for left/right keys")
    player_db.add_argument("--workers", type=int, default=0, help="Run ingest workers in player process")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = IngesterConfig.from_env()
    if getattr(args, "workers", None):
        config.worker_count = args.workers

    service = IngesterService(config)

    if args.command == "init-db":
        service.init()
        print(f"initialized db at {config.db_path}")
        return

    if args.command == "enqueue":
        urls = _read_urls(args.url, args.file)
        if not urls:
            parser.error("enqueue requires --url and/or --file with at least one URL")
        service.init()
        ids = service.enqueue(urls, priority=args.priority)
        print(json.dumps({"queued": len(ids), "job_ids": ids}, indent=2))
        return

    if args.command == "jobs":
        service.init()
        print(json.dumps(service.recent_jobs(limit=args.limit), indent=2))
        return

    if args.command == "run":
        service.run_forever()
        return

    if args.command == "download-test":
        service.init()
        path = download_url_only(config, args.url)
        print(json.dumps({"downloaded_path": str(path)}, indent=2))
        return

    if args.command == "metadata-test":
        service.init()
        meta = fetch_video_metadata(config, args.url)
        if args.full_json:
            print(json.dumps(meta, indent=2, ensure_ascii=False))
            return
        summary = {
            "id": meta.get("id"),
            "title": meta.get("title"),
            "channel": meta.get("channel") or meta.get("uploader"),
            "duration_sec": meta.get("duration"),
            "upload_date": meta.get("upload_date"),
            "webpage_url": meta.get("webpage_url"),
            "view_count": meta.get("view_count"),
            "like_count": meta.get("like_count"),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    if args.command == "transcribe-test":
        service.init()
        video_path = Path(args.video_path)
        if not video_path.exists():
            parser.error(f"video file not found: {video_path}")
        video_id = args.video_id or video_path.stem
        transcript_json = transcribe_video(config, video_path, video_id)
        segments = load_whisper_segments(transcript_json)
        result = {
            "video_path": str(video_path),
            "video_id": video_id,
            "transcript_json_path": str(transcript_json),
            "segment_count": len(segments),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.command == "single-shot-test":
        service.init()
        job_ids = service.enqueue([args.url], priority=args.priority)
        job_id = int(job_ids[0])
        if not args.quiet_progress:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] queued job_id={job_id} url={args.url}", flush=True)

        def _progress(stage: str, payload: dict[str, object]) -> None:
            if args.quiet_progress:
                return
            ts = datetime.now().strftime("%H:%M:%S")
            details = []
            if "video_id" in payload:
                details.append(f"video_id={payload['video_id']}")
            if "segment_count" in payload:
                details.append(f"segments={payload['segment_count']}")
            if "error" in payload:
                details.append(f"error={payload['error']}")
            suffix = f" ({', '.join(details)})" if details else ""
            print(f"[{ts}] {stage}{suffix}", flush=True)

        result = service.process_job_id_with_progress(
            job_id,
            worker_id=args.worker_id,
            progress_cb=_progress,
        )
        print(json.dumps({"job_id": job_id, **result}, indent=2, ensure_ascii=False))
        return

    if args.command == "backfill-merge":
        service.init()

        def _progress(stage: str, payload: dict[str, object]) -> None:
            if args.quiet_progress:
                return
            ts = datetime.now().strftime("%H:%M:%S")
            msg = f"[{ts}] {stage} video_id={payload.get('video_id')} job_id={payload.get('job_id')}"
            if "resolved_path" in payload:
                msg += f" path={payload.get('resolved_path')}"
            if "error" in payload:
                msg += f" error={payload.get('error')}"
            print(msg, flush=True)

        summary = service.backfill_merge_playback_paths(
            limit=args.limit,
            dry_run=bool(args.dry_run),
            progress_cb=_progress,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    if args.command == "search-play-test":
        service.init()
        if not sys.stdout.isatty():
            parser.error("search-play-test requires an interactive terminal")
        transcript_json = Path(args.transcript_json)
        if not transcript_json.exists():
            parser.error(f"transcript json not found: {transcript_json}")
        media_path = Path(args.media_path)
        if not media_path.exists():
            parser.error(f"media path not found: {media_path}")
        segments = load_whisper_segments(transcript_json)
        selection = pick_segment_with_fzf(
            segments,
            fzf_bin=args.fzf_bin,
            initial_query=args.query,
        )
        if selection is None:
            print("no selection made")
            return
        pid = launch_vlc_at_time(media_path, float(selection["start_sec"]), vlc_bin=args.vlc_bin)
        result = {
            "media_path": str(media_path),
            "start_sec": round(float(selection["start_sec"]), 3),
            "vlc_pid": pid,
            "selection": selection["raw_line"],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.command == "db-search-play":
        service.init()
        if not sys.stdout.isatty():
            parser.error("db-search-play requires an interactive terminal")
        matches = service.search_segments(args.query, limit=args.limit)
        if not matches:
            print(json.dumps({"query": args.query, "matches": 0}, indent=2, ensure_ascii=False))
            return
        selected = pick_db_match_with_fzf(
            matches,
            fzf_bin=args.fzf_bin,
            initial_query=args.query,
        )
        if selected is None:
            print("no selection made")
            return
        transcript_json_path = selected.get("transcript_json_path")
        local_video_path = selected.get("local_video_path")
        start_ms = int(selected.get("start_ms", 0) or 0)
        if not transcript_json_path:
            parser.error("selected match has no transcript_json_path in DB")
        if not local_video_path:
            parser.error("selected match has no local_video_path in DB")
        transcript_json = Path(str(transcript_json_path))
        preferred_media = Path(str(local_video_path))
        video_path = resolve_playback_media_path(
            config,
            video_id=str(selected.get("video_id") or ""),
            preferred_path=preferred_media,
        )
        if not transcript_json.exists():
            parser.error(f"transcript json not found: {transcript_json}")
        if not video_path.exists():
            parser.error(f"video path not found: {video_path}")

        try:
            from alogger_player.app import run_player
        except ImportError as exc:
            parser.error(
                "db-search-play requires Tk + VLC Python bindings. "
                "Install tkinter system libs and `pip install -r requirements.txt`. "
                f"Original import error: {exc}"
            )
        run_player(
            transcript_json=transcript_json,
            video_path=video_path,
            skim_seconds=float(args.skim_seconds),
            start_sec=float(start_ms) / 1000.0,
        )
        return

    if args.command == "player-test":
        service.init()
        try:
            from alogger_player.app import run_player
        except ImportError as exc:
            parser.error(
                "player-test requires Tk + VLC Python bindings. "
                "Install tkinter system libs (e.g. tk/tcl packages) and `pip install -r requirements.txt`. "
                f"Original import error: {exc}"
            )

        transcript_json = Path(args.transcript_json)
        video_path = Path(args.video_path)
        audio_path = Path(args.audio_path) if args.audio_path else None
        if not transcript_json.exists():
            parser.error(f"transcript json not found: {transcript_json}")
        if not video_path.exists():
            parser.error(f"video path not found: {video_path}")
        if audio_path and not audio_path.exists():
            parser.error(f"audio path not found: {audio_path}")
        run_player(
            transcript_json=transcript_json,
            video_path=video_path,
            audio_path=audio_path,
            skim_seconds=float(args.skim_seconds),
            workers=int(args.workers),
        )
        return

    if args.command == "player-db":
        service.init()
        try:
            from alogger_player.app import run_player
        except ImportError as exc:
            parser.error(
                "player-db requires Tk + VLC Python bindings. "
                "Install tkinter system libs (e.g. tk/tcl packages) and `pip install -r requirements.txt`. "
                f"Original import error: {exc}"
            )
        run_player(
            transcript_json=None,
            video_path=None,
            skim_seconds=float(args.skim_seconds),
            workers=int(args.workers),
        )
        return

    if args.command == "tui":
        if not sys.stdout.isatty():
            parser.error("tui requires an interactive terminal")
        worker_count = args.workers if args.workers else config.worker_count
        run_tui(service, refresh_sec=args.refresh_sec, worker_count=worker_count)
        return

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()

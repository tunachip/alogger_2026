from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import IngestConfig
from .service import IngestService

def _read_sources(urls: str | None, file_paths: str | None) -> list[str]:
    sources: list[str] = []
    if urls:
        sources.append(urls.strip())
    if file_paths:
        for line in Path(file_paths).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sources.append(line)
    return sources

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alogger Service")

    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-db', help="Initialize SQLite Schema")

    enqueue = sub.add_parser(
        'enqueue',
        help='Queue youtube URL(s) for ingest'
    )
    enqueue.add_argument(
        '-u', '--url',
        help='Single Youtube URL'
    )
    enqueue.add_argument(
        '-f', '--file',
        help='Path to text file containing one URL per line'
    )
    enqueue.add_argument(
        '-p', '--priority',
        type=int,
        default=0,
        help='Queue Priority for Download Workers'
    )
    enqueue.add_argument(
        '-o', '--allow-overwrite',
        action='store_true',
        help='Allow queueing URLs whose video_id already exists in the DB'
    )

    run = sub.add_parser(
        'run',
        help="Runs the Downloader Background Services"
    )
    run.add_argument(
        '-w', '--workers',
        type=int,
        help='Number of Download Workers'
    )

    jobs = sub.add_parser(
        'jobs',
        help='Lists recent Ingest Jobs'
    )
    jobs.add_argument(
        '-l', '--limit',
        type=int,
        default=20,
        help='Max number of Jobs to print to the console'
    )
    
    query_play = sub.add_parser(
        'query-and-play',
        help='Search Transcript Segments in DB and open player at selection.'
    )
    query_play.add_argument(
        '-q', '--query',
        required=True,
        help='Text Query for Substring Matching'
    )
    query_play.add_argument(
        '-l', '--limit',
        type=int,
        default=300,
        help='Max Number of Segment Matches to Load'
    )
    query_play.add_argument(
        '-b', '--fzf-bin',
        default='fzf',
        help='fzf binary (default: fzf)'
    )
    return parser

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    
    # Load Config
    config = IngestConfig.from_env()
    if getattr(args, 'workers', None):
        config.worker_count = args.workers

    # Start Service
    service = IngestService(config)

    match(args.command):

        case 'init-db':
            service.init()
            print(f'Initialized db at {config.db_path}')
            return

        case 'enqueue':
            content = _read_sources(args.urls, args.files)
            if not content:
                parser.error('enqueue requires --urls and/or --files with at least one item.')
            service.init()
            result = service.enqueue(
                content,
                priority=args.priority,
                allow_overwrite=bool(args.allow_overwrite)
            )
            print(json.dumps({
                "queued": len(result['queued_ids']),
                'job_ids': result['queued_ids'],
                'conflicts': [{
                    'video_id': c.get('video_id'),
                    'title': c.get('title'),
                    'source': c.get('source'),
                } for c in result['conflicts']],
            }, indent=2))
            return

        case 'jobs':
            service.init()
            print(json.dumps(service.recent_jobs(limit=args.limit), indent=2))
            return

        case 'run':
            service.run_forever()
            return

        case 'query-and-play':
            if not sys.stdout.isatty():
                parser.error('query-and-play requires an interactive terminal.')
            service.init()
            matches = service.search_segments(args.query, limit=args.limit)
            if not matches:
                print(json.dumps({
                    'query': args.query,
                    'matches': 0,
                }, indent=2))
                return
            selection = (
                pick_with_idx(matches)
                if args.use_fzf
                else pick_with_fzf(
                    matches,
                    fzf_bin=args.fzf_bin,
                    query=args.query
                )
            )
            if selection is None:
                print('No Selection Made.')
                return
            db_transcript_path = selection.get('transcript')
            db_video_path = selection.get('video_path')
            db_start_ms = int(selection.get('start_ms'))
            if not db_transcript_path:
                parser.error('selection has no transcript in DB')
            if not db_video_path:
                parser.error('selection has no video_path in DB')
            transcript = Path(str(db_transcript_path))
            video_path = Path(str(db_video_path))
            start_secs = float(db_start_ms * 1000.0)
            if not transcript:
                parser.error(f'transcript not found: {transcript}')
            if not video_path:
                parser.error(f'video_path not found: {video_path}')
            try:
                from .app import run_player
            except ImportError as exc:
                parser.error(
                    'query-and-play requires Tk + VLC Pything Bindings.'
                    'Install dependancies with "pip install -r requirements.txt".'
                    f'Import Error: {exc}'
                )
            run_player(
                transcipt=transcript,
                video_path=video_path,
                start_secs=start_secs,
            )
            return
            
        case _:
            parser.error(f'unknown command: {args.command}')


if __name__ == "__main__":
    main()

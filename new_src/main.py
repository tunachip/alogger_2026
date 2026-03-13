from __future__ import annotations

import argparse
from pathlib import Path

from .app import run_player


def build_parser() -> argpargse.ArgumentParser:
    parser = argparse.ArgumentParser(description='ALOGGER GUI')
    parser.add_argument(
        '-t', '--transcript',
        type=Path,
        help='Path to the local JSON Transcript.'
    )
    parser.add_argument(
        '-v', '--video',
        type=Path,
        help='Path to the local video file.'
    )
    parser.add_argument(
        '-a', '--audio',
        type=Path,
        help='Path to the local audio file.'
    )
    parser.add_argument(
        '-s', '--start',
        type=float,
        default=0.0,
        help='Initial Playback Timestamp.'
    )
    parser.add_argument(
        '-w', '--workers',
        type=int,
        default=0,
        help='Ingest Workers spawned on GUI Launch.'
    )
    return parser

def main() -> None:
    args = build_parser().parse_args()
    print(
        f'''
         ===== ALOGGER 2026 =====
        running from: "{Path.cwd()}"
        transcript:   "{args.transcript}"
        video_path:   "{args.video_path}"
        audio_path:   "{args.audio_path}"
        start_secs:   {args.start_secs}
        workers:      {args.workers}
         =======================
        '''
    )
    run_player(
        transcript = args.transcript,
        video_path = args.video_path,
        audio_path = args.audio_path,
        start_secs = args.start_secs,
        workers    = args.workers,
    )

if __name__ == '__main__':
    main()

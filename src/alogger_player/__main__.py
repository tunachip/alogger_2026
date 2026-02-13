from __future__ import annotations

import argparse
from pathlib import Path

from .app import run_player


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alog GUI player")
    parser.add_argument("--transcript-json", type=Path, help="Path to Whisper JSON transcript")
    parser.add_argument("--video-path", type=Path, help="Path to local video file")
    parser.add_argument("--audio-path", type=Path, help="Optional separate audio file")
    parser.add_argument("--skim-seconds", type=float, default=5.0, help="Seek step for left/right keys")
    parser.add_argument("--start-sec", type=float, default=0.0, help="Initial playback timestamp")
    parser.add_argument("--workers", type=int, default=0, help="Run ingest workers in GUI process")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_player(
        transcript_json=args.transcript_json,
        video_path=args.video_path,
        audio_path=args.audio_path,
        skim_seconds=args.skim_seconds,
        start_sec=args.start_sec,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
PYTHONPATH=src ALOGGER_WHISPER_MODEL=tiny python -m alogger_ingester transcribe-test --video-path data/media/nID9gWrUfN4.f399.mp4 --video-id nID9gWrUfN4_test

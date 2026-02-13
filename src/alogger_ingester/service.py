from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import IngesterConfig
from .db import DB, Job
from .notify import send_webhook
from .pipeline import (
    PipelineError,
    download_video,
    fetch_video_metadata,
    load_whisper_segments,
    transcribe_video,
)


class IngesterService:
    def __init__(self, config: IngesterConfig) -> None:
        self.config = config
        self.db = DB(config.db_path)
        self._stop_event = threading.Event()

    def init(self) -> None:
        self.config.ensure_dirs()
        self.db.init_schema()

    def enqueue(self, urls: list[str], priority: int = 0) -> list[int]:
        return self.db.enqueue(urls, priority=priority)

    def run_forever(self) -> None:
        self.init()
        with ThreadPoolExecutor(max_workers=self.config.worker_count) as executor:
            futures = [executor.submit(self._worker_loop, i) for i in range(self.config.worker_count)]
            try:
                for f in futures:
                    f.result()
            except KeyboardInterrupt:
                self._stop_event.set()
                for f in futures:
                    f.cancel()

    def stop(self) -> None:
        self._stop_event.set()

    def _worker_loop(self, worker_id: int) -> None:
        while not self._stop_event.is_set():
            job = self.db.reserve_next_job()
            if not job:
                time.sleep(self.config.poll_interval_sec)
                continue

            try:
                self._process_job(job, worker_id)
            except Exception as exc:  # defensive catch for service stability
                self.db.update_job_status(job.id, "failed", error_text=str(exc))
                self._notify("failed", job_id=job.id, url=job.url, error=str(exc), worker_id=worker_id)

    def _process_job(self, job: Job, worker_id: int) -> None:
        metadata = fetch_video_metadata(self.config, job.url)
        video_id = metadata.get("id")
        if not video_id:
            raise PipelineError("yt-dlp metadata did not include video id")

        self.db.upsert_video(video_id=video_id, source_url=job.url, metadata=metadata)

        local_video_path = download_video(self.config, job.url, video_id)
        self.db.update_job_status(
            job.id,
            "transcribing",
            video_id=video_id,
            local_video_path=str(local_video_path),
        )

        transcript_json_path = transcribe_video(self.config, local_video_path, video_id)
        segments = load_whisper_segments(transcript_json_path)
        self.db.replace_transcript_segments(video_id=video_id, segments=segments)

        self.db.update_job_status(
            job.id,
            "done",
            video_id=video_id,
            local_video_path=str(local_video_path),
            transcript_json_path=str(transcript_json_path),
        )
        self._notify(
            "done",
            job_id=job.id,
            url=job.url,
            video_id=video_id,
            transcript_json_path=str(transcript_json_path),
            worker_id=worker_id,
        )

    def _notify(self, event: str, **payload: object) -> None:
        message = {"event": event, **payload}
        print(message, flush=True)
        if self.config.webhook_url:
            try:
                send_webhook(self.config.webhook_url, message)
            except Exception:
                # Keep ingest workers running even when notification delivery fails.
                pass

    def recent_jobs(self, limit: int = 25) -> list[dict[str, object]]:
        return self.db.list_jobs(limit=limit)

    def dashboard_snapshot(self) -> dict[str, object]:
        return self.db.get_dashboard_snapshot()

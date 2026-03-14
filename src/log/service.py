from __future__ import annotations

import difflib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Callable

from .ai import generate_transcript_summary, merged_ai_settings
from .config import IngesterConfig
from .db import DB, Job
from .notify import send_webhook
from .pipeline import (
    PipelineError,
    _media_has_audio_stream,
    _media_has_video_stream,
    download_video,
    fetch_youtube_rss_feed,
    fetch_video_metadata,
    list_channel_videos,
    channel_feed_url_from_channel_id,
    load_whisper_segments,
    merge_streams_for_playback,
    transcribe_video,
)


class IngesterService:
    def __init__(self, config: IngesterConfig) -> None:
        self.config = config
        self.db = DB(config.db_path)
        self.auto_transcribe_default = True
        self.subscription_db_max_videos = 0
        self.job_retry_limit = 0
        self._ai_runtime_settings = merged_ai_settings()
        self.downloader_worker_count = max(1, int(config.worker_count))
        self.transcriber_worker_count = max(1, int(config.worker_count))
        self.summarizer_worker_count = max(1, int(config.worker_count))
        self._download_sem = threading.Semaphore(self.downloader_worker_count)
        self._transcribe_sem = threading.Semaphore(self.transcriber_worker_count)
        self._summarize_sem = threading.Semaphore(self.summarizer_worker_count)
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._subscription_thread: threading.Thread | None = None
        self._summary_state_lock = threading.Lock()
        self._active_summary_jobs: dict[int, dict[str, object]] = {}

    def init(self) -> None:
        self.config.ensure_dirs()
        self.db.init_schema()

    def enqueue(
        self,
        urls: list[str],
        priority: int = 0,
        *,
        auto_transcribe: bool | None = None,
    ) -> list[int]:
        return self.db.enqueue_download(urls, priority=priority, auto_transcribe=auto_transcribe)

    def inspect_url(self, url: str) -> dict[str, object]:
        self.init()
        metadata = fetch_video_metadata(self.config, url)
        video_id = str(metadata.get("id") or "")
        if not video_id:
            raise PipelineError("yt-dlp metadata did not include video id")
        existing_video = self.db.get_video(video_id)
        existing_done = self.db.get_latest_done_job_for_video(video_id)
        return {
            "url": url,
            "video_id": video_id,
            "title": metadata.get("title"),
            "exists": existing_video is not None,
            "existing_video": existing_video,
            "existing_done_job": existing_done,
        }

    def fetch_url_metadata(self, url: str) -> dict[str, object]:
        return fetch_video_metadata(self.config, url)

    def enqueue_with_dedupe(
        self,
        urls: list[str],
        *,
        priority: int = 0,
        allow_overwrite: bool = False,
        auto_transcribe: bool | None = None,
    ) -> dict[str, object]:
        self.init()
        queued_ids: list[int] = []
        conflicts: list[dict[str, object]] = []
        for url in urls:
            info = self.inspect_url(url)
            if bool(info.get("exists")) and not allow_overwrite:
                conflicts.append(info)
                continue
            ids = self.db.enqueue_download([url], priority=priority, auto_transcribe=auto_transcribe)
            queued_ids.extend(ids)
        return {"queued_ids": queued_ids, "conflicts": conflicts}

    def set_runtime_options(
        self,
        *,
        auto_transcribe_default: bool | None = None,
        subscription_db_max_videos: int | None = None,
        job_retry_limit: int | None = None,
        ai_runtime_settings: dict[str, object] | None = None,
    ) -> None:
        if auto_transcribe_default is not None:
            self.auto_transcribe_default = bool(auto_transcribe_default)
        if subscription_db_max_videos is not None:
            self.subscription_db_max_videos = max(0, int(subscription_db_max_videos))
        if job_retry_limit is not None:
            self.job_retry_limit = max(0, int(job_retry_limit))
        if ai_runtime_settings is not None:
            self._ai_runtime_settings = merged_ai_settings(ai_runtime_settings)

    def _summary_enabled(self) -> bool:
        return bool(self._ai_runtime_settings.get("auto_summary_default", False))

    def _ensure_summary_ollama_ready(self) -> None:
        pass

    def _generate_video_summary(
        self,
        *,
        video_id: str,
        metadata: dict[str, object],
        segments: list[dict[str, object]],
        job_id: int,
        worker_id: int,
        force: bool = False,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        if (not force and not self._summary_enabled()) or not segments:
            return
        with self._summarize_sem:
            started_at = time.monotonic()
            with self._summary_state_lock:
                self._active_summary_jobs[job_id] = {
                    "job_id": job_id,
                    "video_id": video_id,
                    "worker_id": worker_id,
                    "started_monotonic": started_at,
                }
            if progress_cb:
                progress_cb("summary_start", {"job_id": job_id, "video_id": video_id})
            try:
                payload = generate_transcript_summary(
                    transcript_segments=segments,
                    metadata=metadata,
                    settings=self._ai_runtime_settings,
                    ensure_ollama_ready=self._ensure_summary_ollama_ready,
                )
                summary = str(payload.get("summary") or "").strip()
                genres_raw = payload.get("genre") or []
                genres = [str(item).strip() for item in genres_raw if str(item).strip()]
                merged_fields = {
                    "summary": summary,
                    "genre": genres,
                    "summary_model": payload.get("model"),
                    "summary_segment_limit": payload.get("segment_limit"),
                }
                self.db.merge_video_metadata_fields(video_id, merged_fields)
                if progress_cb:
                    progress_cb(
                        "summary_done",
                        {
                            "job_id": job_id,
                            "video_id": video_id,
                            "genre_count": len(genres),
                            "summary_length": len(summary),
                        },
                    )
                self._notify(
                    "summary_done",
                    job_id=job_id,
                    video_id=video_id,
                    worker_id=worker_id,
                    genre_count=len(genres),
                    summary_length=len(summary),
                )
            except Exception as exc:
                self.db.merge_video_metadata_fields(
                    video_id,
                    {
                        "summary_error": str(exc),
                    },
                )
                if progress_cb:
                    progress_cb(
                        "summary_failed",
                        {
                            "job_id": job_id,
                            "video_id": video_id,
                            "error": str(exc),
                        },
                    )
                self._notify(
                    "summary_failed",
                    job_id=job_id,
                    video_id=video_id,
                    worker_id=worker_id,
                    error=str(exc),
                )
            finally:
                with self._summary_state_lock:
                    self._active_summary_jobs.pop(job_id, None)

    def _handle_job_failure(
        self,
        job: Job,
        exc: Exception,
        *,
        worker_id: int,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        error = str(exc)
        if int(job.retries) < int(self.job_retry_limit):
            self.db.retry_job(job.id, error_text=error)
            self._notify(
                "retrying",
                job_id=job.id,
                url=job.url,
                error=error,
                worker_id=worker_id,
                retries=int(job.retries) + 1,
                retry_limit=int(self.job_retry_limit),
            )
            if progress_cb:
                progress_cb(
                    "retrying",
                    {
                        "job_id": job.id,
                        "url": job.url,
                        "worker_id": worker_id,
                        "error": error,
                        "retries": int(job.retries) + 1,
                        "retry_limit": int(self.job_retry_limit),
                    },
                )
            return
        self.db.update_job_status(job.id, "failed", error_text=error)
        self._notify("failed", job_id=job.id, url=job.url, error=error, worker_id=worker_id)
        if progress_cb:
            progress_cb(
                "failed",
                {
                    "job_id": job.id,
                    "url": job.url,
                    "worker_id": worker_id,
                    "error": error,
                },
            )

    def run_forever(self) -> None:
        self.init()
        self._stop_event.clear()
        self._start_subscription_poller()
        total_workers = max(
            1,
            self.downloader_worker_count
            + self.transcriber_worker_count
            + self.summarizer_worker_count,
        )
        with ThreadPoolExecutor(max_workers=total_workers) as executor:
            futures = []
            futures.extend(
                executor.submit(self._download_worker_loop, i)
                for i in range(self.downloader_worker_count)
            )
            futures.extend(
                executor.submit(self._transcribe_worker_loop, i)
                for i in range(self.transcriber_worker_count)
            )
            futures.extend(
                executor.submit(self._summary_worker_loop, i)
                for i in range(self.summarizer_worker_count)
            )
            try:
                for f in futures:
                    f.result()
            except KeyboardInterrupt:
                self._stop_event.set()
                for f in futures:
                    f.cancel()
            finally:
                self.stop_background_workers()

    def process_job_id(self, job_id: int, worker_id: int = 0) -> dict[str, object]:
        return self.process_job_id_with_progress(job_id, worker_id=worker_id)

    def enqueue_video_for_transcription(
        self,
        video_id: str,
        *,
        priority: int = 0,
        force: bool = False,
    ) -> dict[str, object]:
        self.init()
        video = self.db.get_video(video_id)
        if not video:
            raise PipelineError(f"Unknown video id: {video_id}")
        playable = self.db.get_latest_playable_job_for_video(video_id)
        media_path = str((playable or {}).get("local_video_path") or "").strip()
        if not media_path:
            raise PipelineError("Video has no local media to transcribe")
        pending = self.db.pending_job_for_video_stage(video_id, "transcribe")
        if pending and not force:
            return {"queued": False, "reason": "already_queued", "job": pending}
        job_id = self.db.enqueue_video_stage(
            video_id=video_id,
            source_url=str(video.get("source_url") or playable.get("source_url") or ""),
            queue_stage="transcribe",
            priority=priority,
            auto_transcribe=True,
            local_video_path=media_path,
            transcript_json_path=str((playable or {}).get("transcript_json_path") or "").strip() or None,
        )
        return {"queued": True, "job_id": job_id, "video_id": video_id, "queue_stage": "transcribe"}

    def enqueue_video_for_summary(
        self,
        video_id: str,
        *,
        priority: int = 0,
        force: bool = False,
    ) -> dict[str, object]:
        self.init()
        video = self.db.get_video(video_id)
        if not video:
            raise PipelineError(f"Unknown video id: {video_id}")
        playable = self.db.get_latest_playable_job_for_video(video_id)
        media_path = str((playable or {}).get("local_video_path") or "").strip()
        transcript_path = str((playable or {}).get("transcript_json_path") or "").strip()
        if not transcript_path:
            raise PipelineError("Video has no transcript to summarize")
        pending = self.db.pending_job_for_video_stage(video_id, "summarize")
        if pending and not force:
            return {"queued": False, "reason": "already_queued", "job": pending}
        job_id = self.db.enqueue_video_stage(
            video_id=video_id,
            source_url=str(video.get("source_url") or playable.get("source_url") or ""),
            queue_stage="summarize",
            priority=priority,
            auto_transcribe=True,
            local_video_path=media_path or None,
            transcript_json_path=transcript_path,
        )
        return {"queued": True, "job_id": job_id, "video_id": video_id, "queue_stage": "summarize"}

    def process_job_id_with_progress(
        self,
        job_id: int,
        *,
        worker_id: int = 0,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        self.init()
        processed_any = False
        while True:
            job = self.db.reserve_job_by_id(job_id)
            if not job:
                row = self.db.get_job(job_id)
                if not processed_any:
                    if row:
                        return {"processed": False, "reason": "job_not_queued", "job": row}
                    return {"processed": False, "reason": "job_not_found", "job_id": job_id}
                break
            processed_any = True
            try:
                self._process_reserved_job(job, worker_id, progress_cb=progress_cb)
            except Exception as exc:
                self._handle_job_failure(
                    job,
                    exc,
                    worker_id=worker_id,
                    progress_cb=progress_cb,
                )
                break
        row = self.db.get_job(job.id)
        return {"processed": True, "job": row if row else {"id": job.id}}

    def _process_reserved_job(
        self,
        job: Job,
        worker_id: int,
        *,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        stage = str(job.queue_stage or "download")
        if stage == "download":
            self._process_download_job(job, worker_id, progress_cb=progress_cb)
            return
        if stage == "transcribe":
            self._process_transcribe_job(job, worker_id, progress_cb=progress_cb)
            return
        if stage == "summarize":
            self._process_summary_job(job, worker_id, progress_cb=progress_cb)
            return
        raise PipelineError(f"Unsupported queue stage: {stage}")

    def stop(self) -> None:
        self._stop_event.set()
        self.stop_background_workers()

    def start_background_workers(
        self,
        worker_count: int,
        *,
        downloader_count: int | None = None,
        transcriber_count: int | None = None,
        summarizer_count: int | None = None,
    ) -> None:
        self.init()
        if (
            worker_count <= 0
            and (downloader_count or 0) <= 0
            and (transcriber_count or 0) <= 0
            and (summarizer_count or 0) <= 0
        ):
            return
        if self._worker_threads:
            return
        d_count = max(0, int(self.downloader_worker_count if downloader_count is None else downloader_count))
        t_count = max(0, int(self.transcriber_worker_count if transcriber_count is None else transcriber_count))
        s_count = max(0, int(self.summarizer_worker_count if summarizer_count is None else summarizer_count))
        if d_count <= 0 and t_count <= 0 and s_count <= 0:
            return
        self.downloader_worker_count = max(1, d_count or 1)
        self.transcriber_worker_count = max(1, t_count or 1)
        self.summarizer_worker_count = max(1, s_count or 1)
        self._download_sem = threading.Semaphore(self.downloader_worker_count)
        self._transcribe_sem = threading.Semaphore(self.transcriber_worker_count)
        self._summarize_sem = threading.Semaphore(self.summarizer_worker_count)
        self._stop_event.clear()
        self._worker_threads = []
        self._worker_threads.extend(
            threading.Thread(target=self._download_worker_loop, args=(i,), daemon=True)
            for i in range(self.downloader_worker_count)
        )
        self._worker_threads.extend(
            threading.Thread(target=self._transcribe_worker_loop, args=(i,), daemon=True)
            for i in range(self.transcriber_worker_count)
        )
        self._worker_threads.extend(
            threading.Thread(target=self._summary_worker_loop, args=(i,), daemon=True)
            for i in range(self.summarizer_worker_count)
        )
        for t in self._worker_threads:
            t.start()
        self._start_subscription_poller()

    def stop_background_workers(self) -> None:
        self._stop_event.set()
        threads = self._worker_threads[:]
        self._worker_threads = []
        for t in threads:
            t.join(timeout=2.0)
        sub_thread = self._subscription_thread
        self._subscription_thread = None
        if sub_thread:
            sub_thread.join(timeout=2.0)

    def _start_subscription_poller(self) -> None:
        if self._subscription_thread and self._subscription_thread.is_alive():
            return
        self._subscription_thread = threading.Thread(
            target=self._subscription_poll_loop,
            daemon=True,
            name="alog-subscription-poller",
        )
        self._subscription_thread.start()

    def _subscription_poll_loop(self) -> None:
        interval = max(30.0, float(self.config.subscription_poll_interval_sec))
        while not self._stop_event.is_set():
            try:
                summary = self.poll_subscriptions_once()
                if int(summary.get("queued", 0)) > 0:
                    self._notify("subscription_poll", **summary)
            except Exception as exc:
                self._notify("subscription_poll_failed", error=str(exc))
            self._stop_event.wait(interval)

    def _download_worker_loop(self, worker_id: int) -> None:
        while not self._stop_event.is_set():
            job = self.db.reserve_next_job_for_stage("download")
            if not job:
                time.sleep(self.config.poll_interval_sec)
                continue

            try:
                self._process_download_job(job, worker_id)
            except Exception as exc:  # defensive catch for service stability
                self._handle_job_failure(job, exc, worker_id=worker_id)

    def _transcribe_worker_loop(self, worker_id: int) -> None:
        while not self._stop_event.is_set():
            job = self.db.reserve_next_job_for_stage("transcribe")
            if not job:
                time.sleep(self.config.poll_interval_sec)
                continue
            try:
                self._process_transcribe_job(job, worker_id)
            except Exception as exc:
                self._handle_job_failure(job, exc, worker_id=worker_id)

    def _summary_worker_loop(self, worker_id: int) -> None:
        while not self._stop_event.is_set():
            job = self.db.reserve_next_job_for_stage("summarize")
            if not job:
                time.sleep(self.config.poll_interval_sec)
                continue
            try:
                self._process_summary_job(job, worker_id)
            except Exception as exc:
                self._handle_job_failure(job, exc, worker_id=worker_id)

    def _process_download_job(
        self,
        job: Job,
        worker_id: int,
        *,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        if progress_cb:
            progress_cb("metadata_start", {"job_id": job.id, "url": job.url, "worker_id": worker_id})
        metadata = fetch_video_metadata(self.config, job.url)
        video_id = metadata.get("id")
        if not video_id:
            raise PipelineError("yt-dlp metadata did not include video id")
        if progress_cb:
            progress_cb("metadata_done", {"job_id": job.id, "video_id": str(video_id)})
        self.db.upsert_video(video_id=video_id, source_url=job.url, metadata=metadata)
        with self._download_sem:
            if progress_cb:
                progress_cb("download_start", {"job_id": job.id, "video_id": str(video_id)})
            local_video_path = download_video(self.config, job.url, video_id)
            if progress_cb:
                progress_cb("download_done", {"job_id": job.id, "video_id": str(video_id), "local_video_path": str(local_video_path)})
        should_transcribe = (
            self.auto_transcribe_default
            if job.auto_transcribe is None
            else bool(int(job.auto_transcribe))
        )
        if should_transcribe:
            self.db.queue_job_stage(
                job.id,
                queue_stage="transcribe",
                video_id=str(video_id),
                local_video_path=str(local_video_path),
            )
            if progress_cb:
                progress_cb("transcribe_queued", {"job_id": job.id, "video_id": str(video_id)})
            return
        if progress_cb:
            progress_cb("transcribe_skipped", {"job_id": job.id, "video_id": str(video_id)})
        final_media_path = self._finalize_job_media(
            video_id=str(video_id),
            fallback_path=local_video_path,
            job_id=job.id,
            progress_cb=progress_cb,
        )
        self.db.update_job_status(
            job.id,
            "done",
            queue_stage="download",
            video_id=str(video_id),
            local_video_path=str(final_media_path),
        )
        if progress_cb:
            progress_cb("done", {"job_id": job.id, "video_id": str(video_id), "local_video_path": str(final_media_path), "transcript_json_path": None})

    def _process_transcribe_job(
        self,
        job: Job,
        worker_id: int,
        *,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        video_id = str(job.video_id or "")
        local_video_path = Path(str(job.local_video_path or ""))
        if not video_id or not str(local_video_path):
            raise PipelineError("Transcribe job missing local video path")
        with self._transcribe_sem:
            if progress_cb:
                progress_cb("transcribe_start", {"job_id": job.id, "video_id": video_id, "local_video_path": str(local_video_path)})
            transcript_json_path = transcribe_video(self.config, local_video_path, video_id)
            if progress_cb:
                progress_cb("transcribe_done", {"job_id": job.id, "video_id": video_id, "transcript_json_path": str(transcript_json_path)})
                progress_cb("index_start", {"job_id": job.id, "video_id": video_id})
            segments = load_whisper_segments(transcript_json_path)
            self.db.replace_transcript_segments(video_id=video_id, segments=segments)
            if progress_cb:
                progress_cb("index_done", {"job_id": job.id, "video_id": video_id, "segment_count": len(segments)})
        if self._summary_enabled():
            self.db.queue_job_stage(
                job.id,
                queue_stage="summarize",
                video_id=video_id,
                local_video_path=str(local_video_path),
                transcript_json_path=str(transcript_json_path),
            )
            if progress_cb:
                progress_cb("summary_queued", {"job_id": job.id, "video_id": video_id})
            return
        final_media_path = self._finalize_job_media(
            video_id=video_id,
            fallback_path=local_video_path,
            job_id=job.id,
            progress_cb=progress_cb,
        )
        self.db.update_job_status(
            job.id,
            "done",
            queue_stage="transcribe",
            video_id=video_id,
            local_video_path=str(final_media_path),
            transcript_json_path=str(transcript_json_path),
        )
        if progress_cb:
            progress_cb("done", {"job_id": job.id, "video_id": video_id, "local_video_path": str(final_media_path), "transcript_json_path": str(transcript_json_path)})

    def _process_summary_job(
        self,
        job: Job,
        worker_id: int,
        *,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        video_id = str(job.video_id or "")
        transcript_json_path = Path(str(job.transcript_json_path or ""))
        fallback_path = Path(str(job.local_video_path or ""))
        if not video_id or not str(transcript_json_path):
            raise PipelineError("Summary job missing transcript path")
        metadata = self.db.get_video(video_id)
        if not metadata:
            raise PipelineError(f"Unknown video id: {video_id}")
        rich_metadata = dict(metadata)
        raw_metadata = str(rich_metadata.get("metadata_json") or "").strip()
        if raw_metadata:
            try:
                loaded = json.loads(raw_metadata)
                if isinstance(loaded, dict):
                    rich_metadata.update(loaded)
            except Exception:
                pass
        segments = load_whisper_segments(transcript_json_path)
        self._generate_video_summary(
            video_id=video_id,
            metadata=rich_metadata,
            segments=segments,
            job_id=job.id,
            worker_id=worker_id,
            force=True,
            progress_cb=progress_cb,
        )
        final_media_path = self._finalize_job_media(
            video_id=video_id,
            fallback_path=fallback_path,
            job_id=job.id,
            progress_cb=progress_cb,
        )
        self.db.update_job_status(
            job.id,
            "done",
            queue_stage="summarize",
            video_id=video_id,
            local_video_path=str(final_media_path) if str(final_media_path) else None,
            transcript_json_path=str(transcript_json_path),
        )
        if progress_cb:
            progress_cb("done", {"job_id": job.id, "video_id": video_id, "local_video_path": str(final_media_path), "transcript_json_path": str(transcript_json_path)})

    def _finalize_job_media(
        self,
        *,
        video_id: str,
        fallback_path: Path,
        job_id: int,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> Path:
        if progress_cb:
            progress_cb("merge_start", {"job_id": job_id, "video_id": video_id})
        playback_path = merge_streams_for_playback(self.config, video_id=video_id)
        final_media_path = playback_path if playback_path is not None else fallback_path
        if progress_cb:
            progress_cb("merge_done", {"job_id": job_id, "video_id": video_id, "local_video_path": str(final_media_path)})
        return final_media_path

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
        snapshot = self.db.get_dashboard_snapshot()
        now = time.monotonic()
        with self._summary_state_lock:
            active_summary = [
                {
                    "job_id": int(row.get("job_id") or 0),
                    "video_id": str(row.get("video_id") or ""),
                    "worker_id": int(row.get("worker_id") or 0),
                    "elapsed_sec": max(
                        0.0,
                        now - float(row.get("started_monotonic") or now),
                    ),
                }
                for row in self._active_summary_jobs.values()
            ]
        counts = dict(snapshot.get("counts") or {})
        counts["summarizing"] = len(active_summary)
        snapshot["counts"] = counts
        snapshot["active_summary_jobs"] = active_summary
        return snapshot

    def search_segments(self, query_text: str, *, limit: int = 200) -> list[dict[str, object]]:
        return self.db.search_transcript_segments(query_text, limit=limit)

    def search_videos(self, query_text: str, *, limit: int = 100) -> list[dict[str, object]]:
        return self.db.search_videos_by_transcript(query_text, limit=limit)

    def search_video_titles(self, query_text: str, *, limit: int = 200) -> list[dict[str, object]]:
        rows = self.db.search_videos_by_title(query_text, limit=limit)
        needle = query_text.strip().lower()
        out: list[dict[str, object]] = []
        for row in rows:
            payload = dict(row)
            title = str(payload.get("title") or payload.get("video_id") or "")
            payload["match_count"] = title.lower().count(needle) if needle else 1
            out.append(payload)
        return out

    def search_video_metadata(self, query_text: str, *, limit: int = 200) -> list[dict[str, object]]:
        needle = query_text.strip().lower()
        rows = [dict(r) for r in self.db.search_videos_by_metadata(needle, limit=limit)]
        if rows or not needle:
            return rows

        # Fuzzy fallback for misspellings (e.g., "syvlan" vs "sylvanfranklin").
        candidates = [dict(r) for r in self.db.list_playable_videos(limit=max(300, limit * 3))]
        stop = {
            "play", "a", "an", "the", "video", "videos", "open", "show", "watch",
            "by", "from", "please", "for", "me", "latest", "recent",
        }
        query_terms = [t for t in re.split(r"[^a-z0-9]+", needle) if t and t not in stop]
        if not query_terms:
            query_terms = [needle]

        def score_row(row: dict[str, object]) -> float:
            fields = [
                str(row.get("title") or ""),
                str(row.get("channel") or ""),
                str(row.get("uploader_id") or ""),
                str(row.get("video_id") or ""),
                str(row.get("source_url") or ""),
                str(row.get("webpage_url") or ""),
            ]
            if not fields:
                return 0.0
            term_scores: list[float] = []
            for field in fields:
                f = field.lower().strip()
                if not f:
                    continue
                field_tokens = [tok for tok in re.split(r"[^a-z0-9]+", f) if tok]
                for q in query_terms:
                    best_q = 0.0
                    if q in f:
                        best_q = 1.0
                    else:
                        best_q = max(best_q, difflib.SequenceMatcher(None, q, f).ratio())
                        for tok in field_tokens:
                            # Prefix/suffix containment catches single missing-char names well.
                            if q in tok or tok in q:
                                best_q = max(best_q, 0.92)
                            best_q = max(best_q, difflib.SequenceMatcher(None, q, tok).ratio())
                    term_scores.append(best_q)
            if not term_scores:
                return 0.0
            # Use mean of top query-term matches so one strong creator match can win.
            term_scores.sort(reverse=True)
            return sum(term_scores[: max(1, min(len(query_terms), 3))]) / float(max(1, min(len(query_terms), 3)))

        scored: list[tuple[float, dict[str, object]]] = []
        for row in candidates:
            s = score_row(row)
            if s >= 0.36:
                scored.append((s, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _s, row in scored[:limit]]

    def jobs_summary(self, limit: int = 25) -> dict[str, object]:
        return self.db.list_jobs_summary(limit=limit)

    def clear_queue(self) -> int:
        self.init()
        return self.db.delete_jobs_by_status(["queued"])

    def clear_next_queued_job(self) -> dict[str, object] | None:
        self.init()
        return self.db.delete_oldest_job_by_status(["queued"])

    def clear_next_queued_job_for_stage(self, stage: str) -> dict[str, object] | None:
        self.init()
        return self.db.delete_oldest_queued_job_for_stage(stage)

    def kill_active_jobs(self) -> int:
        self.init()
        return self.db.fail_jobs_by_status(
            ["downloading", "transcribing"],
            error_text="killed from worker popup",
        )

    def kill_next_active_job(self) -> dict[str, object] | None:
        self.init()
        return self.db.fail_oldest_job_by_status(
            ["downloading", "transcribing"],
            error_text="killed from workflow popup",
        )

    def kill_next_active_job_for_stage(self, stage: str) -> dict[str, object] | None:
        self.init()
        return self.db.fail_oldest_running_job_for_stage(
            stage,
            error_text="killed from workflow popup",
        )

    def kill_job(self, job_id: int) -> bool:
        self.init()
        with self._summary_state_lock:
            self._active_summary_jobs.pop(job_id, None)
        return self.db.fail_job(job_id, error_text="killed from workflow popup")

    def kill_oldest_summary_job(self) -> dict[str, object] | None:
        self.init()
        with self._summary_state_lock:
            rows = sorted(
                self._active_summary_jobs.values(),
                key=lambda row: float(row.get("started_monotonic") or 0.0),
            )
            if not rows:
                return None
            payload = dict(rows[0])
            job_id = int(payload.get("job_id") or 0)
            self._active_summary_jobs.pop(job_id, None)
        if job_id > 0:
            self.db.fail_job(job_id, error_text="killed from workflow popup")
        return payload

    def list_channel_videos(self, channel_ref: str, *, limit: int = 30) -> dict[str, object]:
        return list_channel_videos(self.config, channel_ref, limit=limit)

    def add_channel_subscription(
        self,
        channel_ref: str,
        *,
        seed_with_latest: bool = True,
        auto_transcribe: bool | None = None,
    ) -> dict[str, object]:
        self.init()
        listing = self.list_channel_videos(channel_ref, limit=1)
        channel_id = str(listing.get("channel_id") or "").strip()
        if not channel_id:
            raise PipelineError("Could not resolve channel_id for subscription")
        feed_url = channel_feed_url_from_channel_id(channel_id)
        entries = fetch_youtube_rss_feed(feed_url)
        last_seen = entries[0]["video_id"] if seed_with_latest and entries else None
        channel_title = str(listing.get("channel") or channel_id)
        sub_id = self.db.upsert_channel_subscription(
            channel_key=channel_id,
            source_ref=str(listing.get("source") or channel_ref),
            feed_url=feed_url,
            channel_title=channel_title,
            active=True,
            auto_transcribe=auto_transcribe,
            last_seen_video_id=last_seen,
        )
        return {
            "id": sub_id,
            "channel_key": channel_id,
            "channel_title": channel_title,
            "feed_url": feed_url,
            "last_seen_video_id": last_seen,
        }

    def list_channel_subscriptions(self, *, active_only: bool = False) -> list[dict[str, object]]:
        self.init()
        return self.db.list_channel_subscriptions(active_only=active_only)

    def remove_channel_subscription(self, channel_key: str) -> int:
        self.init()
        return self.db.remove_channel_subscription(channel_key=channel_key)

    def update_channel_subscription(
        self,
        channel_key: str,
        *,
        active: bool | None = None,
        auto_transcribe: bool | None = None,
        clear_auto_transcribe: bool = False,
    ) -> int:
        self.init()
        if clear_auto_transcribe:
            return self.db.clear_channel_subscription_auto_transcribe(channel_key=channel_key)
        return self.db.update_channel_subscription(
            channel_key=channel_key,
            active=active,
            auto_transcribe=auto_transcribe,
        )

    def poll_subscriptions_once(self) -> dict[str, object]:
        self.init()
        subs = self.db.list_channel_subscriptions(active_only=True)
        scanned = 0
        queued = 0
        errors: list[dict[str, object]] = []
        for sub in subs:
            scanned += 1
            channel_key = str(sub.get("channel_key") or "")
            feed_url = str(sub.get("feed_url") or "")
            last_seen = str(sub.get("last_seen_video_id") or "").strip()
            sub_auto_raw = sub.get("auto_transcribe")
            sub_auto = (
                self.auto_transcribe_default
                if sub_auto_raw is None
                else bool(int(sub_auto_raw))
            )
            try:
                entries = fetch_youtube_rss_feed(feed_url)
            except Exception as exc:
                errors.append({"channel_key": channel_key, "error": str(exc)})
                self.db.update_subscription_poll_state(channel_key=channel_key, last_seen_video_id=None)
                continue

            newest_seen: str | None = entries[0]["video_id"] if entries else None
            new_urls: list[str] = []
            for row in entries:
                video_id = str(row.get("video_id") or "").strip()
                if not video_id:
                    continue
                if last_seen and video_id == last_seen:
                    break
                if self.db.get_video(video_id):
                    continue
                new_urls.append(str(row.get("url") or f"https://www.youtube.com/watch?v={video_id}"))
            if new_urls:
                if self.subscription_db_max_videos > 0 and self.db.count_videos() >= self.subscription_db_max_videos:
                    errors.append(
                        {
                            "channel_key": channel_key,
                            "error": (
                                "subscription capacity reached "
                                f"({self.db.count_videos()}/{self.subscription_db_max_videos})"
                            ),
                        }
                    )
                else:
                    result = self.enqueue_with_dedupe(
                        new_urls,
                        allow_overwrite=False,
                        auto_transcribe=sub_auto,
                    )
                    queued += len(list(result.get("queued_ids") or []))
            self.db.update_subscription_poll_state(
                channel_key=channel_key,
                last_seen_video_id=newest_seen,
            )
        return {"scanned": scanned, "queued": queued, "errors": errors}

    def delete_video_and_assets(self, video_id: str) -> dict[str, object]:
        self.init()
        assets = self.db.list_video_asset_paths(video_id)
        media_candidates = {Path(p) for p in assets.get("media_paths", []) if p}
        transcript_candidates = {Path(p) for p in assets.get("transcript_paths", []) if p}
        media_candidates.update(self.config.media_dir.glob(f"{video_id}*"))
        transcript_dir = self.config.transcript_dir / video_id
        deleted_files = 0
        missing_files = 0
        for path in sorted(media_candidates):
            if path.exists() and path.is_file():
                try:
                    path.unlink()
                    deleted_files += 1
                except Exception:
                    pass
            else:
                missing_files += 1
        for path in sorted(transcript_candidates):
            if path.exists() and path.is_file():
                try:
                    path.unlink()
                    deleted_files += 1
                except Exception:
                    pass
            else:
                missing_files += 1
        if transcript_dir.exists() and transcript_dir.is_dir():
            for child in transcript_dir.glob("*"):
                if child.is_file():
                    try:
                        child.unlink()
                        deleted_files += 1
                    except Exception:
                        pass
            try:
                transcript_dir.rmdir()
            except Exception:
                pass

        db_counts = self.db.delete_video_records(video_id)
        return {
            "video_id": video_id,
            "deleted_files": deleted_files,
            "missing_files": missing_files,
            **db_counts,
        }

    def backfill_merge_playback_paths(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        self.init()
        rows = self.db.list_latest_done_jobs(limit=limit)
        scanned = 0
        updated = 0
        skipped = 0
        failed = 0

        for row in rows:
            scanned += 1
            job_id = int(row.get("id") or 0)
            video_id = str(row.get("video_id") or "")
            current_path = str(row.get("local_video_path") or "")
            payload = {"job_id": job_id, "video_id": video_id, "local_video_path": current_path}
            try:
                merged = merge_streams_for_playback(self.config, video_id=video_id)
                if merged is None:
                    skipped += 1
                    if progress_cb:
                        progress_cb("skip_no_merge_candidate", payload)
                    continue
                merged_str = str(merged)
                has_av = _media_has_video_stream(merged) is True and _media_has_audio_stream(merged) is True
                if not has_av:
                    skipped += 1
                    if progress_cb:
                        progress_cb("skip_not_av", {**payload, "resolved_path": merged_str})
                    continue
                if current_path == merged_str:
                    skipped += 1
                    if progress_cb:
                        progress_cb("skip_already_set", {**payload, "resolved_path": merged_str})
                    continue
                if not dry_run:
                    self.db.update_job_local_video_path(job_id, merged_str)
                updated += 1
                if progress_cb:
                    progress_cb(
                        "updated" if not dry_run else "would_update",
                        {**payload, "resolved_path": merged_str},
                    )
            except Exception as exc:
                failed += 1
                if progress_cb:
                    progress_cb("failed", {**payload, "error": str(exc)})

        return {
            "scanned": scanned,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "dry_run": dry_run,
        }

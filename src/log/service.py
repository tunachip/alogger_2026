from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

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
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._subscription_thread: threading.Thread | None = None

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
        return self.db.enqueue(urls, priority=priority, auto_transcribe=auto_transcribe)

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
            ids = self.db.enqueue([url], priority=priority, auto_transcribe=auto_transcribe)
            queued_ids.extend(ids)
        return {"queued_ids": queued_ids, "conflicts": conflicts}

    def set_runtime_options(
        self,
        *,
        auto_transcribe_default: bool | None = None,
        subscription_db_max_videos: int | None = None,
    ) -> None:
        if auto_transcribe_default is not None:
            self.auto_transcribe_default = bool(auto_transcribe_default)
        if subscription_db_max_videos is not None:
            self.subscription_db_max_videos = max(0, int(subscription_db_max_videos))

    def run_forever(self) -> None:
        self.init()
        self._stop_event.clear()
        self._start_subscription_poller()
        with ThreadPoolExecutor(max_workers=self.config.worker_count) as executor:
            futures = [executor.submit(self._worker_loop, i) for i in range(self.config.worker_count)]
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

    def process_job_id_with_progress(
        self,
        job_id: int,
        *,
        worker_id: int = 0,
        progress_cb: Callable[[str, dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        self.init()
        job = self.db.reserve_job_by_id(job_id)
        if not job:
            row = self.db.get_job(job_id)
            if row:
                return {"processed": False, "reason": "job_not_queued", "job": row}
            return {"processed": False, "reason": "job_not_found", "job_id": job_id}
        try:
            self._process_job(job, worker_id, progress_cb=progress_cb)
        except Exception as exc:
            self.db.update_job_status(job.id, "failed", error_text=str(exc))
            self._notify("failed", job_id=job.id, url=job.url, error=str(exc), worker_id=worker_id)
            if progress_cb:
                progress_cb(
                    "failed",
                    {
                        "job_id": job.id,
                        "url": job.url,
                        "worker_id": worker_id,
                        "error": str(exc),
                    },
                )
        row = self.db.get_job(job.id)
        return {"processed": True, "job": row if row else {"id": job.id}}

    def stop(self) -> None:
        self._stop_event.set()
        self.stop_background_workers()

    def start_background_workers(self, worker_count: int) -> None:
        self.init()
        if worker_count <= 0:
            return
        if self._worker_threads:
            return
        self._stop_event.clear()
        self._worker_threads = [
            threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            for i in range(worker_count)
        ]
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

    def _process_job(
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

        if progress_cb:
            progress_cb("download_start", {"job_id": job.id, "video_id": str(video_id)})
        local_video_path = download_video(self.config, job.url, video_id)
        if progress_cb:
            progress_cb(
                "download_done",
                {"job_id": job.id, "video_id": str(video_id), "local_video_path": str(local_video_path)},
            )
        self.db.update_job_status(
            job.id,
            "transcribing",
            video_id=video_id,
            local_video_path=str(local_video_path),
        )
        should_transcribe = (
            self.auto_transcribe_default
            if job.auto_transcribe is None
            else bool(int(job.auto_transcribe))
        )
        transcript_json_path: Path | None = None
        if should_transcribe:
            if progress_cb:
                progress_cb(
                    "transcribe_start",
                    {"job_id": job.id, "video_id": str(video_id), "local_video_path": str(local_video_path)},
                )
            transcript_json_path = transcribe_video(self.config, local_video_path, video_id)
            if progress_cb:
                progress_cb(
                    "transcribe_done",
                    {
                        "job_id": job.id,
                        "video_id": str(video_id),
                        "transcript_json_path": str(transcript_json_path),
                    },
                )
                progress_cb("index_start", {"job_id": job.id, "video_id": str(video_id)})
            segments = load_whisper_segments(transcript_json_path)
            self.db.replace_transcript_segments(video_id=video_id, segments=segments)
            if progress_cb:
                progress_cb(
                    "index_done",
                    {"job_id": job.id, "video_id": str(video_id), "segment_count": len(segments)},
                )
                progress_cb("merge_start", {"job_id": job.id, "video_id": str(video_id)})
        elif progress_cb:
            progress_cb("transcribe_skipped", {"job_id": job.id, "video_id": str(video_id)})
            progress_cb("merge_start", {"job_id": job.id, "video_id": str(video_id)})

        playback_path = merge_streams_for_playback(self.config, video_id=video_id)
        final_media_path = playback_path if playback_path is not None else local_video_path
        if progress_cb:
            progress_cb(
                "merge_done",
                {
                    "job_id": job.id,
                    "video_id": str(video_id),
                    "local_video_path": str(final_media_path),
                },
            )

        self.db.update_job_status(
            job.id,
            "done",
            video_id=video_id,
            local_video_path=str(final_media_path),
            transcript_json_path=(str(transcript_json_path) if transcript_json_path else None),
        )
        if progress_cb:
            progress_cb(
                "done",
                {
                    "job_id": job.id,
                    "video_id": str(video_id),
                    "local_video_path": str(final_media_path),
                    "transcript_json_path": (str(transcript_json_path) if transcript_json_path else None),
                },
            )
        self._notify(
            "done",
            job_id=job.id,
            url=job.url,
            video_id=video_id,
            transcript_json_path=(str(transcript_json_path) if transcript_json_path else None),
            worker_id=worker_id,
            transcribed=should_transcribe,
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

    def jobs_summary(self, limit: int = 25) -> dict[str, object]:
        return self.db.list_jobs_summary(limit=limit)

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

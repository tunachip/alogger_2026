from __future__ import annotations

import curses
import os
import shutil
import signal
import threading
import time
from pathlib import Path
from typing import Any

from .db import Job
from .pipeline import (
    PipelineError,
    download_video,
    fetch_video_metadata,
    load_whisper_segments,
    transcribe_video,
)
from .service import IngesterService


def rough_progress_pct(status: str, stage: str | None) -> str:
    if status == "idle":
        return "-"
    if status == "paused":
        return "paused"
    if status == "failed":
        return "failed"

    if status == "transcribing":
        return "80%"
    if status == "downloading":
        if stage == "metadata":
            return "5%"
        if stage == "download":
            return "35%"
        return "25%"
    if stage == "index":
        return "95%"
    return "10%"


def truncate_url(url: str, width: int) -> str:
    if len(url) <= width:
        return url
    if width <= 3:
        return url[:width]
    return url[: width - 3] + "..."


def _safe_add(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = stdscr.getmaxyx()
    if y >= h or x >= w:
        return
    stdscr.addnstr(y, x, text, max(0, w - x - 1), attr)


class WorkerRuntime:
    def __init__(self, worker_id: int, service: IngesterService) -> None:
        self.worker_id = worker_id
        self.service = service

        self._stop = False
        self._paused = False
        self._kill_requested = False
        self._delete_after_kill = False

        self._current_proc: Any = None
        self._proc_paused = False

        self.status = "idle"
        self.current_job_id: int | None = None
        self.current_url: str | None = None
        self.current_video_id: str | None = None
        self.current_stage: str | None = None
        self.local_video_path: str | None = None
        self.transcript_json_path: str | None = None
        self.last_error: str | None = None
        self.started_monotonic: float | None = None

        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop = True
            self._kill_requested = True
            proc = self._current_proc
        if proc and proc.poll() is None:
            proc.kill()
        self._thread.join(timeout=5)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = None
            if self.started_monotonic is not None:
                elapsed = max(0.0, time.monotonic() - self.started_monotonic)
            return {
                "worker_id": self.worker_id,
                "status": self.status,
                "job_id": self.current_job_id,
                "url": self.current_url,
                "video_id": self.current_video_id,
                "stage": self.current_stage,
                "elapsed_sec": elapsed,
                "last_error": self.last_error,
            }

    def toggle_pause(self) -> str:
        with self._lock:
            self._paused = not self._paused
            proc = self._current_proc
            paused_now = self._paused

        if proc and proc.poll() is None:
            try:
                if paused_now:
                    os.kill(proc.pid, signal.SIGSTOP)
                    with self._lock:
                        self._proc_paused = True
                else:
                    os.kill(proc.pid, signal.SIGCONT)
                    with self._lock:
                        self._proc_paused = False
            except OSError:
                pass

        return "paused" if paused_now else "resumed"

    def kill_active(self, *, delete_files: bool) -> bool:
        with self._lock:
            if self.current_job_id is None:
                return False
            self._kill_requested = True
            self._delete_after_kill = delete_files
            proc = self._current_proc

        if proc and proc.poll() is None:
            proc.kill()
        return True

    def is_empty(self) -> bool:
        with self._lock:
            return self.current_job_id is None

    def _loop(self) -> None:
        while True:
            if self._should_stop():
                return

            if self._is_paused_without_process():
                with self._lock:
                    self.status = "paused"
                time.sleep(self.service.config.poll_interval_sec)
                continue

            job = self.service.db.reserve_next_job()
            if not job:
                with self._lock:
                    self.status = "paused" if self._paused else "idle"
                time.sleep(self.service.config.poll_interval_sec)
                continue

            self._reset_for_job(job)
            try:
                self._process_job(job)
                self.service._notify(
                    "done",
                    job_id=job.id,
                    url=job.url,
                    video_id=self.current_video_id,
                    transcript_json_path=self.transcript_json_path,
                    worker_id=self.worker_id,
                )
            except Exception as exc:
                user_killed = self._consume_kill_requested()
                err = "killed by user" if user_killed else str(exc)
                self.service.db.update_job_status(job.id, "failed", error_text=err)
                self._maybe_cleanup_partial()
                with self._lock:
                    self.last_error = err
                    self.status = "failed"
                self.service._notify(
                    "failed",
                    job_id=job.id,
                    url=job.url,
                    error=err,
                    worker_id=self.worker_id,
                )
            finally:
                with self._lock:
                    self.current_job_id = None
                    self.current_url = None
                    self.current_stage = None
                    self.current_video_id = None
                    self.local_video_path = None
                    self.transcript_json_path = None
                    self.started_monotonic = None
                    if self.status != "paused":
                        self.status = "idle"

    def _process_job(self, job: Job) -> None:
        with self._lock:
            self.status = "downloading"
            self.current_stage = "metadata"

        metadata = fetch_video_metadata(
            self.service.config,
            job.url,
            on_process=self._register_process,
            should_terminate=self._kill_pending,
        )
        self._clear_process()
        video_id = metadata.get("id")
        if not video_id:
            raise PipelineError("yt-dlp metadata did not include video id")

        with self._lock:
            self.current_video_id = str(video_id)
            self.current_stage = "download"

        self.service.db.upsert_video(video_id=video_id, source_url=job.url, metadata=metadata)

        local_video_path = download_video(
            self.service.config,
            job.url,
            video_id,
            on_process=self._register_process,
            should_terminate=self._kill_pending,
        )
        self._clear_process()
        with self._lock:
            self.local_video_path = str(local_video_path)
            self.status = "transcribing"
            self.current_stage = "transcribe"

        self.service.db.update_job_status(
            job.id,
            "transcribing",
            video_id=video_id,
            local_video_path=str(local_video_path),
        )

        transcript_json_path = transcribe_video(
            self.service.config,
            local_video_path,
            video_id,
            on_process=self._register_process,
            should_terminate=self._kill_pending,
        )
        self._clear_process()
        with self._lock:
            self.transcript_json_path = str(transcript_json_path)
            self.current_stage = "index"

        segments = load_whisper_segments(transcript_json_path)
        self.service.db.replace_transcript_segments(video_id=video_id, segments=segments)

        self.service.db.update_job_status(
            job.id,
            "done",
            video_id=video_id,
            local_video_path=str(local_video_path),
            transcript_json_path=str(transcript_json_path),
        )

    def _register_process(self, proc: Any) -> None:
        with self._lock:
            self._current_proc = proc
            if self._paused and proc.poll() is None:
                try:
                    os.kill(proc.pid, signal.SIGSTOP)
                    self._proc_paused = True
                except OSError:
                    self._proc_paused = False
            else:
                self._proc_paused = False

    def _clear_process(self) -> None:
        with self._lock:
            self._current_proc = None
            self._proc_paused = False

    def _kill_pending(self) -> bool:
        with self._lock:
            return self._kill_requested

    def _consume_kill_requested(self) -> bool:
        with self._lock:
            was_set = self._kill_requested
            self._kill_requested = False
            return was_set

    def _is_paused_without_process(self) -> bool:
        with self._lock:
            return self._paused and self._current_proc is None

    def _should_stop(self) -> bool:
        with self._lock:
            return self._stop

    def _reset_for_job(self, job: Job) -> None:
        with self._lock:
            self.status = "downloading"
            self.current_job_id = job.id
            self.current_url = job.url
            self.current_video_id = None
            self.current_stage = "metadata"
            self.local_video_path = None
            self.transcript_json_path = None
            self.last_error = None
            self.started_monotonic = time.monotonic()
            self._kill_requested = False
            self._delete_after_kill = False

    def _maybe_cleanup_partial(self) -> None:
        with self._lock:
            should_delete = self._delete_after_kill
            video_path = self.local_video_path
            transcript_json_path = self.transcript_json_path
            video_id = self.current_video_id
            self._delete_after_kill = False
            self._current_proc = None
            self._proc_paused = False

        if not should_delete:
            return

        if video_path:
            path = Path(video_path)
            if path.exists():
                path.unlink(missing_ok=True)

        if transcript_json_path:
            t_path = Path(transcript_json_path)
            if t_path.parent.exists():
                shutil.rmtree(t_path.parent, ignore_errors=True)
        elif video_id:
            t_dir = self.service.config.transcript_dir / video_id
            if t_dir.exists():
                shutil.rmtree(t_dir, ignore_errors=True)


class WorkerPool:
    def __init__(self, service: IngesterService, worker_count: int) -> None:
        self.workers = [WorkerRuntime(i, service) for i in range(worker_count)]

    def start(self) -> None:
        for worker in self.workers:
            worker.start()

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()


def _prompt_input(stdscr: curses.window, prompt: str) -> str:
    curses.echo()
    curses.curs_set(1)
    h, _w = stdscr.getmaxyx()
    y = max(0, h - 2)
    stdscr.move(y, 0)
    stdscr.clrtoeol()
    stdscr.addstr(y, 0, prompt)
    stdscr.refresh()
    raw = stdscr.getstr(y, len(prompt), 512)
    curses.noecho()
    curses.curs_set(0)
    return raw.decode("utf-8", errors="ignore").strip()


def _prompt_yes_no(stdscr: curses.window, prompt: str) -> bool:
    while True:
        value = _prompt_input(stdscr, f"{prompt} [y/n]: ").lower()
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False


def run_tui(service: IngesterService, refresh_sec: float, worker_count: int) -> None:
    service.init()
    curses.wrapper(_loop, service, refresh_sec, worker_count)


def _loop(
    stdscr: curses.window,
    service: IngesterService,
    refresh_sec: float,
    worker_count: int,
) -> None:
    curses.curs_set(0)
    stdscr.nodelay(False)
    pool = WorkerPool(service, worker_count)
    pool.start()

    selected = 0
    d_pending_at: float | None = None
    status_msg = "Ready"

    try:
        while True:
            snapshot = service.dashboard_snapshot()
            counts = snapshot["counts"]

            worker_snaps = [w.snapshot() for w in pool.workers]
            selected = max(0, min(selected, len(worker_snaps) - 1))
            has_active = any(w["job_id"] is not None for w in worker_snaps)

            stdscr.erase()
            _safe_add(stdscr, 0, 0, "Alogger Ingester TUI", curses.A_BOLD)
            _safe_add(
                stdscr,
                1,
                0,
                f"workers={worker_count} | mode={'active-refresh' if has_active else 'input-refresh'}",
            )
            _safe_add(stdscr, 2, 0, "Keys: j/k move, Enter add URL on empty worker, Space pause/resume, dd kill, q quit")

            _safe_add(
                stdscr,
                4,
                0,
                (
                    "Jobs "
                    f"queued={counts['queued']} "
                    f"downloading={counts['downloading']} "
                    f"transcribing={counts['transcribing']} "
                    f"done={counts['done']} "
                    f"failed={counts['failed']}"
                ),
            )
            _safe_add(stdscr, 5, 0, f"Status: {status_msg}")

            _safe_add(stdscr, 7, 0, "Workers", curses.A_BOLD)
            _safe_add(stdscr, 8, 0, "ID  State         Stage       Job      Progress  URL")

            y = 9
            max_rows = max(0, stdscr.getmaxyx()[0] - y - 1)
            for i, ws in enumerate(worker_snaps[:max_rows]):
                line = (
                    f"{ws['worker_id']:<3} "
                    f"{str(ws['status']):<13} "
                    f"{str(ws['stage'] or '-'): <11} "
                    f"{str(ws['job_id'] or '-'): <8} "
                    f"{rough_progress_pct(str(ws['status']), ws['stage']):<9} "
                    f"{truncate_url(str(ws['url'] or '-'), 120)}"
                )
                attr = curses.A_REVERSE if i == selected else 0
                _safe_add(stdscr, y, 0, line, attr)
                y += 1

            stdscr.refresh()
            stdscr.timeout(int(refresh_sec * 1000) if has_active else -1)
            key = stdscr.getch()
            now_mono = time.monotonic()

            if d_pending_at is not None and now_mono - d_pending_at > 1.0:
                d_pending_at = None

            if key in (ord("q"), ord("Q")):
                break
            if key == ord("j"):
                selected = min(len(pool.workers) - 1, selected + 1)
                status_msg = f"Selected worker {selected}"
            elif key == ord("k"):
                selected = max(0, selected - 1)
                status_msg = f"Selected worker {selected}"
            elif key in (10, 13):
                worker = pool.workers[selected]
                if not worker.is_empty():
                    status_msg = "Selected worker is not empty"
                else:
                    url = _prompt_input(stdscr, "YouTube URL: ")
                    if url:
                        job_ids = service.enqueue([url])
                        status_msg = f"Enqueued job {job_ids[0]}"
                    else:
                        status_msg = "No URL entered"
            elif key == ord(" "):
                worker = pool.workers[selected]
                result = worker.toggle_pause()
                status_msg = f"Worker {selected} {result}"
            elif key == ord("d"):
                if d_pending_at is None:
                    d_pending_at = now_mono
                    status_msg = "Press d again to kill selected worker process"
                else:
                    d_pending_at = None
                    worker = pool.workers[selected]
                    if worker.is_empty():
                        status_msg = "No active job on selected worker"
                    else:
                        delete_files = _prompt_yes_no(stdscr, "Delete files produced so far?")
                        if worker.kill_active(delete_files=delete_files):
                            status_msg = f"Kill requested on worker {selected}"
                        else:
                            status_msg = "No active process to kill"
    finally:
        pool.stop()

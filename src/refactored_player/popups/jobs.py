from __future__ import annotations

import json
import time
import tkinter as tk
from typing import Any

from ..constants import FONT_SIZE_OFFSETS, ICONS, LISTBOX, POPUP_SIZES
from ..utils import format_hms as _fmt_hms


class JobsPopupMixin:
    def _restart_workers(
        self,
        target: int,
        downloaders: int | None = None,
        transcribers: int | None = None,
        summarizers: int | None = None,
    ) -> None:
        target = max(0, int(target))
        if downloaders is None:
            downloaders = self._downloader_target_count
        if transcribers is None:
            transcribers = self._transcriber_target_count
        if summarizers is None:
            summarizers = self._summarizer_target_count
        downloaders = max(0, int(downloaders))
        transcribers = max(0, int(transcribers))
        summarizers = max(0, int(summarizers))
        target = max(target, downloaders, transcribers, summarizers)
        self.ingester.stop_background_workers()
        if target > 0:
            self.ingester.start_background_workers(
                max(target, downloaders + transcribers + summarizers),
                downloader_count=downloaders,
                transcriber_count=transcribers,
                summarizer_count=summarizers,
            )
        self._worker_target_count = target
        self._downloader_target_count = downloaders
        self._transcriber_target_count = transcribers
        self._summarizer_target_count = summarizers
        self._workers_paused = False
        self._workers_eta_next_recalc_at = 0.0

    def _workflow_command_menu(self) -> list[str]:
        state = getattr(self, "_workflow_command_state", "root")
        if state == "add_worker":
            return ["Downloader", "Transcriber", "Summarizer", "Back"]
        if state == "remove_worker":
            return ["Downloader", "Transcriber", "Summarizer", "Back"]
        if state == "kill_one":
            return ["Oldest Download", "Oldest Transcribe", "Oldest Summary", "Back"]
        if state == "remove_one":
            return ["Next Download Queue", "Next Transcribe Queue", "Next Summary Queue", "Back"]
        if state == "confirm_kill_all":
            return ["Confirm Kill All Jobs", "Cancel"]
        if state == "confirm_clear_all":
            return ["Confirm Clear All Queues", "Cancel"]
        return [
            "Add Worker",
            "Remove Worker",
            "Inject Transcribe Queue",
            "Inject Summary Queue",
            "Kill One Job",
            "Remove One Queue Item",
            "Kill All Jobs",
            "Clear All Queues",
            "Poll Subscriptions",
        ]

    def _workflow_command_label(self) -> str:
        state = getattr(self, "_workflow_command_state", "root")
        mapping = {
            "root": "Commands",
            "add_worker": "Add Worker",
            "remove_worker": "Remove Worker",
            "kill_one": "Kill One Job",
            "remove_one": "Remove One Queue Item",
            "confirm_kill_all": "Confirm Kill All Jobs",
            "confirm_clear_all": "Confirm Clear All Queues",
        }
        return mapping.get(state, "Commands")

    def _run_worker_command(self, command: str) -> str:
        label = command.strip().lower()
        state = getattr(self, "_workflow_command_state", "root")
        if state == "root":
            if label == "add worker":
                self._workflow_command_state = "add_worker"
                return "Choose worker type to add"
            if label == "remove worker":
                self._workflow_command_state = "remove_worker"
                return "Choose worker type to remove"
            if label == "inject transcribe queue":
                self._open_queue_picker_popup("transcribe")
                return "Queue picker opened for transcribe"
            if label == "inject summary queue":
                self._open_queue_picker_popup("summarize")
                return "Queue picker opened for summarize"
            if label == "kill one job":
                self._workflow_command_state = "kill_one"
                return "Choose which single job to kill"
            if label == "remove one queue item":
                self._workflow_command_state = "remove_one"
                return "Choose which queued item to remove"
            if label == "kill all jobs":
                self._workflow_command_state = "confirm_kill_all"
                return "Confirm kill all active jobs"
            if label == "clear all queues":
                self._workflow_command_state = "confirm_clear_all"
                return "Confirm clear all queued items"
            if label == "poll subscriptions":
                try:
                    summary = self.ingester.poll_subscriptions_once()
                    scanned = int(summary.get("scanned", 0))
                    queued = int(summary.get("queued", 0))
                    return f"Poll: scanned={scanned} queued={queued}"
                except Exception as exc:
                    return f"Poll failed: {exc}"
            return "Unknown command"
        if label in {"back", "cancel"}:
            self._workflow_command_state = "root"
            return "Back to commands"
        if state == "add_worker":
            if label == "downloader":
                self._restart_workers(
                    self._worker_target_count,
                    self._downloader_target_count + 1,
                    self._transcriber_target_count,
                    self._summarizer_target_count,
                )
            elif label == "transcriber":
                self._restart_workers(
                    self._worker_target_count,
                    self._downloader_target_count,
                    self._transcriber_target_count + 1,
                    self._summarizer_target_count,
                )
            elif label == "summarizer":
                self._restart_workers(
                    self._worker_target_count,
                    self._downloader_target_count,
                    self._transcriber_target_count,
                    self._summarizer_target_count + 1,
                )
            self._workflow_command_state = "root"
            return (
                "Workers: "
                f"d={self._downloader_target_count} "
                f"t={self._transcriber_target_count} "
                f"s={self._summarizer_target_count}"
            )
        if state == "remove_worker":
            if label == "downloader":
                self._restart_workers(
                    self._worker_target_count,
                    max(0, self._downloader_target_count - 1),
                    self._transcriber_target_count,
                    self._summarizer_target_count,
                )
            elif label == "transcriber":
                self._restart_workers(
                    self._worker_target_count,
                    self._downloader_target_count,
                    max(0, self._transcriber_target_count - 1),
                    self._summarizer_target_count,
                )
            elif label == "summarizer":
                self._restart_workers(
                    self._worker_target_count,
                    self._downloader_target_count,
                    self._transcriber_target_count,
                    max(0, self._summarizer_target_count - 1),
                )
            self._workflow_command_state = "root"
            return (
                "Workers: "
                f"d={self._downloader_target_count} "
                f"t={self._transcriber_target_count} "
                f"s={self._summarizer_target_count}"
            )
        if state == "kill_one":
            try:
                if label == "oldest download":
                    row = self.ingester.kill_next_active_job_for_stage("download")
                    self._workflow_command_state = "root"
                    if not row:
                        return "No active download jobs"
                    return f"Killed job {row.get('id')}"
                if label == "oldest transcribe":
                    row = self.ingester.kill_next_active_job_for_stage("transcribe")
                    self._workflow_command_state = "root"
                    if not row:
                        return "No active transcribe jobs"
                    return f"Killed job {row.get('id')}"
                if label == "oldest summary":
                    row = self.ingester.kill_oldest_summary_job()
                    self._workflow_command_state = "root"
                    if not row:
                        return "No active summary jobs"
                    return f"Killed summary job {row.get('job_id')}"
            except Exception as exc:
                self._workflow_command_state = "root"
                return f"Kill failed: {exc}"
        if state == "remove_one":
            try:
                if label == "next download queue":
                    row = self.ingester.clear_next_queued_job_for_stage("download")
                    self._workflow_command_state = "root"
                    if not row:
                        return "No queued download jobs"
                    return f"Removed queued job {row.get('id')}"
                if label == "next transcribe queue":
                    row = self.ingester.clear_next_queued_job_for_stage("transcribe")
                    self._workflow_command_state = "root"
                    if not row:
                        return "No queued transcribe jobs"
                    return f"Removed queued job {row.get('id')}"
                if label == "next summary queue":
                    row = self.ingester.clear_next_queued_job_for_stage("summarize")
                    self._workflow_command_state = "root"
                    if not row:
                        return "No queued summary jobs"
                    return f"Removed queued job {row.get('id')}"
            except Exception as exc:
                self._workflow_command_state = "root"
                return f"Remove failed: {exc}"
        if state == "confirm_kill_all":
            self._workflow_command_state = "root"
            if label == "confirm kill all jobs":
                try:
                    n = int(self.ingester.kill_active_jobs())
                    return f"Killed active jobs: {n}"
                except Exception as exc:
                    return f"Kill failed: {exc}"
        if state == "confirm_clear_all":
            self._workflow_command_state = "root"
            if label == "confirm clear all queues":
                try:
                    n = int(self.ingester.clear_queue())
                    return f"Cleared queued jobs: {n}"
                except Exception as exc:
                    return f"Clear queue failed: {exc}"
        self._workflow_command_state = "root"
        return "Unknown command"

    def _recalculate_workers_eta(self) -> None:
        now = time.monotonic()
        self._workers_eta_last_tick_at = now
        self._workers_eta_next_recalc_at = now + self._workers_eta_recalc_interval_sec
        if self._workers_paused:
            self._workers_eta_remaining_sec = None
            return
        try:
            snapshot = self.ingester.dashboard_snapshot()
            counts = snapshot.get("counts", {})
            pending = (
                int(counts.get("queued", 0))
                + int(counts.get("downloading", 0))
                + int(counts.get("transcribing", 0))
                + int(counts.get("summarizing", 0))
            )
            if pending <= 0:
                self._workers_eta_remaining_sec = 0.0
                return
            avg = snapshot.get("median_duration_sec")
            if avg is None:
                avg = snapshot.get("avg_duration_sec")
            avg_sec = max(30.0, float(avg) if avg is not None else 300.0)
            workers = max(
                1,
                int(
                    (
                        self._downloader_target_count
                        + self._transcriber_target_count
                        + self._summarizer_target_count
                    )
                    or self._worker_target_count
                    or 1
                ),
            )
            self._workers_eta_remaining_sec = float(pending) * avg_sec / float(workers)
        except Exception:
            if self._workers_eta_remaining_sec is None:
                self._workers_eta_remaining_sec = 0.0

    def _tick_workers_eta_countdown(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._workers_eta_last_tick_at)
        self._workers_eta_last_tick_at = now
        if self._workers_eta_remaining_sec is None:
            return
        self._workers_eta_remaining_sec = max(0.0, self._workers_eta_remaining_sec - elapsed)

    def _workers_eta_line(self) -> str:
        now = time.monotonic()
        if now >= self._workers_eta_next_recalc_at:
            self._recalculate_workers_eta()
        self._tick_workers_eta_countdown()
        recalc_in = max(0, int(self._workers_eta_next_recalc_at - time.monotonic()))
        if self._workers_paused:
            return f"eta=paused  recalc={_fmt_hms(recalc_in)}"
        if self._workers_eta_remaining_sec is None:
            return f"eta=unknown  recalc={_fmt_hms(recalc_in)}"
        return f"eta~{_fmt_hms(self._workers_eta_remaining_sec)}  recalc={_fmt_hms(recalc_in)}"

    def _stage_progress_bar(self, fill_ratio: float, *, width: int = 10) -> str:
        clamped = max(0.0, min(1.0, fill_ratio))
        filled = min(width, max(0, int(round(clamped * width))))
        return "[" + ("#" * filled) + ("." * (width - filled)) + "]"

    def _workflow_label(self, row: dict[str, Any], *, prefer: tuple[str, ...], width: int = 28) -> str:
        text = ""
        for key in prefer:
            value = str(row.get(key) or "").strip()
            if value:
                text = value
                break
        if not text:
            text = str(row.get("id") or row.get("job_id") or row.get("video_id") or "-")
        text = text.replace("\n", " ").strip()
        if len(text) > width:
            text = text[: max(0, width - 1)] + "…"
        return text.ljust(width)

    def _metadata_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        raw = str(row.get("metadata_json") or "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _summary_backlog_rows(self, limit: int = 8) -> list[dict[str, Any]]:
        try:
            rows = [dict(row) for row in self.ingester.search_video_metadata("", limit=max(20, limit * 3))]
        except Exception:
            return []
        pending: list[dict[str, Any]] = []
        for row in rows:
            if not str(row.get("transcript_json_path") or "").strip():
                continue
            payload = self._metadata_payload(row)
            if str(payload.get("summary") or "").strip():
                continue
            pending.append(row)
            if len(pending) >= limit:
                break
        return pending

    def _render_stage_text(
        self,
        widget: tk.Text,
        *,
        queue_rows: list[dict[str, Any]],
        worker_rows: list[dict[str, Any]],
        worker_count: int,
        state: str,
    ) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.tag_configure("queue_head", foreground=self._theme_color("FG_INFO"))
        widget.tag_configure("workers_head", foreground=self._theme_color("FG_ACCENT"))
        widget.tag_configure("progress_head", foreground=self._theme_color("FG_MUTED"))
        widget.tag_configure("meta", foreground=self._theme_color("FG_SOFT"))
        widget.tag_configure("idle", foreground=self._theme_color("FG_LOG"))
        widget.tag_configure("active", foreground=self._theme_color("FG_ACCENT"))
        widget.tag_configure("error", foreground=self._theme_color("FG_ERROR"))
        widget.tag_configure("done", foreground=self._theme_color("FG_INFO"))

        widget.insert(tk.END, "queue".ljust(31), ("queue_head",))
        widget.insert(tk.END, "workers".ljust(12), ("workers_head",))
        widget.insert(tk.END, "progress\n", ("progress_head",))

        row_count = max(3, len(queue_rows), len(worker_rows))
        for idx in range(row_count):
            queue_label = (
                self._workflow_label(queue_rows[idx], prefer=("title", "video_id", "source_url"))
                if idx < len(queue_rows)
                else "".ljust(28)
            )
            widget.insert(tk.END, f"{queue_label} ", ("meta",))
            if idx < len(worker_rows):
                worker = worker_rows[idx]
                tag = str(worker.get("tag") or "idle")
                widget.insert(tk.END, f"{ICONS['WORKER']} ".ljust(12), (tag,))
                widget.insert(tk.END, str(worker.get("progress") or self._stage_progress_bar(0.0)), (tag,))
                suffix = str(worker.get("suffix") or "")
                if suffix:
                    widget.insert(tk.END, f" {suffix}", ("meta",))
            else:
                widget.insert(tk.END, "".ljust(12), ("idle",))
                widget.insert(tk.END, self._stage_progress_bar(0.0), ("idle",))
            widget.insert(tk.END, "\n")
        widget.insert(tk.END, f"\nworkers={worker_count}  {state}", ("meta",))
        widget.configure(state="disabled")

    def _open_jobs_popup(self) -> None:
        self._workflow_command_state = "root"
        popup = self._create_popup_window(
            name="workers",
            title="Workflows",
            size=POPUP_SIZES["WORKERS"],
            attr_name="_jobs_popup",
            row_weights={0: 1, 1: 0},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)
        content.columnconfigure(0, weight=4)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=self._theme_color("APP_BG"))
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=(8, 6))
        left.columnconfigure(0, weight=1)
        for idx in range(4):
            left.rowconfigure(idx * 2 + 1, weight=1)

        panels: dict[str, tk.Text] = {}
        for idx, title in enumerate(("DOWNLOAD", "TRANSCRIBE", "SUMMARIZE", "FINISHED")):
            head = tk.Label(
                left,
                text=title,
                anchor="w",
                bg=self._theme_color("SURFACE_BG"),
                fg=self._theme_color("FG_MUTED"),
                font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                padx=8,
                pady=4,
            )
            head.grid(row=idx * 2, column=0, sticky="ew", pady=(0 if idx == 0 else 4, 2))
            body = tk.Text(
                left,
                bg=self._theme_color("PANEL_BG"),
                fg=self._theme_color("FG"),
                borderwidth=0,
                highlightthickness=0,
                font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                wrap="none",
                padx=8,
                pady=8,
            )
            body.grid(row=idx * 2 + 1, column=0, sticky="nsew", pady=(0, 4))
            body.configure(state="disabled")
            panels[title.lower()] = body

        right = tk.Frame(content, bg=self._theme_color("APP_BG"))
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(8, 6))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._jobs_queue_list = tk.Label(
            right,
            text=self._workflow_command_label(),
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        self._jobs_queue_list.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        cmd_list = self._create_listbox(right, font_delta=FONT_SIZE_OFFSETS["BODY"])
        cmd_list.configure(height=LISTBOX["COMMAND_HEIGHT"])
        cmd_list.grid(row=1, column=0, sticky="nsew")
        self._workers_cmd_list = cmd_list

        self._jobs_dl_text = panels["download"]
        self._jobs_tr_text = panels["transcribe"]
        self._jobs_text = panels["summarize"]
        self._jobs_done_list = panels["finished"]

        status_var = tk.StringVar(value=self._status_hint("jobs"))
        status_lbl = self._create_status_label(
            content,
            status_var,
            font_delta=FONT_SIZE_OFFSETS["SMALL"],
        )
        status_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

        def render_commands() -> None:
            commands = self._workflow_command_menu()
            if self._jobs_queue_list and self._jobs_queue_list.winfo_exists():
                self._jobs_queue_list.configure(text=self._workflow_command_label())
            cmd_list.delete(0, tk.END)
            for cmd in commands:
                cmd_list.insert(tk.END, cmd)
            if commands:
                cmd_list.selection_set(0)
                cmd_list.activate(0)

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = cmd_list.curselection()
            if not sel:
                return "break"
            label = str(cmd_list.get(sel[0]))
            status_var.set(self._run_worker_command(label))
            render_commands()
            self._refresh_jobs_popup()
            return "break"

        def move(delta: int) -> str:
            commands = self._workflow_command_menu()
            sel = cmd_list.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, max(0, len(commands) - 1)))
            cmd_list.selection_clear(0, tk.END)
            cmd_list.selection_set(nxt)
            cmd_list.activate(nxt)
            cmd_list.see(nxt)
            return "break"

        render_commands()
        self._bind_popup_close(popup, after_close=self._close_jobs_popup, focus_filter=False)
        popup.bind("<Up>", lambda _e: move(-1))
        popup.bind("<Down>", lambda _e: move(1))
        popup.bind("<Return>", run_selected)
        cmd_list.bind("<Double-Button-1>", run_selected)
        self._refresh_jobs_popup()

    def _close_jobs_popup(self) -> None:
        if self._jobs_after_id:
            try:
                self.root.after_cancel(self._jobs_after_id)
            except Exception:
                pass
            self._jobs_after_id = None
        if self._jobs_popup and self._jobs_popup.winfo_exists():
            self._jobs_popup.destroy()
        self._jobs_popup = None
        self._jobs_text = None
        self._workers_cmd_list = None
        self._jobs_queue_list = None
        self._jobs_done_list = None
        self._jobs_dl_text = None
        self._jobs_tr_text = None
        self._jobs_agent_text = None
        self._workflow_command_state = "root"
        self.filter_entry.focus_set()

    def _refresh_jobs_popup(self) -> None:
        if not self._jobs_popup or not self._jobs_popup.winfo_exists():
            return
        try:
            snapshot = self.ingester.jobs_summary(limit=30)
            dash = self.ingester.dashboard_snapshot()
            counts = dict(snapshot.get("counts", {}) or {})
            stage_counts = dict(dash.get("stage_counts", {}) or {})
            jobs = [dict(row) for row in (snapshot.get("jobs", []) or [])]
            active_jobs = [dict(row) for row in (dash.get("active_jobs", []) or [])]
            active_summary = [dict(row) for row in (dash.get("active_summary_jobs", []) or [])]

            download_queue = [
                row for row in jobs
                if str(row.get("status")) == "queued"
                and str(row.get("queue_stage") or "download") == "download"
            ]
            transcribe_queue = [
                row for row in jobs
                if str(row.get("status")) == "queued"
                and str(row.get("queue_stage") or "download") == "transcribe"
            ]
            summary_queue = [
                row for row in jobs
                if str(row.get("status")) == "queued"
                and str(row.get("queue_stage") or "download") == "summarize"
            ]
            finished_jobs = [row for row in jobs if str(row.get("status")) in {"done", "failed"}]
            download_active = [
                row for row in active_jobs
                if str(row.get("status")) == "downloading"
                and str(row.get("queue_stage") or "download") == "download"
            ]
            transcribe_active = [
                row for row in active_jobs
                if str(row.get("status")) == "transcribing"
                and str(row.get("queue_stage") or "download") == "transcribe"
            ]

            download_workers: list[dict[str, Any]] = []
            for idx in range(max(1, self._downloader_target_count)):
                if idx < len(download_active):
                    row = download_active[idx]
                    elapsed = float(row.get("elapsed_sec") or 0.0)
                    download_workers.append(
                        {"tag": "active", "progress": self._stage_progress_bar(min(1.0, elapsed / 120.0)), "suffix": ""}
                    )
                else:
                    download_workers.append(
                        {"tag": "idle", "progress": self._stage_progress_bar(0.0), "suffix": ""}
                    )

            transcribe_workers: list[dict[str, Any]] = []
            for idx in range(max(1, self._transcriber_target_count)):
                if idx < len(transcribe_active):
                    row = transcribe_active[idx]
                    elapsed = float(row.get("elapsed_sec") or 0.0)
                    transcribe_workers.append(
                        {"tag": "active", "progress": self._stage_progress_bar(min(1.0, elapsed / 300.0)), "suffix": ""}
                    )
                else:
                    transcribe_workers.append(
                        {"tag": "idle", "progress": self._stage_progress_bar(0.0), "suffix": ""}
                    )

            summary_enabled = bool(self._ai_settings.get("auto_summary_default", False))
            summary_lane_active = (
                summary_enabled
                or bool(summary_queue)
                or bool(active_summary)
                or self._summarizer_target_count > 0
            )
            summary_backlog = self._summary_backlog_rows(limit=8) if summary_lane_active else []
            summary_workers: list[dict[str, Any]] = []
            summary_slots = max(1, self._summarizer_target_count if summary_lane_active else 1)
            for idx in range(summary_slots):
                if idx < len(active_summary):
                    row = active_summary[idx]
                    elapsed = float(row.get("elapsed_sec") or 0.0)
                    summary_workers.append(
                        {"tag": "active", "progress": self._stage_progress_bar(min(1.0, elapsed / 90.0)), "suffix": ""}
                    )
                else:
                    summary_workers.append(
                        {
                            "tag": "idle" if summary_lane_active else "error",
                            "progress": self._stage_progress_bar(0.0),
                            "suffix": "" if summary_lane_active else "disabled",
                        }
                    )

            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._render_stage_text(
                    self._jobs_dl_text,
                    queue_rows=download_queue[:8],
                    worker_rows=download_workers,
                    worker_count=self._downloader_target_count,
                    state=f"{self._workers_eta_line()} queued={stage_counts.get('download_queued', 0)}",
                )
            if self._jobs_tr_text and self._jobs_tr_text.winfo_exists():
                self._render_stage_text(
                    self._jobs_tr_text,
                    queue_rows=transcribe_queue[:8],
                    worker_rows=transcribe_workers,
                    worker_count=self._transcriber_target_count,
                    state=f"queued={stage_counts.get('transcribe_queued', 0)} active={len(transcribe_active)}",
                )
            if self._jobs_text and self._jobs_text.winfo_exists():
                self._render_stage_text(
                    self._jobs_text,
                    queue_rows=summary_queue[:8] if summary_queue else summary_backlog,
                    worker_rows=summary_workers,
                    worker_count=(self._summarizer_target_count if summary_lane_active else 0),
                    state=(
                        f"queued={stage_counts.get('summarize_queued', 0)}"
                        if summary_lane_active else "disabled"
                    ),
                )
            if self._jobs_done_list and self._jobs_done_list.winfo_exists():
                self._jobs_done_list.configure(state="normal")
                self._jobs_done_list.delete("1.0", tk.END)
                self._jobs_done_list.tag_configure("file_head", foreground=self._theme_color("FG_INFO"))
                self._jobs_done_list.tag_configure("result_head", foreground=self._theme_color("FG_ACCENT"))
                self._jobs_done_list.tag_configure("meta", foreground=self._theme_color("FG_SOFT"))
                self._jobs_done_list.tag_configure("done", foreground=self._theme_color("FG_INFO"))
                self._jobs_done_list.tag_configure("error", foreground=self._theme_color("FG_ERROR"))
                self._jobs_done_list.insert(tk.END, "file".ljust(31), ("file_head",))
                self._jobs_done_list.insert(tk.END, "result\n", ("result_head",))
                rows = finished_jobs[:8]
                for row in rows:
                    label = self._workflow_label(row, prefer=("video_id", "source_url"))
                    status = str(row.get("status") or "")
                    self._jobs_done_list.insert(tk.END, f"{label} ", ("meta",))
                    self._jobs_done_list.insert(tk.END, ("success" if status == "done" else "failed"), ("done" if status == "done" else "error",))
                    self._jobs_done_list.insert(tk.END, "\n")
                for _ in range(max(0, 3 - len(rows))):
                    self._jobs_done_list.insert(tk.END, "".ljust(31), ("meta",))
                    self._jobs_done_list.insert(tk.END, "\n")
                self._jobs_done_list.insert(
                    tk.END,
                    f"\nfinished={counts.get('done', 0)} failed={counts.get('failed', 0)}",
                    ("meta",),
                )
                self._jobs_done_list.configure(state="disabled")
        except Exception as exc:
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.insert("1.0", f"Failed to load workflows: {exc}")
                self._jobs_dl_text.configure(state="disabled")
        self._jobs_after_id = self.root.after(1000, self._refresh_jobs_popup)

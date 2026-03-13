from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from ..constants import FONT
from ..models import OverlayPanel
from ..utils import format_hms as _fmt_hms


class JobsPopupMixin:
    def _restart_workers(
        self,
        target: int,
        downloaders: int | None = None,
        transcribers: int | None = None
    ) -> None:
        target = max(0, int(target))
        if downloaders is None:
            downloaders = self._downloader_target_count
        if transcribers is None:
            transcribers = self._transcriber_target_count
        downloaders = max(0, int(downloaders))
        transcribers = max(0, int(transcribers))
        target = max(target, downloaders, transcribers)
        self.ingester.stop_background_workers()
        if target > 0:
            self.ingester.start_background_workers(
                max(target, downloaders + transcribers),
                downloader_count=downloaders,
                transcriber_count=transcribers,
            )
        self._worker_target_count = target
        self._downloader_target_count = downloaders
        self._transcriber_target_count = transcribers
        self._workers_paused = False
        self._workers_eta_next_recalc_at = 0.0

    def _run_worker_command(self, command: str) -> str:
        label = command.strip().lower()
        if label == "add downloader":
            self._restart_workers(
                self._worker_target_count,
                self._downloader_target_count + 1,
                self._transcriber_target_count
            )
            return f"Downloaders: {self._downloader_target_count}"
        if label == "remove downloader":
            self._restart_workers(
                self._worker_target_count,
                max(0, self._downloader_target_count - 1),
                self._transcriber_target_count
            )
            return f"Downloaders: {self._downloader_target_count}"
        if label == "add transcriber":
            self._restart_workers(
                self._worker_target_count,
                self._downloader_target_count,
                self._transcriber_target_count + 1
            )
            return f"Transcribers: {self._transcriber_target_count}"
        if label == "remove transcriber":
            self._restart_workers(
                self._worker_target_count,
                self._downloader_target_count,
                max(0, self._transcriber_target_count - 1)
            )
            return f"Transcribers: {self._transcriber_target_count}"
        if label == "kill jobs":
            try:
                n = int(self.ingester.kill_active_jobs())
                return f"Killed active jobs: {n}"
            except Exception as exc:
                return f"Kill failed: {exc}"
        if label == "clear queue":
            try:
                n = int(self.ingester.clear_queue())
                return f"Cleared queued jobs: {n}"
            except Exception as exc:
                return f"Clear queue failed: {exc}"
        if label == "poll subscriptions":
            try:
                summary = self.ingester.poll_subscriptions_once()
                scanned = summary.get('snanned', 0)
                queued = summary.get('queued', 0)
                return f"Poll: scanned={scanned} queued={queued}"
            except Exception as exc:
                return f"Poll failed: {exc}"
        return "Unknown command"

    def _recalculate_workers_eta(self) -> None:
        now = time.monotonic()
        self._workers_eta_last_tick_at = now
        self._workers_eta_next_recalc_at = (
            now + self._workers_eta_recalc_interval_sec
        )
        if self._workers_paused:
            self._workers_eta_remaining_sec = None
            return
        try:
            snapshot = self.ingester.dashboard_snapshot()
            counts = snapshot.get("counts", {})
            queued = int(counts.get("queued", 0))
            downloading = int(counts.get("downloading", 0))
            transcribing = int(counts.get("transcribing", 0))
            pending = queued + downloading + transcribing
            if pending <= 0:
                self._workers_eta_remaining_sec = 0.0
                return
            avg = snapshot.get("median_duration_sec")
            if avg is None:
                avg = snapshot.get("avg_duration_sec")
            avg_sec = float(avg) if avg is not None else 300.0
            avg_sec = max(30.0, avg_sec)
            workers = max(1, int((
                self._downloader_target_count +
                self._transcriber_target_count
                ) or self._worker_target_count or 1))
            self._workers_eta_remaining_sec = float(
                pending) * avg_sec / float(workers)
        except Exception:
            # Keep prior countdown if snapshot fails.
            if self._workers_eta_remaining_sec is None:
                self._workers_eta_remaining_sec = 0.0

    def _tick_workers_eta_countdown(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._workers_eta_last_tick_at)
        self._workers_eta_last_tick_at = now
        if self._workers_eta_remaining_sec is None:
            return
        self._workers_eta_remaining_sec = max(
            0.0, self._workers_eta_remaining_sec - elapsed)

    def _workers_eta_line(self) -> str:
        now = time.monotonic()
        if now >= self._workers_eta_next_recalc_at:
            self._recalculate_workers_eta()
        self._tick_workers_eta_countdown()
        recalc_in = max(
            0, int(self._workers_eta_next_recalc_at - time.monotonic()))
        if self._workers_paused:
            return f"eta_til_finish=paused  recalc_in={_fmt_hms(recalc_in)}"
        if self._workers_eta_remaining_sec is None:
            return f"eta_til_finish=unknown  recalc_in={_fmt_hms(recalc_in)}"
        return (
            f"eta_til_finish=~{_fmt_hms(self._workers_eta_remaining_sec)}  "
            f"recalc_in={_fmt_hms(recalc_in)}"
        )

    def _open_jobs_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Workers", "1320x620")
        self._jobs_popup = popup
        self._register_popup("workers", popup)
        popup.rowconfigure(0, weight=1)
        popup.rowconfigure(1, weight=0)
        popup.columnconfigure(0, weight=1)

        visual = tk.Frame(popup, bg="#111111")
        visual.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))
        # Prioritize queue/finished readability over worker stack columns.
        for col, wt in enumerate((5, 2, 2, 5)):
            visual.columnconfigure(col, weight=wt)
        visual.grid_columnconfigure(0, minsize=300)
        visual.grid_columnconfigure(3, minsize=300)
        visual.rowconfigure(1, weight=1)

        def head(col: int, text: str) -> None:
            tk.Label(
                visual,
                text=text,
                anchor="w",
                bg="#0d0d0d",
                fg="#8f8f8f",
                font=(FONT["STYLE"], FONT["SIZE"] - 3),
                padx=8,
                pady=4,
            ).grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0 if col == 0 else 6, 0),
                pady=(0, 4)
            )

        head(0, "Queue")
        head(1, "Downloaders")
        head(2, "Transcribers")
        head(3, "Finished")

        queue_list = tk.Listbox(
            visual,
            bg="#000000",
            fg="#ffffff",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            exportselection=False,
        )
        queue_list.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=(0, 6),
            pady=(0, 8)
        )
        self._jobs_queue_list = queue_list

        dl_text = tk.Text(
            visual,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="none",
            padx=8,
            pady=8,
        )
        dl_text.grid(
            row=1,
            column=1,
            sticky="nsew",
            padx=(0, 6),
            pady=(0, 8)
        )
        dl_text.configure(state="disabled")
        self._jobs_dl_text = dl_text

        tr_text = tk.Text(
            visual,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="none",
            padx=8,
            pady=8,
        )
        tr_text.grid(
            row=1,
            column=2,
            sticky="nsew",
            padx=(0, 6),
            pady=(0, 8)
        )
        tr_text.configure(state="disabled")
        self._jobs_tr_text = tr_text

        done_list = tk.Listbox(
            visual,
            bg="#000000",
            fg="#d2d2d2",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            exportselection=False,
        )
        done_list.grid(
            row=1,
            column=3,
            sticky="nsew",
            pady=(0, 8)
        )
        self._jobs_done_list = done_list

        cmd_list = tk.Listbox(
            popup,
            bg="#000000",
            fg="#ffffff",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            exportselection=False,
            height=6,
        )
        cmd_list.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self._workers_cmd_list = cmd_list
        commands = [
            "Add Downloader",
            "Remove Downloader",
            "Add Transcriber",
            "Remove Transcriber",
            "Kill Jobs",
            "Clear Queue",
            "Poll Subscriptions"
        ]
        for cmd in commands:
            cmd_list.insert(tk.END, cmd)
        cmd_list.selection_set(0)

        status_var = tk.StringVar(
            value="Enter executes command | Up/Down move command cursor")
        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status_lbl.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8)
        )

        agent_head = tk.Label(
            popup,
            text="Agent Activity",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=4,
        )
        agent_head.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
        agent_text = tk.Text(
            popup,
            bg="#000000",
            fg="#d2d2d2",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="word",
            height=8,
            padx=8,
            pady=8,
        )
        agent_text.grid(
            row=4,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8)
        )
        agent_text.configure(state="disabled")
        self._jobs_agent_text = agent_text

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = cmd_list.curselection()
            if not sel:
                return "break"
            label = str(cmd_list.get(sel[0]))
            status_var.set(self._run_worker_command(label))
            self._refresh_jobs_popup()
            return "break"

        def move(delta: int) -> str:
            sel = cmd_list.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(commands) - 1))
            cmd_list.selection_clear(0, tk.END)
            cmd_list.selection_set(nxt)
            cmd_list.activate(nxt)
            cmd_list.see(nxt)
            return "break"

        popup.bind("<Escape>", lambda _e: self._close_jobs_popup())
        popup.bind("<Up>", lambda _e: move(-1))
        popup.bind("<Down>", lambda _e: move(1))
        popup.bind("<Return>", run_selected)
        cmd_list.bind("<Double-Button-1>", run_selected)
        popup.protocol("WM_DELETE_WINDOW", self._close_jobs_popup)
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
        self.filter_entry.focus_set()

    def _refresh_jobs_popup(self) -> None:
        if not self._jobs_popup or not self._jobs_popup.winfo_exists():
            return
        try:
            snapshot = self.ingester.jobs_summary(limit=30)
            dash = self.ingester.dashboard_snapshot()
            counts = snapshot.get("counts", {})
            jobs = [dict(r) for r in (snapshot.get("jobs", []) or [])]
            active_jobs = [dict(r)
                           for r in (dash.get("active_jobs", []) or [])]
            self._worker_anim_tick = (self._worker_anim_tick + 1) % 1000

            queued_jobs = [r for r in jobs if str(r.get("status")) in {
                "queued", "downloading", "transcribing"}]
            finished_jobs = [r for r in jobs if str(
                r.get("status")) in {"done", "failed"}]
            if self._jobs_queue_list and self._jobs_queue_list.winfo_exists():
                self._jobs_queue_list.delete(0, tk.END)
                for row in queued_jobs:
                    self._jobs_queue_list.insert(
                        tk.END,
                        f"{row.get('id')} {str(row.get('status')):<11} {
                            str(row.get('video_id') or '-')}",
                    )
            if self._jobs_done_list and self._jobs_done_list.winfo_exists():
                self._jobs_done_list.delete(0, tk.END)
                for row in finished_jobs:
                    self._jobs_done_list.insert(
                        tk.END,
                        f"{row.get('id')} {str(row.get('status')):<6} {
                            str(row.get('video_id') or '-')}",
                    )

            downloading_active = [r for r in active_jobs if str(
                r.get("status")) == "downloading"]
            transcribing_active = [r for r in active_jobs if str(
                r.get("status")) == "transcribing"]

            def _worker_lines(
                kind: str,
                count: int,
                active: list[dict[str, Any]]
            ) -> list[str]:
                lines: list[str] = []
                blink = "█" if (self._worker_anim_tick % 2 == 0) else "."
                z_phase = (self._worker_anim_tick % 3) + 1
                for i in range(max(0, count)):
                    if i < len(active):
                        job = active[i]
                        elapsed = float(job.get("elapsed_sec") or 0.0)
                        target = 120.0 if kind == "downloading" else 300.0
                        fill = max(
                            0, min(10, int((elapsed / max(1.0, target)) * 10.0)))
                        bar = "[" + ("█" * max(0, fill - 1)) + (blink if fill >
                                                                0 else ".") + ("." * max(0, 10 - fill)) + "]"
                        lines.append(f"⛑{i+1:02d} ({kind})")
                        lines.append(f"{bar} job={job.get('id')}")
                    else:
                        z = "z" * z_phase
                        lines.append(f"⛑{i+1:02d} (idle)")
                        lines.append(f"[{z:<10}]")
                    lines.append("")
                return lines or ["(none)"]

            dl_lines = [
                f"downloaders={self._downloader_target_count}",
                f"transcribers={self._transcriber_target_count}",
                self._workers_eta_line(),
                "",
                *_worker_lines("downloading",
                               self._downloader_target_count, downloading_active),
            ]
            tr_lines = [
                f"queued={counts.get('queued', 0)} done={counts.get(
                    'done', 0)} failed={counts.get('failed', 0)}",
                "",
                *_worker_lines("transcribing",
                               self._transcriber_target_count, transcribing_active),
            ]
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.tag_configure("idle", foreground="#39d5ff")
                self._jobs_dl_text.tag_configure("hat", foreground="#f7d154")
                for line in dl_lines:
                    tag = None
                    if line.startswith("[") and "z" in line:
                        tag = "idle"
                    elif line.startswith("⛑"):
                        tag = "hat"
                    self._jobs_dl_text.insert(
                        tk.END, line + "\n", (() if tag is None else (tag,)))
                self._jobs_dl_text.configure(state="disabled")
            if self._jobs_tr_text and self._jobs_tr_text.winfo_exists():
                self._jobs_tr_text.configure(state="normal")
                self._jobs_tr_text.delete("1.0", tk.END)
                self._jobs_tr_text.tag_configure("idle", foreground="#39d5ff")
                self._jobs_tr_text.tag_configure("hat", foreground="#f7d154")
                for line in tr_lines:
                    tag = None
                    if line.startswith("[") and "z" in line:
                        tag = "idle"
                    elif line.startswith("⛑"):
                        tag = "hat"
                    self._jobs_tr_text.insert(
                        tk.END, line + "\n", (() if tag is None else (tag,)))
                self._jobs_tr_text.configure(state="disabled")
            if self._jobs_agent_text and self._jobs_agent_text.winfo_exists():
                self._jobs_agent_text.configure(state="normal")
                self._jobs_agent_text.delete("1.0", tk.END)
                self._jobs_agent_text.tag_configure(
                    "act", foreground="#7fd7ff")
                self._jobs_agent_text.tag_configure(
                    "err", foreground="#ff8a8a")
                self._jobs_agent_text.tag_configure(
                    "summary", foreground="#f7d154")
                self._jobs_agent_text.tag_configure(
                    "info", foreground="#b0b0b0")
                for row in self._agent_activity[-80:]:
                    kind = str(row.get("kind") or "info")
                    msg = str(row.get("message") or "")
                    stamp = str(row.get("time") or "00:00:00")
                    self._jobs_agent_text.insert(
                        tk.END,
                        f"[{stamp}] {kind}: {msg}\n",
                        (kind if kind in {"act", "err",
                         "summary", "info"} else "info",),
                    )
                self._jobs_agent_text.configure(state="disabled")
        except Exception as exc:
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.insert(
                    "1.0", f"Failed to load ingest jobs: {exc}")
                self._jobs_dl_text.configure(state="disabled")
            if self._jobs_agent_text and self._jobs_agent_text.winfo_exists():
                self._jobs_agent_text.configure(state="normal")
                self._jobs_agent_text.delete("1.0", tk.END)
                self._jobs_agent_text.insert(
                    "1.0", f"Agent pane refresh failed: {exc}")
                self._jobs_agent_text.configure(state="disabled")
        self._jobs_after_id = self.root.after(1000, self._refresh_jobs_popup)

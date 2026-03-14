from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from ..constants import FONT_SIZE_OFFSETS, ICONS, LISTBOX, POPUP_SIZES
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
                scanned = summary.get("scanned", 0)
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
        popup = self._create_popup_window(
            name="workers",
            title="Workers",
            size=POPUP_SIZES["WORKERS"],
            attr_name="_jobs_popup",
            row_weights={0: 1, 1: 0},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        visual = tk.Frame(content, bg=self._theme_color("APP_BG"))
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
                bg=self._theme_color("SURFACE_BG"),
                fg=self._theme_color("FG_MUTED"),
                font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
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

        queue_list = self._create_listbox(
            visual,
            font_delta=FONT_SIZE_OFFSETS["SMALL"],
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
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG"),
            borderwidth=0,
            highlightthickness=0,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
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
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG"),
            borderwidth=0,
            highlightthickness=0,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
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

        done_list = self._create_listbox(
            visual,
            fg=self._theme_color("FG_SOFT"),
            select_fg=self._theme_color("FG"),
            font_delta=FONT_SIZE_OFFSETS["SMALL"],
        )
        done_list.grid(
            row=1,
            column=3,
            sticky="nsew",
            pady=(0, 8)
        )
        self._jobs_done_list = done_list

        cmd_list = self._create_listbox(
            content,
            font_delta=FONT_SIZE_OFFSETS["BODY"],
        )
        cmd_list.configure(height=LISTBOX["COMMAND_HEIGHT"])
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
            value=self._status_hint("jobs"))
        status_lbl = self._create_status_label(
            content,
            status_var,
            font_delta=FONT_SIZE_OFFSETS["SMALL"],
        )
        status_lbl.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8)
        )

        agent_head = tk.Label(
            content,
            text="Agent Activity",
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        agent_head.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
        agent_text = tk.Text(
            content,
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG_SOFT"),
            borderwidth=0,
            highlightthickness=0,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            wrap="word",
            height=LISTBOX["AGENT_HEIGHT"],
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

        self._bind_popup_close(
            popup,
            after_close=self._close_jobs_popup,
            focus_filter=False,
        )
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
            failed_recent = [r for r in finished_jobs if str(r.get("status")) == "failed"]

            def _worker_rows(
                kind: str,
                count: int,
                active: list[dict[str, Any]],
                *,
                failure_slots: int = 0,
            ) -> list[dict[str, str]]:
                rows: list[dict[str, str]] = []
                blink = "█" if (self._worker_anim_tick % 2 == 0) else "."
                for i in range(max(0, count)):
                    if i < len(active):
                        job = active[i]
                        elapsed = float(job.get("elapsed_sec") or 0.0)
                        target = 120.0 if kind == "downloading" else 300.0
                        fill = max(
                            0, min(10, int((elapsed / max(1.0, target)) * 10.0)))
                        bar = "[" + ("█" * max(0, fill - 1)) + (blink if fill > 0 else ".") + ("." * max(0, 10 - fill)) + "]"
                        rows.append({
                            "icon_tag": "active",
                            "bar_tag": "active",
                            "suffix": f" {bar} job={job.get('id')}",
                        })
                    else:
                        rows.append({
                            "icon_tag": "error" if i < failure_slots else "idle",
                            "bar_tag": "error" if i < failure_slots else "idle",
                            "suffix": " [..........]",
                        })
                return rows or [{
                    "icon_tag": "idle",
                    "bar_tag": "idle",
                    "suffix": " (none)",
                }]

            dl_lines = [
                self._workers_eta_line(),
            ]
            tr_lines = [
                f"queued={counts.get('queued', 0)} done={counts.get('done', 0)} failed={counts.get('failed', 0)}",
                "",
            ]
            dl_workers = _worker_rows(
                "downloading",
                self._downloader_target_count,
                downloading_active,
                failure_slots=max(0, min(self._downloader_target_count - len(downloading_active), len(failed_recent))),
            )
            tr_workers = _worker_rows(
                "transcribing",
                self._transcriber_target_count,
                transcribing_active,
                failure_slots=max(0, min(self._transcriber_target_count - len(transcribing_active), max(0, len(failed_recent) - self._downloader_target_count))),
            )
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.tag_configure("idle", foreground=self._theme_color("FG_LOG"))
                self._jobs_dl_text.tag_configure("active", foreground=self._theme_color("FG_ACCENT"))
                self._jobs_dl_text.tag_configure("error", foreground=self._theme_color("FG_ERROR"))
                self._jobs_dl_text.tag_configure("meta", foreground=self._theme_color("FG_SOFT"))
                for line in dl_lines:
                    self._jobs_dl_text.insert(tk.END, line + "\n", ("meta",))
                self._jobs_dl_text.insert(tk.END, "\n")
                for row in dl_workers:
                    self._jobs_dl_text.insert(tk.END, ICONS["WORKER"], (row["icon_tag"],))
                    self._jobs_dl_text.insert(tk.END, row["suffix"] + "\n", (row["bar_tag"],))
                self._jobs_dl_text.configure(state="disabled")
            if self._jobs_tr_text and self._jobs_tr_text.winfo_exists():
                self._jobs_tr_text.configure(state="normal")
                self._jobs_tr_text.delete("1.0", tk.END)
                self._jobs_tr_text.tag_configure("idle", foreground=self._theme_color("FG_LOG"))
                self._jobs_tr_text.tag_configure("active", foreground=self._theme_color("FG_ACCENT"))
                self._jobs_tr_text.tag_configure("error", foreground=self._theme_color("FG_ERROR"))
                self._jobs_tr_text.tag_configure("meta", foreground=self._theme_color("FG_SOFT"))
                for line in tr_lines:
                    self._jobs_tr_text.insert(tk.END, line + "\n", ("meta",))
                for row in tr_workers:
                    self._jobs_tr_text.insert(tk.END, ICONS["WORKER"], (row["icon_tag"],))
                    self._jobs_tr_text.insert(tk.END, row["suffix"] + "\n", (row["bar_tag"],))
                self._jobs_tr_text.configure(state="disabled")
            if self._jobs_agent_text and self._jobs_agent_text.winfo_exists():
                self._jobs_agent_text.configure(state="normal")
                self._jobs_agent_text.delete("1.0", tk.END)
                self._jobs_agent_text.tag_configure(
                    "act", foreground=self._theme_color("FG_INFO"))
                self._jobs_agent_text.tag_configure(
                    "err", foreground=self._theme_color("FG_ERROR"))
                self._jobs_agent_text.tag_configure(
                    "summary", foreground=self._theme_color("FG_ACCENT"))
                self._jobs_agent_text.tag_configure(
                    "info", foreground=self._theme_color("FG_LOG"))
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

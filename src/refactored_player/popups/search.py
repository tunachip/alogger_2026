from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from alog.pipeline import resolve_playback_media_path

from ..constants import FONT, FONT_SIZE_OFFSETS, LISTBOX, POPUP_SIZES, THEME


class SearchPopupMixin:
    def _open_search_popup(self) -> None:
        popup = self._create_popup_window(
                    name="finder",
                    title="Search DB",
                    size=POPUP_SIZES["DEFAULT"],
            attr_name="_search_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        query_var = tk.StringVar(value=self.filter_var.get().strip())
        query_entry = ttk.Entry(
            popup, textvariable=query_var, style="Filter.TEntry")
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header = tk.Label(
            popup,
            text="Matches  Title (matches = number of matching caption segments)",
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_MUTED"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        body = tk.Frame(popup, bg=THEME["APP_BG"])
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        count_list = self._create_listbox(
            body,
            fg=THEME["FG_ACCENT"],
            select_fg=THEME["FG_ACCENT"],
            width=LISTBOX["COUNT_WIDTH"],
            bold=True,
            takefocus=0,
        )
        title_list = self._create_listbox(body)
        count_list.grid(row=0, column=0, sticky="ns")
        title_list.grid(row=0, column=1, sticky="nsew")

        hint = tk.Label(
            popup,
            text="Type query, Up/Down select, Enter open video, Esc close",
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_MUTED"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=6,
        )
        hint.grid(row=3, column=0, sticky="ew")

        def _set_selection(idx: int) -> None:
            self._set_listbox_selection(
                [count_list, title_list],
                idx,
                len(self._search_results),
            )

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            count_list.delete(0, tk.END)
            title_list.delete(0, tk.END)
            self._search_results = []
            if not query:
                return
            rows = self.ingester.search_videos(query, limit=200)
            self._search_results = [dict(r) for r in rows]
            for row in self._search_results:
                title = str(
                    row.get("title")
                    or row.get("video_id")
                    or "untitled"
                ).replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                count_list.insert(tk.END, f"{count:>4}")
                title_list.insert(tk.END, title)
            if self._search_results:
                _set_selection(0)

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
            if not sel:
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._search_results):
                return "break"
            row = self._search_results[idx]
            query = query_var.get().strip()
            video_id = str(row.get("video_id") or "")
            transcript_path = Path(str(row.get("transcript_json_path") or ""))
            preferred = Path(str(row.get("local_video_path") or "")) if row.get(
                "local_video_path") else None
            if not transcript_path.exists():
                self.status_var.set(f"Missing transcript for {video_id}")
                return "break"
            try:
                video_path = resolve_playback_media_path(
                    self.ingester_config,
                    video_id=video_id,
                    preferred_path=preferred,
                )
            except Exception as exc:
                self.status_var.set(f"Playback path error: {exc}")
                return "break"
            audio_path = self._find_audio_sidecar(video_id, video_path)
            start_sec = float(int(row.get("first_start_ms") or 0)) / 1000.0
            self._load_session(
                video_id=video_id,
                transcript_json=transcript_path,
                video_path=video_path,
                audio_path=audio_path,
                start_sec=start_sec,
                filter_text=query,
            )
            popup.destroy()
            self._search_popup = None
            self.filter_entry.focus_set()
            return "break"

        def move_sel(delta: int) -> str:
            sel = title_list.curselection()
            cur = int(sel[0]) if sel else 0
            _set_selection(cur + delta)
            return "break"

        query_var.trace_add("write", refresh_results)
        self._bind_popup_close(
            popup,
            after_close=lambda: setattr(self, "_search_popup", None),
        )
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list, count_list],
            on_enter=lambda: open_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        title_list.bind("<Double-Button-1>", open_selected)
        count_list.bind("<Button-1>", lambda _e: "break")
        refresh_results()
        query_entry.focus_set()

    def _open_video_picker_popup(self) -> None:
        popup = self._create_popup_window(
                    name="open_video",
                    title="Open Video",
                    size=POPUP_SIZES["DEFAULT"],
            attr_name="_video_picker_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        query_var = tk.StringVar(value="")
        query_entry = ttk.Entry(
            popup, textvariable=query_var, style="Filter.TEntry")
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header = tk.Label(
            popup,
            text="Matches  Title (matches = number of title matches)",
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_MUTED"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        body = tk.Frame(popup, bg=THEME["APP_BG"])
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        count_list = self._create_listbox(
            body,
            fg=THEME["FG_ACCENT"],
            select_fg=THEME["FG_ACCENT"],
            width=LISTBOX["COUNT_WIDTH"],
            bold=True,
            takefocus=0,
        )
        title_list = self._create_listbox(body)
        count_list.grid(row=0, column=0, sticky="ns")
        title_list.grid(row=0, column=1, sticky="nsew")

        hint = tk.Label(
            popup,
            text="Type title filter, Up/Down select, Enter open, Delete remove video+transcript, Esc close",
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_MUTED"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=6,
        )
        hint.grid(row=3, column=0, sticky="ew")
        status_var = tk.StringVar(value="")
        status_lbl = self._create_status_label(
            popup,
            status_var,
            fg=THEME["FG_SOFT"],
            font_delta=-3,
        )
        status_lbl.grid(row=4, column=0, sticky="ew")
        pending_delete: dict[str, str | None] = {"video_id": None}

        def _set_selection(idx: int) -> None:
            self._set_listbox_selection(
                [count_list, title_list],
                idx,
                len(self._video_picker_results),
            )

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            count_list.delete(0, tk.END)
            title_list.delete(0, tk.END)
            self._video_picker_results = []
            rows = self.ingester.search_video_titles(query, limit=300)
            self._video_picker_results = [dict(r) for r in rows]
            for row in self._video_picker_results:
                title = str(
                    row.get("title")
                    or row.get("video_id")
                    or "untitled"
                ).replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                count_list.insert(tk.END, f"{count:>4}")
                title_list.insert(tk.END, title)
            if self._video_picker_results:
                _set_selection(0)
            status_var.set(f"{len(self._video_picker_results)} rows")

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
            if not sel:
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._video_picker_results):
                return "break"
            row = self._video_picker_results[idx]
            video_id = str(row.get("video_id") or "")
            transcript_raw = str(row.get("transcript_json_path") or "").strip()
            transcript_path = Path(transcript_raw) if transcript_raw else None
            preferred = Path(str(row.get("local_video_path") or "")) if row.get(
                "local_video_path") else None
            if transcript_path is not None and not transcript_path.exists():
                transcript_path = None
            try:
                video_path = resolve_playback_media_path(
                    self.ingester_config,
                    video_id=video_id,
                    preferred_path=preferred,
                )
            except Exception as exc:
                self.status_var.set(f"Playback path error: {exc}")
                return "break"
            audio_path = self._find_audio_sidecar(video_id, video_path)
            self._load_session(
                video_id=video_id,
                transcript_json=transcript_path,
                video_path=video_path,
                audio_path=audio_path,
                start_sec=0.0,
                filter_text="",
            )
            popup.destroy()
            self._video_picker_popup = None
            self.filter_entry.focus_set()
            return "break"

        def move_sel(delta: int) -> str:
            sel = title_list.curselection()
            cur = int(sel[0]) if sel else 0
            _set_selection(cur + delta)
            return "break"

        def delete_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
            if not sel:
                status_var.set("No video selected")
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._video_picker_results):
                status_var.set("Invalid selection")
                return "break"
            row = self._video_picker_results[idx]
            video_id = str(row.get("video_id") or "")
            if not video_id:
                status_var.set("Selection has no video id")
                return "break"
            if pending_delete.get("video_id") != video_id:
                pending_delete["video_id"] = video_id
                status_var.set(f"Press Delete again to remove {video_id}")
                return "break"
            pending_delete["video_id"] = None
            try:
                summary = self.ingester.delete_video_and_assets(video_id)
                status_var.set(
                    f"Deleted {video_id}: files={
                        summary.get('deleted_files', 0)} "
                    f"jobs={summary.get('jobs_deleted', 0)}"
                )
                if self.current_video_id == video_id:
                    self._clear_loaded_session(
                        "Deleted currently loaded video")
                refresh_results()
            except Exception as exc:
                status_var.set(f"Delete failed: {exc}")
            return "break"

        query_var.trace_add("write", refresh_results)
        self._bind_popup_close(
            popup,
            after_close=lambda: setattr(self, "_video_picker_popup", None),
        )
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list, count_list],
            on_enter=lambda: open_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        title_list.bind("<Double-Button-1>", open_selected)
        title_list.bind("<Delete>", delete_selected)
        count_list.bind("<Button-1>", lambda _e: "break")
        popup.bind("<Delete>", delete_selected)
        refresh_results()
        query_entry.focus_set()

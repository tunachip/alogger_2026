from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class CommandPopupMixin:
    def _open_command_popup(self) -> None:
        popup = self._create_popup_window(
            name="command",
            title="Command Menu",
            size="720x520",
            attr_name="_command_popup",
            row_weights={1: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        query_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=query_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        listbox = self._create_listbox(popup)
        listbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        commands: list[tuple[str, Callable[[], None]]] = [
            ("Ingest Popup", lambda: self._toggle_popup(
                "ingest", self._open_ingest_popup)),
            ("Workers", lambda: self._toggle_popup(
                "workers", self._open_jobs_popup)),
            ("Open Video", lambda: self._toggle_popup(
                "open_video", self._open_video_picker_popup)),
            ("Finder", lambda: self._toggle_popup(
                "finder", self._open_search_popup)),
            ("AI Agent", lambda: self._toggle_popup("ai", self._open_ai_popup)),
            ("Goto Timestamp", self._open_goto_popup),
            ("Settings", lambda: self._toggle_popup(
                "settings", self._open_settings_popup)),
            ("Channel Browser", lambda: self._toggle_popup(
                "channel", self._open_channel_popup)),
            ("Subscriptions", lambda: self._toggle_popup(
                "subscriptions", self._open_subscriptions_popup)),
            # type: ignore[arg-type]
            ("Toggle Transcript Log", lambda: self._on_toggle_transcript_log(None)),
            # type: ignore[arg-type]
            ("Toggle Details", lambda: self._on_toggle_details(None)),
            ("Toggle Skim Mode", self._toggle_skim_mode),
            ("Quit", self.close),
        ]
        filtered_indexes: list[int] = list(range(len(commands)))

        def refresh(*_args: object) -> None:
            needle = query_var.get().strip().lower()
            filtered_indexes.clear()
            listbox.delete(0, tk.END)
            for idx, (label, _fn) in enumerate(commands):
                if needle and needle not in label.lower():
                    continue
                filtered_indexes.append(idx)
                listbox.insert(tk.END, label)
            if filtered_indexes:
                listbox.selection_set(0)

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                return "break"
            pos = int(sel[0])
            if pos < 0 or pos >= len(filtered_indexes):
                return "break"
            cmd_idx = filtered_indexes[pos]
            _label, fn = commands[cmd_idx]
            popup.destroy()
            fn()
            return "break"

        def move(delta: int) -> str:
            if not filtered_indexes:
                return "break"
            sel = listbox.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(filtered_indexes) - 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(nxt)
            listbox.activate(nxt)
            listbox.see(nxt)
            return "break"

        query_var.trace_add("write", refresh)
        self._bind_popup_close(popup, focus_filter=False)
        self._bind_fzf_like_keys(
            popup,
            entry=entry,
            capture_widgets=[entry, listbox],
            on_enter=lambda: run_selected(),
            on_up=lambda: move(-1),
            on_down=lambda: move(1),
            on_home=lambda: move(-10_000),
            on_end=lambda: move(10_000),
            on_page_up=lambda: move(-10),
            on_page_down=lambda: move(10),
        )
        listbox.bind("<Double-Button-1>", run_selected)
        entry.focus_set()
        refresh()

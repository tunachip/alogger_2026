from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..constants import FONT


class IngestPopupMixin:
    def _open_ingest_popup(self) -> None:
        popup = self._create_popup_window(
            name="ingest",
            title="Ingest",
            size="900x460",
            attr_name="_ingest_popup",
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        input_var = tk.StringVar()
        entry = ttk.Entry(popup, textvariable=input_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        hint = tk.Label(
            popup,
            text="Input + Enter on command: Ingest (URL[s]) | Browse (channel) | Subscribe (channel)",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        hint.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        listbox = self._create_listbox(popup)
        listbox.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        commands = ["Ingest", "Browse", "Subscribe"]
        for c in commands:
            listbox.insert(tk.END, c)
        listbox.selection_set(0)

        status = tk.StringVar(value="Ready")
        status_lbl = self._create_status_label(popup, status)
        status_lbl.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))

        def _selected_command() -> str:
            sel = listbox.curselection()
            idx = int(sel[0]) if sel else 0
            return commands[max(0, min(idx, len(commands) - 1))]

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            cmd = _selected_command()
            token = input_var.get().strip()
            if cmd == "Ingest":
                if not token:
                    status.set("Provide one or more URLs separated by spaces")
                    return "break"
                urls = [u.strip() for u in token.split() if u.strip()]
                if not urls:
                    status.set("No URLs parsed")
                    return "break"
                try:
                    result = self.ingester.enqueue_with_dedupe(
                        urls,
                        allow_overwrite=False,
                        auto_transcribe=self._auto_transcribe_default(),
                    )
                    ids = list(result.get("queued_ids") or [])
                    status.set(f"Queued {len(ids)} jobs")
                    if ids:
                        self.status_var.set(f"Queued ingest job {ids[0]}")
                except Exception as exc:
                    status.set(f"Ingest failed: {exc}")
                return "break"

            if cmd == "Browse":
                if not token:
                    status.set("Provide channel URL/@handle/name")
                    return "break"
                self._channel_default_ref = token
                popup.destroy()
                self._open_channel_popup()
                self.status_var.set(f"Channel browse requested: {token}")
                return "break"

            if not token:
                status.set("Provide channel URL/@handle/name")
                return "break"
            try:
                sub = self.ingester.add_channel_subscription(
                    token, seed_with_latest=True)
                status.set(f"Subscribed: {sub.get(
                    'channel_title')} ({sub.get('channel_key')})")
            except Exception as exc:
                status.set(f"Subscribe failed: {exc}")
            return "break"

        def move(delta: int) -> str:
            sel = listbox.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(commands) - 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(nxt)
            listbox.activate(nxt)
            listbox.see(nxt)
            return "break"

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
        listbox.bind("<Return>", run_selected)
        listbox.bind("<Double-Button-1>", run_selected)
        self._bind_popup_close(popup)
        entry.focus_set()

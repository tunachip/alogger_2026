from __future__ import annotations

import tkinter as tk
from typing import Any


class SubscriptionsPopupMixin:
    def _open_subscriptions_popup(self) -> None:
        popup = self._create_popup_window(
            name="subscriptions",
            title="Subscriptions",
            size="920x620",
            attr_name="_subscriptions_popup",
            reuse_existing=True,
            row_weights={0: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        listbox = self._create_listbox(popup, font_delta=-3)
        listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))

        status_var = tk.StringVar(
            value="R refresh | Delete remove selected | P poll now")
        status_lbl = self._create_status_label(
            popup, status_var, font_delta=-3)
        status_lbl.grid(row=1, column=0, sticky="ew")
        rows_cache: list[dict[str, Any]] = []

        def refresh_rows(_event: tk.Event[tk.Misc] | None = None) -> str:
            nonlocal rows_cache
            try:
                rows = self.ingester.list_channel_subscriptions(
                    active_only=False)
                rows_cache = [dict(r) for r in rows]
                listbox.delete(0, tk.END)
                for row in rows_cache:
                    title = str(row.get("channel_title")
                                or row.get("channel_key") or "")
                    key = str(row.get("channel_key") or "")
                    last_seen = str(row.get("last_seen_video_id") or "-")
                    mode_raw = row.get("auto_transcribe")
                    mode = "default" if mode_raw is None else (
                        "on" if int(mode_raw) == 1 else "off")
                    listbox.insert(tk.END, f"{title} | {key} | last_seen={
                                   last_seen} | transcribe={mode}")
                status_var.set(f"{len(rows_cache)} subscriptions loaded")
                if rows_cache:
                    listbox.selection_set(0)
            except Exception as exc:
                status_var.set(f"Failed to load subscriptions: {exc}")
            return "break"

        def remove_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                status_var.set("No subscription selected")
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(rows_cache):
                status_var.set("Invalid selection")
                return "break"
            channel_key = str(rows_cache[idx].get("channel_key") or "")
            if not channel_key:
                status_var.set("Selected row has no channel key")
                return "break"
            try:
                removed = self.ingester.remove_channel_subscription(
                    channel_key)
                status_var.set(f"Removed={removed} channel_key={channel_key}")
                refresh_rows()
            except Exception as exc:
                status_var.set(f"Remove failed: {exc}")
            return "break"

        def poll_now(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                summary = self.ingester.poll_subscriptions_once()
                status_var.set(
                    f"Poll complete: scanned={
                        summary.get('scanned', 0)
                    } queued={
                        summary.get('queued', 0)
                    }"
                )
            except Exception as exc:
                status_var.set(f"Poll failed: {exc}")
            return "break"

        self._bind_popup_close(popup)
        popup.bind("<KeyPress-r>", refresh_rows)
        popup.bind("<Delete>", remove_selected)
        popup.bind("<KeyPress-p>", poll_now)
        refresh_rows()

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from ..constants import FONT_SIZE_OFFSETS, POPUP_SIZES
from ..utils import format_hms as _fmt_hms


class NavigationPopupMixin:
    def _open_goto_popup(self) -> None:
        popup = self._create_popup_window(
            name="goto",
            title="Goto Timestamp",
            size=POPUP_SIZES["GOTO"],
            attr_name="_goto_popup",
            reuse_existing=True,
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        value_var = tk.StringVar(value="")
        entry = ttk.Entry(content, textvariable=value_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        status_var = tk.StringVar(
            value=self._status_hint("goto"))
        self._create_status_label(content, status_var).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        fmt_guard = {"active": False}

        def format_compact_time(raw: str) -> str:
            digits = re.sub(r"\D", "", str(raw or ""))
            if not digits:
                return ""
            if len(digits) <= 2:
                return digits
            if len(digits) <= 4:
                return f"{digits[:-2]}:{digits[-2:]}"
            return f"{digits[:-4]}:{digits[-4:-2]}:{digits[-2:]}"

        def digit_cursor(raw: str, cursor: int) -> int:
            return len(re.sub(r"\D", "", raw[:max(0, cursor)]))

        def display_cursor(formatted: str, digits_before_cursor: int) -> int:
            if digits_before_cursor <= 0:
                return 0
            seen = 0
            for idx, char in enumerate(formatted):
                if char.isdigit():
                    seen += 1
                    if seen >= digits_before_cursor:
                        return idx + 1
            return len(formatted)

        def on_time_change(*_args: object) -> None:
            if fmt_guard["active"]:
                return
            raw_value = value_var.get()
            cursor = 0
            try:
                cursor = int(entry.index(tk.INSERT))
            except Exception:
                cursor = len(raw_value)
            digits_before = digit_cursor(raw_value, cursor)
            formatted = format_compact_time(raw_value)
            if formatted == raw_value:
                return
            fmt_guard["active"] = True
            value_var.set(formatted)
            entry.icursor(display_cursor(formatted, digits_before))
            fmt_guard["active"] = False

        def parse_time(raw: str) -> float | None:
            token = raw.strip()
            if not token:
                return None
            if ":" not in token:
                try:
                    return max(0.0, float(token))
                except ValueError:
                    return None
            parts = token.split(":")
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 2:
                return max(0.0, nums[0] * 60.0 + nums[1])
            if len(nums) == 3:
                return max(0.0, nums[0] * 3600.0 + nums[1] * 60.0 + nums[2])
            return None

        def apply_goto(_event: tk.Event[tk.Misc] | None = None) -> str:
            sec = parse_time(value_var.get())
            if sec is None:
                status_var.set("Invalid format. Use seconds or HH:MM:SS")
                return "break"
            self._seek_to_absolute(sec)
            self.status_var.set(f"Jumped to {_fmt_hms(sec)}")
            popup.destroy()
            return "break"

        popup.bind("<Return>", apply_goto)
        entry.bind("<Return>", apply_goto)
        entry.bind("<KP_Enter>", apply_goto)
        value_var.trace_add("write", on_time_change)
        self._bind_popup_close(popup, focus_filter=False)
        entry.focus_set()

    def _open_skim_popup(self) -> None:
        popup = self._create_popup_window(
            name="skim",
            title="Skim Settings",
            size=POPUP_SIZES["SKIM"],
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        pre_var = tk.StringVar(value=str(self._skim_pre_ms))
        post_var = tk.StringVar(value=str(self._skim_post_ms))
        status_var = tk.StringVar(
            value=f"Current: {'ON' if self._skim_mode else 'OFF'} (pre={
                self._skim_pre_ms}ms, post={self._skim_post_ms}ms)"
        )

        pre_entry = ttk.Entry(content, textvariable=pre_var,
                              style="Filter.TEntry")
        pre_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        post_entry = ttk.Entry(
            content, textvariable=post_var, style="Filter.TEntry")
        post_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        hint = tk.Label(
            content,
            text=self._status_hint("skim"),
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
            padx=8,
            pady=6,
        )
        hint.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))

        status_lbl = self._create_status_label(
            content,
            status_var,
            fg=self._theme_color("FG_SOFT"),
        )
        status_lbl.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))

        def apply_values(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                pre = max(0, int(pre_var.get().strip() or "0"))
                post = max(0, int(post_var.get().strip() or "0"))
            except ValueError:
                status_var.set(
                    "Pre/post values must be integers in milliseconds")
                return "break"
            self._skim_pre_ms = pre
            self._skim_post_ms = post
            status_var.set(
                f"Applied: {'ON' if self._skim_mode else 'OFF'} (pre={pre}ms, post={
                    post}ms)"
            )
            return "break"

        popup.bind("<Return>", apply_values)
        self._bind_popup_close(popup)
        pre_entry.focus_set()

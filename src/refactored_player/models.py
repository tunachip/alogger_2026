from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from .constants import LAYOUT, POPUP_SIZES, THEME


@dataclass(slots=True)
class SegmentRow:
    index: int
    start_sec: float
    end_sec: float
    text: str
    text_lc: str


class OverlayPanel(tk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(
            root,
            bg=THEME["APP_BG"],
            highlightthickness=1,
            highlightbackground=THEME["BORDER"],
            bd=0,
        )
        self._wm_delete_cb: Callable[[], None] | None = None

    def title(self, _title: str) -> None:
        return

    def geometry(self, size: str) -> None:
        width = int(POPUP_SIZES["DEFAULT"].split("x", 1)[0])
        height = int(POPUP_SIZES["DEFAULT"].split("x", 1)[1])
        try:
            token = size.lower().split("+", 1)[0]
            width_token, height_token = token.split("x", 1)
            width = max(LAYOUT["POPUP_MIN_WIDTH"], int(width_token))
            height = max(LAYOUT["POPUP_MIN_HEIGHT"], int(height_token))
        except Exception:
            pass
        self.place(relx=0.5, rely=0.5, anchor="center", width=width, height=height)
        self.lift()

    def transient(self, _root: tk.Tk) -> None:
        return

    def protocol(self, name: str, callback: Callable[[], None]) -> None:
        if name == "WM_DELETE_WINDOW":
            self._wm_delete_cb = callback

    def request_close(self) -> None:
        if self._wm_delete_cb:
            self._wm_delete_cb()
            return
        self.destroy()

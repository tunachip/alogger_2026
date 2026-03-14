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
        self._requested_width = int(POPUP_SIZES["DEFAULT"].split("x", 1)[0])
        self._requested_height = int(POPUP_SIZES["DEFAULT"].split("x", 1)[1])
        self._master_configure_bind = self.master.bind(
            "<Configure>",
            self._on_master_configure,
            add="+",
        )
        self.bind("<Destroy>", self._on_destroy, add="+")

    def title(self, _title: str) -> None:
        return

    def geometry(self, size: str) -> None:
        try:
            token = size.lower().split("+", 1)[0]
            width_token, height_token = token.split("x", 1)
            self._requested_width = max(LAYOUT["POPUP_MIN_WIDTH"], int(width_token))
            self._requested_height = max(LAYOUT["POPUP_MIN_HEIGHT"], int(height_token))
        except Exception:
            pass
        self._sync_geometry_to_root()

    def _fitted_dimensions(self) -> tuple[int, int]:
        width = self._requested_width
        height = self._requested_height
        try:
            self.master.update_idletasks()
            root_width = max(1, int(self.master.winfo_width()))
            root_height = max(1, int(self.master.winfo_height()))
            width = min(width, max(LAYOUT["POPUP_MIN_WIDTH"], root_width - 32))
            height = min(height, max(LAYOUT["POPUP_MIN_HEIGHT"], root_height - 56))
        except Exception:
            pass
        return width, height

    def _sync_geometry_to_root(self) -> None:
        width, height = self._fitted_dimensions()
        self.place(relx=0.5, rely=0.5, anchor="center", width=width, height=height)
        self.lift()

    def _on_master_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if self.winfo_exists():
            self._sync_geometry_to_root()

    def _on_destroy(self, _event: tk.Event[tk.Misc]) -> None:
        if _event.widget is not self:
            return
        if self._master_configure_bind:
            try:
                self.master.unbind("<Configure>", self._master_configure_bind)
            except Exception:
                pass
            self._master_configure_bind = None

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


class QueryEntry(tk.Text):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        textvariable: tk.StringVar,
        bg: str,
        fg: str,
        accent_fg: str,
        border: str,
        font: tuple[str, int] | tuple[str, int, str],
    ) -> None:
        super().__init__(
            parent,
            height=1,
            wrap="none",
            undo=False,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=border,
            highlightcolor=border,
            bg=bg,
            fg=fg,
            insertbackground=fg,
            font=font,
            padx=6,
            pady=5,
        )
        self._textvariable = textvariable
        self._syncing_var = False
        self._syncing_widget = False
        self.tag_configure("operator", foreground=accent_fg)
        self.bind("<KeyRelease>", self._on_widget_change, add="+")
        self.bind("<<Paste>>", self._on_widget_change, add="+")
        self.bind("<<Cut>>", self._on_widget_change, add="+")
        self._textvariable.trace_add("write", self._on_var_change)
        self._set_value(self._textvariable.get())

    def configure_colors(
        self,
        *,
        bg: str,
        fg: str,
        accent_fg: str,
        border: str,
        font: tuple[str, int] | tuple[str, int, str],
    ) -> None:
        self.configure(
            bg=bg,
            fg=fg,
            insertbackground=fg,
            highlightbackground=border,
            highlightcolor=border,
            font=font,
        )
        self.tag_configure("operator", foreground=accent_fg)
        self._apply_operator_tags()

    def get(self) -> str:  # type: ignore[override]
        return super().get("1.0", "end-1c")

    def delete(self, first: int | str, last: int | str | None = None) -> None:  # type: ignore[override]
        start = self._index_to_text(first)
        end = self._index_to_text(last) if last is not None else f"{start}+1c"
        super().delete(start, end)
        self._after_widget_edit()

    def insert(self, index: int | str, chars: str) -> None:  # type: ignore[override]
        super().insert(self._index_to_text(index), chars)
        self._after_widget_edit()

    def index(self, index: int | str) -> int | str:  # type: ignore[override]
        if index in {tk.INSERT, "insert"}:
            return self._char_offset("insert")
        if index in {tk.END, "end"}:
            return len(self.get())
        return super().index(self._index_to_text(index))

    def icursor(self, index: int | str) -> None:
        target = self._index_to_text(index)
        self.mark_set("insert", target)
        self.see(target)

    def selection_range(self, start: int | str, end: int | str) -> None:
        self.tag_remove(tk.SEL, "1.0", tk.END)
        self.tag_add(tk.SEL, self._index_to_text(start), self._index_to_text(end))
        self.mark_set(tk.INSERT, self._index_to_text(end))
        self.see(tk.INSERT)

    def _index_to_text(self, index: int | str | None) -> str:
        if index is None:
            return "insert"
        if isinstance(index, int):
            return f"1.0+{max(0, index)}c"
        if index in {tk.INSERT, "insert"}:
            return "insert"
        if index in {tk.END, "end"}:
            return "end-1c"
        return str(index)

    def _char_offset(self, text_index: str) -> int:
        try:
            return int(self.count("1.0", text_index, "chars")[0])
        except Exception:
            return len(self.get())

    def _set_value(self, value: str) -> None:
        current = self.get()
        if current == value:
            self._apply_operator_tags()
            return
        cursor = self._char_offset("insert")
        self._syncing_var = True
        super().delete("1.0", tk.END)
        if value:
            super().insert("1.0", value)
        self._apply_operator_tags()
        self.mark_set("insert", f"1.0+{min(cursor, len(value))}c")
        self.see("insert")
        self._syncing_var = False

    def _apply_operator_tags(self) -> None:
        self.tag_remove("operator", "1.0", tk.END)
        value = self.get()
        for idx, char in enumerate(value):
            if char in {"|", "&"}:
                start = f"1.0+{idx}c"
                end = f"1.0+{idx + 1}c"
                self.tag_add("operator", start, end)

    def _after_widget_edit(self) -> None:
        self._apply_operator_tags()
        if self._syncing_var:
            return
        current = self.get()
        if self._textvariable.get() == current:
            return
        self._syncing_widget = True
        self._textvariable.set(current)
        self._syncing_widget = False

    def _on_widget_change(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._after_widget_edit()

    def _on_var_change(self, *_args: str) -> None:
        if self._syncing_widget:
            return
        self._set_value(self._textvariable.get())

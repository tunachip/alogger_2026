from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

import vlc

from ..constants import FONT, FONT_SIZE_OFFSETS, POPUP_ATTRS, THEME
from ..models import OverlayPanel


class PopupBaseMixin:
    def _popup_attr_name(self, name: str) -> str | None:
        return POPUP_ATTRS.get(name)

    def _close_popup_by_name(self, name: str) -> None:
        if name == "workers":
            self._close_jobs_popup()
            return
        attr = self._popup_attr_name(name)
        if not attr:
            return
        popup = getattr(self, attr, None)
        if popup and popup.winfo_exists():
            popup.destroy()
        setattr(self, attr, None)
        if self._active_popup_name == name:
            self._active_popup_name = None

    def _register_popup(self, name: str, popup: tk.Toplevel) -> None:
        # Embedded libVLC can intermittently overdraw Tk popups on some WMs.
        # Pause video rendering while any popup is open to stabilize stacking.
        if not self._active_popup_name:
            try:
                if self.player.get_state() == vlc.State.Playing:
                    self.player.set_pause(1)
                    self._popup_paused_player = True
                else:
                    self._popup_paused_player = False
            except Exception:
                self._popup_paused_player = False
            try:
                self.video_panel.grid_remove()
                self._popup_video_hidden = True
            except Exception:
                self._popup_video_hidden = False
        self._active_popup_name = name

        def _on_destroy(_event: tk.Event[tk.Misc]) -> None:
            if _event.widget is not popup:
                return
            attr = self._popup_attr_name(name)
            if attr:
                setattr(self, attr, None)
            if self._active_popup_name == name:
                self._active_popup_name = None
            if self._active_popup_name is None and self._popup_paused_player:
                try:
                    self.player.set_pause(0)
                except Exception:
                    pass
                self._popup_paused_player = False
            if self._active_popup_name is None and self._popup_video_hidden:
                try:
                    self.video_panel.grid(row=0, column=0, sticky="nsew")
                    self.root.update_idletasks()
                    self._bind_video_output(self.video_panel.winfo_id())
                except Exception:
                    pass
                self._popup_video_hidden = False

        popup.bind("<Destroy>", _on_destroy, add="+")

    def _toggle_popup(self, name: str, opener: Callable[[], None]) -> None:
        if self._active_popup_name == name:
            self._close_popup_by_name(name)
            return
        if self._active_popup_name:
            self._close_popup_by_name(self._active_popup_name)
        opener()

    def _on_open_search_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("finder", self._open_search_popup)
        return "break"

    def _on_open_video_picker_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("open_video", self._open_video_picker_popup)
        return "break"

    def _on_open_ingest_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("ingest", self._open_ingest_popup)
        return "break"

    def _on_open_command_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("command", self._open_command_popup)
        return "break"

    def _on_open_ai_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("ai", self._open_ai_popup)
        return "break"

    def _on_open_goto_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._open_goto_popup()
        return "break"

    def _on_open_settings_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("settings", self._open_settings_popup)
        return "break"

    def _on_escape(self, _event: tk.Event[tk.Misc]) -> str | None:
        if not self._active_popup_name:
            return None
        self._close_popup_by_name(self._active_popup_name)
        try:
            self.filter_entry.focus_set()
        except Exception:
            pass
        return "break"

    def _on_ctrl_s(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_skim_mode()
        return "break"

    def _on_toggle_skim_mode(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_skim_mode()
        return "break"

    def _on_open_skim_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._open_skim_popup()
        return "break"

    def _on_toggle_jobs_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._toggle_popup("workers", self._open_jobs_popup)
        return "break"

    def _on_toggle_details(self, _event: tk.Event[tk.Misc]) -> str:
        if self._details_hidden:
            self.caption_now_box.grid(row=1, column=0, sticky="ew")
            self.clock_view.grid(row=2, column=0, sticky="ew")
            self._details_hidden = False
            self._refresh_clock_now()
            self.status_var.set("Details shown")
            return "break"
        self.caption_now_box.grid_remove()
        self.clock_view.grid_remove()
        self._details_hidden = True
        return "break"

    def _on_type_to_filter(self, event: tk.Event[tk.Misc]) -> str | None:
        if self._active_popup_name is not None:
            return "break"
        if self._transcript_hidden:
            return None
        char = getattr(event, "char", "")
        if not char or len(char) != 1 or not char.isprintable():
            return None
        state = int(getattr(event, "state", 0))
        if state & 0x4:  # Control key mask
            return None
        widget = event.widget
        if widget is self.filter_entry:
            return None
        self.filter_entry.focus_set()
        try:
            pos = int(self.filter_entry.index(tk.INSERT))
            value = self.filter_var.get()
            self.filter_var.set(value[:pos] + char + value[pos:])
            self.filter_entry.icursor(pos + 1)
            return "break"
        except Exception:
            return None

    def _bind_fzf_like_keys(
        self,
        popup: tk.Misc,
        *,
        entry: ttk.Entry,
        capture_widgets: list[tk.Misc] | None = None,
        on_enter: Callable[[], str],
        on_up: Callable[[], str],
        on_down: Callable[[], str],
        on_home: Callable[[], str] | None = None,
        on_end: Callable[[], str] | None = None,
        on_page_up: Callable[[], str] | None = None,
        on_page_down: Callable[[], str] | None = None,
    ) -> None:
        def handler(event: tk.Event[tk.Misc]) -> str | None:
            keysym = str(getattr(event, "keysym", ""))
            state = int(getattr(event, "state", 0))
            ctrl = bool(state & 0x4)

            if keysym == "Return":
                return on_enter()
            if keysym == "Up":
                return on_up()
            if keysym == "Down":
                return on_down()
            if keysym == "Home" and on_home:
                return on_home()
            if keysym == "End" and on_end:
                return on_end()
            if keysym == "Prior" and on_page_up:
                return on_page_up()
            if keysym == "Next" and on_page_down:
                return on_page_down()

            if ctrl and keysym.lower() == "c":
                try:
                    entry.delete(0, tk.END)
                    entry.focus_set()
                except Exception:
                    return "break"
                return "break"

            if ctrl:
                return None

            char = str(getattr(event, "char", ""))
            try:
                pos = int(entry.index(tk.INSERT))
                value = entry.get()
            except Exception:
                return None

            if keysym == "Left":
                entry.focus_set()
                entry.icursor(max(0, pos - 1))
                return "break"
            if keysym == "Right":
                entry.focus_set()
                entry.icursor(min(len(value), pos + 1))
                return "break"
            if keysym == "BackSpace":
                if pos > 0:
                    entry.delete(pos - 1, pos)
                entry.focus_set()
                return "break"
            if keysym == "Delete":
                if pos < len(value):
                    entry.delete(pos, pos + 1)
                entry.focus_set()
                return "break"

            if char and len(char) == 1 and char.isprintable():
                entry.insert(pos, char)
                entry.icursor(pos + 1)
                entry.focus_set()
                return "break"
            return None

        popup.bind("<KeyPress>", handler, add="+")
        for w in (capture_widgets or []):
            w.bind("<KeyPress>", handler, add="+")

    def _on_toggle_transcript_log(self, _event: tk.Event[tk.Misc]) -> str:
        if self._transcript_hidden:
            self.shell.add(self.right_panel, minsize=200)
            if self._split_x_before_hide is not None:
                self.shell.sash_place(0, self._split_x_before_hide, 0)
            self.filter_entry.configure(state="normal")
            self.filter_entry.focus_set()
            self._transcript_hidden = False
            self.root.update_idletasks()
            self._update_progress_bar_width()
            self._refresh_clock_now()
            self.status_var.set("Transcript log shown")
            return "break"

        total_w = self.shell.winfo_width()
        if total_w > 0:
            try:
                self._split_x_before_hide = int(self.shell.sash_coord(0)[0])
            except Exception:
                self._split_x_before_hide = int(total_w * 3 / 4)
        self.filter_entry.configure(state="disabled")
        self.video_panel.focus_set()
        try:
            self.shell.forget(self.right_panel)
        except Exception:
            pass
        self._transcript_hidden = True
        self.root.update_idletasks()
        self._update_progress_bar_width()
        self._refresh_clock_now()
        self.status_var.set("Transcript log hidden")
        return "break"

    def _apply_popup_style(self, popup: tk.Toplevel, title: str, size: str) -> None:
        popup.title(title)
        popup.geometry(size)
        popup.configure(bg=THEME["APP_BG"])
        popup.lift()
        popup.focus_set()
        self._apply_font_scale_tree(popup)

    def _create_popup_window(
        self,
        *,
        name: str,
        title: str,
        size: str,
        attr_name: str | None = None,
        reuse_existing: bool = False,
        row_weights: dict[int, int] | None = None,
        column_weights: dict[int, int] | None = None,
    ) -> tk.Toplevel | None:
        if reuse_existing and attr_name:
            existing = getattr(self, attr_name, None)
            if existing and existing.winfo_exists():
                existing.focus_force()
                return None
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, title, size)
        if attr_name:
            setattr(self, attr_name, popup)
        self._register_popup(name, popup)
        for row, weight in (row_weights or {}).items():
            popup.rowconfigure(row, weight=weight)
        for column, weight in (column_weights or {}).items():
            popup.columnconfigure(column, weight=weight)
        return popup

    def _close_popup_window(
        self,
        popup: tk.Toplevel,
        *,
        after_close: Callable[[], None] | None = None,
        focus_filter: bool = True,
    ) -> None:
        if after_close is not None:
            after_close()
        if popup.winfo_exists():
            popup.destroy()
        if focus_filter:
            try:
                self.filter_entry.focus_set()
            except Exception:
                pass

    def _bind_popup_close(
        self,
        popup: tk.Toplevel,
        *,
        after_close: Callable[[], None] | None = None,
        focus_filter: bool = True,
    ) -> None:
        def close_popup() -> None:
            self._close_popup_window(
                popup,
                after_close=after_close,
                focus_filter=focus_filter,
            )

        popup.bind("<Escape>", lambda _e: close_popup())
        popup.protocol("WM_DELETE_WINDOW", close_popup)

    def _create_listbox(
        self,
        parent: tk.Misc,
        *,
        fg: str = THEME["FG"],
        select_fg: str | None = None,
        font_delta: int = FONT_SIZE_OFFSETS["BODY"],
        bold: bool = False,
        width: int | None = None,
        takefocus: int = 1,
    ) -> tk.Listbox:
        font_spec: tuple[str, int] | tuple[str, int, str]
        if bold:
            font_spec = (FONT["STYLE"], FONT["SIZE"] + font_delta, "bold")
        else:
            font_spec = (FONT["STYLE"], FONT["SIZE"] + font_delta)
        return tk.Listbox(
            parent,
            bg=THEME["PANEL_BG"],
            fg=fg,
            selectbackground=THEME["SELECT_BG"],
            selectforeground=select_fg or fg,
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            width=width or 0,
            font=font_spec,
            exportselection=False,
            takefocus=takefocus,
        )

    def _create_status_label(
        self,
        parent: tk.Misc,
        status_var: tk.StringVar,
        *,
        fg: str = THEME["FG_MUTED"],
        font_delta: int = FONT_SIZE_OFFSETS["BODY"],
        padx: int = 8,
        pady: int = 6,
    ) -> tk.Label:
        return tk.Label(
            parent,
            textvariable=status_var,
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=fg,
            font=(FONT["STYLE"], FONT["SIZE"] + font_delta),
            padx=padx,
            pady=pady,
        )

    def _set_listbox_selection(
        self,
        listboxes: list[tk.Listbox],
        idx: int,
        item_count: int,
    ) -> None:
        if item_count <= 0:
            return
        idx = max(0, min(idx, item_count - 1))
        for listbox in listboxes:
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(idx)
            listbox.activate(idx)
            listbox.see(idx)

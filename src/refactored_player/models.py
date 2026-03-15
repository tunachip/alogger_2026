from __future__ import annotations

import colorsys
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable

from .constants import LAYOUT, POPUP_SIZES, THEME
from .utils import SEARCH_FIELD_OPTIONS, is_search_field_token


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
        enable_field_completion: bool = False,
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
        self._accent_fg = accent_fg
        self._base_fg = fg
        self._syncing_var = False
        self._syncing_widget = False
        self._completion_enabled = bool(enable_field_completion)
        self._completion_options = list(SEARCH_FIELD_OPTIONS)
        self._completion_parent = parent
        self._completion_listbox = tk.Listbox(
            parent,
            bg=bg,
            fg=fg,
            selectbackground=THEME["SELECT_BG"],
            selectforeground=fg,
            activestyle="none",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=border,
            highlightcolor=border,
            exportselection=False,
            font=font,
            height=min(6, len(self._completion_options)),
        )
        self._completion_listbox.bind(
            "<ButtonRelease-1>",
            lambda _e: self._apply_completion(),
            add="+",
        )
        self._completion_visible = False
        self._completion_token_range: tuple[int, int] | None = None
        self.tag_configure("operator", foreground=accent_fg)
        self.tag_configure("field", foreground=accent_fg)
        self.bind("<KeyPress>", self._on_key_press, add="+")
        self.bind("<KeyRelease>", self._on_widget_change, add="+")
        self.bind("<<Paste>>", self._on_widget_change, add="+")
        self.bind("<<Cut>>", self._on_widget_change, add="+")
        self.bind("<FocusOut>", lambda _e: self.after(50, self._hide_completion), add="+")
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
        self._accent_fg = accent_fg
        self._base_fg = fg
        self.tag_configure("operator", foreground=accent_fg)
        self.tag_configure("field", foreground=accent_fg)
        self._completion_listbox.configure(
            bg=bg,
            fg=fg,
            selectbackground=THEME["SELECT_BG"],
            selectforeground=fg,
            highlightbackground=border,
            highlightcolor=border,
            font=font,
        )
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

    def _clause_palette(self, count: int) -> list[str]:
        token = str(self._accent_fg or "").strip()
        if not token.startswith("#") or len(token) != 7:
            return [self._accent_fg for _ in range(max(1, count))]
        try:
            r = int(token[1:3], 16) / 255.0
            g = int(token[3:5], 16) / 255.0
            b = int(token[5:7], 16) / 255.0
        except Exception:
            return [self._accent_fg for _ in range(max(1, count))]
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        total = 10
        base_colors: list[str] = []
        for idx in range(total):
            nr, ng, nb = colorsys.hls_to_rgb((h + (idx / float(total))) % 1.0, l, max(0.15, s))
            base_colors.append(
                "#{:02x}{:02x}{:02x}".format(
                    int(max(0, min(255, round(nr * 255.0)))),
                    int(max(0, min(255, round(ng * 255.0)))),
                    int(max(0, min(255, round(nb * 255.0)))),
                )
            )
        # Traverse the same hue points in a legible "far apart first" order.
        order = [0, 5, 2, 7, 1, 6, 3, 8, 4, 9]
        colors = [base_colors[idx] for idx in order]
        if count <= total:
            return colors[:count]
        out: list[str] = []
        for idx in range(count):
            out.append(colors[idx % total])
        return out

    def _apply_operator_tags(self) -> None:
        self.tag_remove("operator", "1.0", tk.END)
        self.tag_remove("field", "1.0", tk.END)
        value = self.get()
        clauses = value.split(";")
        palette = self._clause_palette(len(clauses))
        for idx in range(max(1, len(clauses))):
            tag_name = f"clause_{idx}"
            try:
                self.tag_delete(tag_name)
            except Exception:
                pass
            self.tag_configure(tag_name, foreground=palette[idx])
        clause_start = 0
        for idx, clause in enumerate(clauses):
            clause_tag = f"clause_{idx}"
            clause_len = len(clause)
            clause_end = clause_start + clause_len
            if clause_end < len(value):
                self.tag_add(
                    clause_tag,
                    f"1.0+{clause_end}c",
                    f"1.0+{clause_end + 1}c",
                )
                self.tag_add(
                    "operator",
                    f"1.0+{clause_end}c",
                    f"1.0+{clause_end + 1}c",
                )
            for rel_idx, char in enumerate(clause):
                if char in {"|", "&", "*", "$"}:
                    start = clause_start + rel_idx
                    self.tag_add(
                        clause_tag,
                        f"1.0+{start}c",
                        f"1.0+{start + 1}c",
                    )
                    self.tag_add(
                        "operator",
                        f"1.0+{start}c",
                        f"1.0+{start + 1}c",
                    )
            stripped = clause.lstrip()
            leading = len(clause) - len(stripped)
            if stripped:
                token = stripped.split(None, 1)[0]
                if is_search_field_token(token):
                    start_idx = clause_start + leading
                    end_idx = start_idx + len(token)
                    self.tag_add(
                        clause_tag,
                        f"1.0+{start_idx}c",
                        f"1.0+{end_idx}c",
                    )
                    self.tag_add(
                        "field",
                        f"1.0+{start_idx}c",
                        f"1.0+{end_idx}c",
                    )
            clause_start += len(clause) + 1

    def _after_widget_edit(self) -> None:
        self._apply_operator_tags()
        if self._completion_enabled:
            self._refresh_completion()
        else:
            self._hide_completion()
        if self._syncing_var:
            return
        current = self.get()
        if self._textvariable.get() == current:
            return
        self._syncing_widget = True
        self._textvariable.set(current)
        self._syncing_widget = False

    def _on_widget_change(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if _event is not None:
            keysym = str(getattr(_event, "keysym", ""))
            if keysym in {
                "Up",
                "Down",
                "Left",
                "Right",
                "Return",
                "Tab",
                "Escape",
                "Home",
                "End",
                "Prior",
                "Next",
            }:
                return
        self._after_widget_edit()

    def _on_var_change(self, *_args: str) -> None:
        if self._syncing_widget:
            return
        self._set_value(self._textvariable.get())

    def _current_field_fragment(self) -> tuple[int, int, str] | None:
        value = self.get()
        cursor = self._char_offset("insert")
        start = cursor
        while start > 0 and value[start - 1] not in {" ", ";", "\n", "\t"}:
            start -= 1
        end = cursor
        while end < len(value) and value[end] not in {" ", ";", "\n", "\t"}:
            end += 1
        token = value[start:end]
        if not token.startswith("$"):
            return None
        bang = token.find("!")
        visible = token if bang == -1 or cursor <= start + bang + 1 else token[:bang + 1]
        return start, end, visible

    def _completion_matches(self) -> list[str]:
        fragment_info = self._current_field_fragment()
        if not self._completion_enabled:
            return []
        if fragment_info is None:
            return []
        _start, _end, fragment = fragment_info
        lookup = fragment.lower()
        if lookup.endswith("!"):
            lookup = lookup[:-1]
        matches = [
            option
            for option in self._completion_options
            if option.startswith(lookup)
        ]
        return matches or self._completion_options

    def _show_completion(self, options: list[str]) -> None:
        current_value = ""
        sel = self._completion_listbox.curselection()
        if sel:
            try:
                current_value = str(self._completion_listbox.get(sel[0]))
            except Exception:
                current_value = ""
        self._completion_listbox.delete(0, tk.END)
        for option in options:
            self._completion_listbox.insert(tk.END, option)
        if not options:
            self._hide_completion()
            return
        selected_idx = 0
        if current_value and current_value in options:
            selected_idx = options.index(current_value)
        self._completion_listbox.selection_clear(0, tk.END)
        self._completion_listbox.selection_set(selected_idx)
        self._completion_listbox.activate(selected_idx)
        self._completion_listbox.see(selected_idx)
        try:
            self._completion_parent.update_idletasks()
            x = int(self.winfo_x())
            y = int(self.winfo_y() + self.winfo_height())
            width = max(int(self.winfo_width()), 140)
        except Exception:
            return
        self._completion_listbox.place(x=x, y=y, width=width)
        self._completion_listbox.lift()
        self._completion_visible = True

    def _hide_completion(self) -> None:
        if self._completion_visible:
            self._completion_listbox.place_forget()
        self._completion_visible = False
        self._completion_token_range = None

    def _refresh_completion(self) -> None:
        if not self._completion_enabled:
            self._hide_completion()
            return
        fragment_info = self._current_field_fragment()
        if fragment_info is None:
            self._hide_completion()
            return
        start, end, _fragment = fragment_info
        matches = self._completion_matches()
        if not matches:
            self._hide_completion()
            return
        self._completion_token_range = (start, end)
        self._show_completion(matches)

    def _move_completion(self, delta: int) -> str:
        if not self._completion_visible:
            return "break"
        size = int(self._completion_listbox.size())
        if size <= 0:
            return "break"
        sel = self._completion_listbox.curselection()
        cur = int(sel[0]) if sel else 0
        nxt = max(0, min(size - 1, cur + delta))
        self._completion_listbox.selection_clear(0, tk.END)
        self._completion_listbox.selection_set(nxt)
        self._completion_listbox.activate(nxt)
        self._completion_listbox.see(nxt)
        return "break"

    def _apply_completion(self) -> str:
        if not self._completion_visible or self._completion_token_range is None:
            return "break"
        sel = self._completion_listbox.curselection()
        if not sel:
            return "break"
        choice = str(self._completion_listbox.get(sel[0]))
        start, end = self._completion_token_range
        fragment_info = self._current_field_fragment()
        suffix = ""
        if fragment_info is not None:
            token = self.get()[fragment_info[0]:fragment_info[1]]
            if "!" in token:
                suffix = "!"
        value = self.get()
        replacement = choice + suffix
        updated = value[:start] + replacement + value[end:]
        self._set_value(updated)
        self.icursor(start + len(replacement))
        self._after_widget_edit()
        self._hide_completion()
        self.focus_set()
        return "break"

    def completion_is_active(self) -> bool:
        return bool(self._completion_visible)

    def _on_key_press(self, event: tk.Event[tk.Misc]) -> str | None:
        if not self._completion_visible:
            return None
        keysym = str(getattr(event, "keysym", ""))
        if keysym == "Up":
            return self._move_completion(-1)
        if keysym == "Down":
            return self._move_completion(1)
        if keysym in {"Tab", "Return"}:
            return self._apply_completion()
        if keysym == "Escape":
            self._hide_completion()
            return "break"
        return None


class PickerTable(ttk.Treeview):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        columns: list[tuple[str, str, int]],
        bg: str,
        fg: str,
        muted_fg: str,
        border: str,
        select_bg: str,
        select_fg: str,
        retained_bg: str,
        retained_fg: str,
        font: tuple[str, int] | tuple[str, int, str],
        heading_font: tuple[str, int] | tuple[str, int, str],
        on_widths_changed: Callable[[dict[str, int]], None] | None = None,
        on_heading_click: Callable[[str], None] | None = None,
        on_heading_right_click: Callable[[str], None] | None = None,
    ) -> None:
        self._style_name = f"PickerTable{hex(id(self))}.Treeview"
        self._heading_style_name = f"{self._style_name}.Heading"
        style = ttk.Style(parent)
        style.configure(
            self._style_name,
            background=bg,
            fieldbackground=bg,
            foreground=fg,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            borderwidth=0,
            rowheight=max(22, int(font[1]) + 10 if len(font) > 1 else 24),
            font=font,
        )
        style.map(
            self._style_name,
            background=[("selected", select_bg)],
            foreground=[("selected", select_fg)],
        )
        style.configure(
            self._heading_style_name,
            background=bg,
            foreground=muted_fg,
            borderwidth=0,
            relief="flat",
            font=heading_font,
            padding=(8, 4),
        )
        super().__init__(
            parent,
            columns=[name for name, _label, _width in columns],
            show="headings",
            selectmode="browse",
            style=self._style_name,
        )
        self._columns_spec = list(columns)
        self._retained_bg = retained_bg
        self._retained_fg = retained_fg
        self._base_bg = bg
        self._base_fg = fg
        self._select_bg = select_bg
        self._select_fg = select_fg
        self._on_widths_changed = on_widths_changed
        self._on_heading_click = on_heading_click
        self._on_heading_right_click = on_heading_right_click
        self.tag_configure("retained", background=retained_bg, foreground=retained_fg)
        self.tag_configure("normal", background=bg, foreground=fg)
        for name, label, width in columns:
            self.heading(name, text=label)
            self.column(name, width=width, stretch=True, anchor="w", minwidth=48)
        self.bind("<Button-1>", self._on_mouse_down, add="+")
        self.bind("<Button-3>", self._on_mouse_right_down, add="+")
        self.bind("<ButtonRelease-1>", self._emit_widths_changed, add="+")
        self.bind("<Configure>", self._emit_widths_changed, add="+")

    def configure_columns(self, columns: list[tuple[str, str, int]]) -> None:
        self._columns_spec = list(columns)
        self.configure(columns=[name for name, _label, _width in columns])
        self["displaycolumns"] = [name for name, _label, _width in columns]
        for name, label, width in columns:
            self.heading(name, text=label)
            self.column(name, width=width, stretch=True, anchor="w", minwidth=48)

    def replace_rows(
        self,
        rows: list[dict[str, str]],
        *,
        id_key: str = "id",
    ) -> None:
        for item_id in self.get_children():
            self.delete(item_id)
        display_columns = list(self["displaycolumns"])
        for row in rows:
            item_id = str(row.get(id_key) or "")
            values = [str(row.get(column) or "") for column in display_columns]
            if item_id:
                self.insert("", "end", iid=item_id, values=values, tags=("normal",))
            else:
                self.insert("", "end", values=values, tags=("normal",))

    def selected_item_id(self) -> str:
        selection = self.selection()
        return str(selection[0]) if selection else ""

    def select_item_id(self, item_id: str) -> None:
        if not item_id or not self.exists(item_id):
            return
        self.selection_set(item_id)
        self.focus(item_id)
        self.see(item_id)

    def select_index(self, index: int) -> None:
        children = list(self.get_children())
        if not children:
            return
        target = children[max(0, min(index, len(children) - 1))]
        self.select_item_id(str(target))

    def current_index(self) -> int:
        selected = self.selected_item_id()
        if not selected:
            return 0
        children = list(self.get_children())
        try:
            return children.index(selected)
        except ValueError:
            return 0

    def item_id_at_y(self, y: int) -> str:
        return str(self.identify_row(y) or "")

    def set_retained(self, retained_ids: set[str]) -> None:
        selected = self.selected_item_id()
        for item_id in self.get_children():
            tags = ["normal"]
            if str(item_id) in retained_ids and str(item_id) != selected:
                tags = ["retained"]
            self.item(item_id, tags=tuple(tags))

    def _emit_widths_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._on_widths_changed is None:
            return
        widths = {
            str(name): int(self.column(name, "width"))
            for name, _label, _width in self._columns_spec
        }
        self._on_widths_changed(widths)

    def _heading_column_from_event(self, event: tk.Event[tk.Misc]) -> str:
        region = str(self.identify_region(int(getattr(event, "x", 0)), int(getattr(event, "y", 0))) or "")
        if region != "heading":
            return ""
        column_token = str(self.identify_column(int(getattr(event, "x", 0))) or "")
        if not column_token.startswith("#"):
            return ""
        try:
            index = int(column_token[1:]) - 1
        except Exception:
            return ""
        columns = list(self["displaycolumns"])
        if index < 0 or index >= len(columns):
            return ""
        return str(columns[index])

    def _on_mouse_down(self, event: tk.Event[tk.Misc]) -> str | None:
        column = self._heading_column_from_event(event)
        if not column or self._on_heading_click is None:
            return None
        self._on_heading_click(column)
        return "break"

    def _on_mouse_right_down(self, event: tk.Event[tk.Misc]) -> str | None:
        column = self._heading_column_from_event(event)
        if not column or self._on_heading_right_click is None:
            return None
        self._on_heading_right_click(column)
        return "break"

from __future__ import annotations

import json
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from .theme import Colorscheme, Font, Geometry, Theme

@dataclass(slots=True)
class SegmentRow:
    index:      int
    start_sec:  float
    end_sec:    float
    text:       str
    text_lc:    str

def _fmt_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

class PlayerWindow:
    def __init__(self, transcript_json: Path | None = None) -> None:
        self.transcript_json = transcript_json
        self.segments = self._load_segments(transcript_json) if transcript_json else []
        self._segment_starts = [seg.start_sec for seg in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0

        self.root = tk.Tk()
        self.root.title("ALOG Player (Refactor)")
        self.root.geometry(f"{int(Geometry.MAIN_X)}x{int(Geometry.MAIN_Y)}")
        self.root.configure(bg=str(Colorscheme.MAIN_BG))

        font_family = (
            str(Font.USER)
            if str(Font.USER) != "unset"
            else f"{str(Font.BASE)}, {str(Font.FALLBACK)}"
        )
        self._text_font = tkfont.Font(
            family=font_family,
            size=int(Geometry.FONT_SIZE)
        )
        self._text_font_bold = tkfont.Font(
            family=font_family,
            size=int(Geometry.FONT_SIZE),
            weight="bold",
        )
        self._timestamp_prefix = "[00:00:00] "
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)

        self._setup_styles()
        self._build_layout()
        self._bind_keys()
        self._refresh_caption_view()

        if not self.segments:
            self.status_var.set("No transcript loaded.")

    def run(self) -> None:
        self.root.mainloop()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use(str(Theme.BASE))
        style.configure(
            "Filter.TEntry",
            fieldbackground=str(Colorscheme.TEXT_FIELD_BG),
            foreground=str(Colorscheme.TEXT_FIELD_FG),
        )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)

        shell = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=4,
            bg=str(Colorscheme.MAIN_BG),
        )
        shell.grid(row=0, column=0, sticky="nsew")
        self.shell = shell

        left = tk.Frame(shell, bg=str(Colorscheme.PLAYER_BG))
        right = tk.Frame(shell, bg=str(Colorscheme.SIDE_MENU_BG))
        self.left_panel = left
        self.right_panel = right
        shell.add(left, minsize=int(Geometry.LEFT_PANEL_MIN_SIZE))
        shell.add(right, minsize=int(Geometry.SIDE_MENU_MIN_SIZE))

        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.video_panel = tk.Frame(
            left,
            bg=str(Colorscheme.PLAYER_BG),
            highlightthickness=0,
            bd=0,
        )
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        self.clock_var = tk.StringVar(value="00:00:00")
        clock_box = tk.Label(
            left,
            textvariable=self.clock_var,
            anchor="w",
            justify="left",
            bg=str(Colorscheme.PLAYER_BG),
            fg=str(Colorscheme.PLAYER_ACCENT),
            font=(self._text_font.actual("family"),
                  int(Geometry.FONT_SIZE) - 1, "bold"),
            padx=10,
            pady=6,
        )
        clock_box.grid(row=2, column=0, sticky="ew")

        self.caption_now_var = tk.StringVar(value="")
        caption_now_box = tk.Label(
            left,
            textvariable=self.caption_now_var,
            anchor="w",
            justify="left",
            bg=str(Colorscheme.PLAYER_BG),
            fg=str(Colorscheme.PLAYER_FG),
            font=(self._text_font.actual("family"),
                  int(Geometry.FONT_SIZE) - 1),
            padx=10,
            pady=8,
            wraplength=400,
        )
        caption_now_box.grid(row=1, column=0, sticky="ew")

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", self._on_filter_change)
        self.filter_entry = ttk.Entry(
            right,
            textvariable=self.filter_var,
            style="Filter.TEntry",
        )
        self.filter_entry.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=8,
            pady=(8, 6)
        )

        self.caption_view = tk.Text(
            right,
            bg=str(Colorscheme.CAPTION_BASE_BG),
            fg=str(Colorscheme.CAPTION_BASE_FG),
            borderwidth=0,
            highlightthickness=0,
            font=self._text_font,
            wrap="word",
            padx=8,
            pady=8,
            insertbackground=str(Colorscheme.CAPTION_BASE_ACCENT),
        )
        self.caption_view.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=8,
            pady=(0, 8)
        )
        self.caption_view.configure(state="disabled")

        self.caption_view.tag_configure(
            "row", lmargin1=0, lmargin2=self._wrap_indent_px)
        self.caption_view.tag_configure(
            "ts", foreground=str(Colorscheme.CAPTION_TIME_FG))
        self.caption_view.tag_configure(
            "txt", foreground=str(Colorscheme.CAPTION_BASE_FG))
        self.caption_view.tag_configure(
            "match", foreground=str(Colorscheme.CAPTION_MATCH_FG))
        self.caption_view.tag_configure(
            "selected", background=str(Colorscheme.CAPTION_SELECT_BG))
        self.caption_view.tag_configure(
            "selected_txt", font=self._text_font_bold)

        self._row_ranges: list[tuple[str, str]] = []
        self._row_text_ranges: list[tuple[str, str]] = []

        # Single status bar across the full app.
        self.status_var = tk.StringVar(value="Ready.")
        status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            bg=str(Colorscheme.STATUS_BASE_BG),
            fg=str(Colorscheme.STATUS_BASE_FG),
            font=(self._text_font.actual("family"),
                  int(Geometry.FONT_SIZE) - 1),
            padx=10,
            pady=6,
        )
        status_bar.grid(row=1, column=0, sticky="ew")

    def _bind_keys(self) -> None:
        self.root.bind("<Up>", self._on_up)
        self.root.bind("<Down>", self._on_down)
        self.root.bind("<Return>", self._on_return)
        self.root.bind("<Control-c>", self._on_clear_filter)
        self.root.bind("<Escape>", self._on_quit)
        self.caption_view.bind("<Double-Button-1>", self._on_double_click)

    def _load_segments(self, transcript_json: Path) -> list[SegmentRow]:
        if not transcript_json.exists():
            raise FileNotFoundError(f"Transcript not found: {transcript_json}")
        payload = json.loads(transcript_json.read_text(encoding="utf-8"))
        raw_segments = payload.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ValueError("Transcript has no valid 'segments' list")

        segments: list[SegmentRow] = []
        for i, seg in enumerate(raw_segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip().replace("\n", " ")
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            segments.append(
                SegmentRow(
                    index=i,
                    start_sec=start,
                    end_sec=end,
                    text=text,
                    text_lc=text.lower(),
                )
            )
        return segments

    def _on_filter_change(self, *_args: object) -> None:
        query = self.filter_var.get().strip().lower()
        if not query:
            self.filtered_indexes = list(range(len(self.segments)))
        else:
            self.filtered_indexes = [
                i for i, seg in enumerate(self.segments)
                if query in seg.text_lc
            ]
        self.selected_filtered_pos = 0
        self._refresh_caption_view()
        if self.filtered_indexes:
            self.status_var.set(f"{len(self.filtered_indexes)} transcript matches")
        else:
            self.status_var.set("No transcript matches")

    def _refresh_caption_view(self) -> None:
        self.caption_view.configure(state="normal")
        self.caption_view.delete("1.0", tk.END)
        self._row_ranges.clear()
        self._row_text_ranges.clear()

        query = self.filter_var.get().strip().lower()
        for idx in self.filtered_indexes:
            seg = self.segments[idx]
            line_start = self.caption_view.index("end-1c")
            prefix = f"[{_fmt_hms(seg.start_sec)}] "
            self.caption_view.insert(tk.END, prefix + seg.text + "\n", ("row",))
            ts_start = line_start
            ts_end = f"{line_start}+{len(prefix)}c"
            txt_start = ts_end
            txt_end = f"{line_start}+{len(prefix) + len(seg.text)}c"
            self.caption_view.tag_add("ts", ts_start, ts_end)
            self.caption_view.tag_add("txt", txt_start, txt_end)
            self._row_text_ranges.append((txt_start, txt_end))
            line_end = self.caption_view.index("end-1c")
            self._row_ranges.append((line_start, line_end))

            if query:
                pos = 0
                while True:
                    hit = seg.text_lc.find(query, pos)
                    if hit == -1:
                        break
                    pos = hit + len(query)
                    s = f"{line_start}+{len(prefix) + hit}c"
                    e = f"{line_start}+{len(prefix) + pos}c"
                    self.caption_view.tag_add("match", s, e)

        if self.filtered_indexes:
            self._select_pos(self.selected_filtered_pos)
        self.caption_view.configure(state="disabled")

    def _select_pos(self, pos: int) -> None:
        if not self.filtered_indexes:
            return
        self.selected_filtered_pos = max(0, min(pos, len(self.filtered_indexes) - 1))

        self.caption_view.tag_remove("selected", "1.0", tk.END)
        self.caption_view.tag_remove("selected_txt", "1.0", tk.END)
        row_start, row_end = self._row_ranges[self.selected_filtered_pos]
        txt_start, txt_end = self._row_text_ranges[self.selected_filtered_pos]
        self.caption_view.tag_add("selected", row_start, row_end)
        self.caption_view.tag_add("selected_txt", txt_start, txt_end)
        self.caption_view.see(row_start)

        seg = self.segments[self.filtered_indexes[self.selected_filtered_pos]]
        self.clock_var.set(_fmt_hms(seg.start_sec))
        self.caption_now_var.set(seg.text)

    def _jump_to_current_selection(self) -> None:
        if not self.filtered_indexes:
            return
        seg = self.segments[self.filtered_indexes[self.selected_filtered_pos]]
        self.clock_var.set(_fmt_hms(seg.start_sec))
        self.caption_now_var.set(seg.text)
        self.status_var.set(f"Selected segment at {_fmt_hms(seg.start_sec)}")

    def _on_up(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos - 1)
        return "break"

    def _on_down(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos + 1)
        return "break"

    def _on_return(self, _event: tk.Event[tk.Misc]) -> str:
        self._jump_to_current_selection()
        return "break"

    def _on_double_click(self, event: tk.Event[tk.Misc]) -> str:
        idx = self.caption_view.index(f"@{event.x},{event.y}")
        row = int(float(idx)) - 1
        self._select_pos(row)
        self._jump_to_current_selection()
        return "break"

    def _on_clear_filter(self, _event: tk.Event[tk.Misc]) -> str:
        self.filter_var.set("")
        return "break"

    def _on_quit(self, _event: tk.Event[tk.Misc]) -> str:
        self.root.destroy()
        return "break"

def run_gui(transcript_json: Path | None = None) -> None:
    app = PlayerWindow(transcript_json=transcript_json)
    app.run()


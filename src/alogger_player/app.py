from __future__ import annotations
import vlc
import json
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any

from alogger_ingester.config import IngesterConfig
from alogger_ingester.pipeline import (
    _media_has_audio_stream,
    _media_has_video_stream,
    resolve_playback_media_path,
)
from alogger_ingester.service import IngesterService

# === Config ===
FONT = {
    'STYLE': 'DejaVu Sans Mono',
    'SIZE': 14,
}

THEME = {

}

def _fmt_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

@dataclass(slots=True)
class SegmentRow:
    index: int
    start_sec: float
    end_sec: float
    text: str
    text_lc: str

class TranscriptPlayer:
    def __init__(
        self,
        transcript_json: Path | None = None,
        video_path: Path | None = None,
        audio_path: Path | None = None,
        skim_seconds: float = 5.0,
        start_sec: float = 0.0,
    ) -> None:
        self.transcript_json = transcript_json
        self.video_path = video_path
        self.audio_path = audio_path
        self.skim_seconds = skim_seconds
        self.start_sec = max(0.0, float(start_sec))

        self.segments = self._load_segments(transcript_json) if transcript_json else []
        self._segment_starts = [seg.start_sec for seg in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        self._last_esc_time = 0.0
        self._search_popup: tk.Toplevel | None = None
        self._ingest_popup: tk.Toplevel | None = None
        self._jobs_popup: tk.Toplevel | None = None
        self._jobs_text: tk.Text | None = None
        self._jobs_after_id: str | None = None
        self._search_results: list[dict[str, Any]] = []
        self.current_video_id: str | None = None
        self._load_fail_count = 0

        self.ingester_config = IngesterConfig.from_env()
        self.ingester = IngesterService(self.ingester_config)
        self.ingester.init()

        self.root = tk.Tk()
        self.root.title("Alogger Player")
        self.root.geometry("1400x850")
        self.root.configure(bg="#111111")

        self._text_font = tkfont.Font(family=FONT['STYLE'], size=FONT['SIZE'])
        self._text_font_bold = tkfont.Font(family=FONT['STYLE'], size=FONT['SIZE'], weight="bold")
        self._timestamp_prefix = "[00:00:00] "
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)

        self._setup_styles()
        self._build_layout()
        self._bind_keys()
        self._build_vlc()

        self._refresh_caption_view()
        if not self.video_path:
            self.status_var.set("No video loaded. Press Ctrl-F to search DB and load one.")
        self._tick_ui()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Filter.TEntry",
            fieldbackground="#151515",
            foreground="#f0f0f0"
        )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=4
        )
        shell.grid(row=0, column=0, sticky="nsew")
        self.shell = shell

        left = tk.Frame(shell, bg="#000000")
        self.left_panel = left
        right = tk.Frame(shell, bg="#111111")
        shell.add(left, minsize=700)
        shell.add(right, minsize=450)
        self.root.after(0, self._set_initial_split_ratio)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.video_panel = tk.Frame(
            left,
            bg="#000000",
            highlightthickness=0,
            bd=0
        )
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        self.clock_var = tk.StringVar(value="00:00:00")
        clock_box = tk.Label(
            left,
            textvariable=self.clock_var,
            anchor="w",
            justify="left",
            bg="#000000",
            fg="#f7d154",
            font=(FONT['STYLE'], FONT['SIZE']-2, "bold"),
            padx=10,
            pady=6,
        )
        clock_box.grid(row=1, column=0, sticky="ew")

        self.caption_now_var = tk.StringVar(value="")
        self.caption_now_box = tk.Label(
            left,
            textvariable=self.caption_now_var,
            anchor="w",
            justify="left",
            bg="#000000",
            fg="#ffffff",
            font=(FONT['STYLE'], FONT['SIZE']-2),
            padx=10,
            pady=8,
            wraplength=400,
        )
        self.caption_now_box.grid(row=2, column=0, sticky="ew")
        self.left_panel.bind("<Configure>", self._on_left_resize)

        self.status_var = tk.StringVar(value="Idle")
        left_status = tk.Label(
            left,
            textvariable=self.status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#d2d2d2",
            font=(FONT['STYLE'], FONT['SIZE']),
            padx=10,
            pady=6,
        )
        left_status.grid(row=3, column=0, sticky="ew")

        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(
            right,
            textvariable=self.filter_var,
            style="Filter.TEntry"
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
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=self._text_font,
            wrap="word",
            padx=8,
            pady=8,
            insertbackground="#ffffff",
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
            "row",
            lmargin1=0,
            lmargin2=self._wrap_indent_px
        )
        self.caption_view.tag_configure(
            "ts",
            foreground="#8f8f8f"
        )
        self.caption_view.tag_configure(
            "txt",
            foreground="#ffffff"
        )
        self.caption_view.tag_configure(
            "match",
            foreground="#f7d154"
        )
        self.caption_view.tag_configure(
            "selected",
            background="#282828"
        )
        self.caption_view.tag_configure(
            "selected_txt",
            font=self._text_font_bold
        )

        self._row_ranges: list[tuple[str, str]] = []
        self._row_text_ranges: list[tuple[str, str]] = []

        self.hint_var = tk.StringVar(value=(
            "Type=precise filter | Up/Down=hover | Enter=jump | Ctrl-Space=play/pause | "
            "Left/Right=skim | PgUp/PgDn/Home/End move | Ctrl-C clear filter | "
            "Ctrl--/Ctrl-= text size | Ctrl-F search | Ctrl-N ingest | Ctrl-I jobs | Esc Esc=quit"
        ))
        hint = tk.Label(
            right,
            textvariable=self.hint_var,
            anchor="w",
            justify="left",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT['STYLE'], FONT['SIZE']-2),
            padx=8,
            pady=6,
        )
        hint.grid(row=2, column=0, sticky="ew")

    def _bind_keys(self) -> None:
        self.filter_var.trace_add("write", self._on_filter_change)

        self.root.bind("<Up>", self._on_up)
        self.root.bind("<Down>", self._on_down)
        self.root.bind("<Return>", self._on_return)
        self.root.bind("<Control-space>", self._on_toggle_play)
        self.root.bind("<Left>", self._on_left)
        self.root.bind("<Right>", self._on_right)
        self.root.bind("<Prior>", self._on_page_up)
        self.root.bind("<Next>", self._on_page_down)
        self.root.bind("<Home>", self._on_home)
        self.root.bind("<End>", self._on_end)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Control-c>", self._on_clear_filter)
        self.root.bind("<Control-minus>", self._on_font_smaller)
        self.root.bind("<Control-equal>", self._on_font_larger)
        self.root.bind("<Control-plus>", self._on_font_larger)
        self.root.bind("<Control-KeyPress-f>", self._on_open_search_popup)
        self.root.bind("<Control-KeyPress-n>", self._on_open_ingest_popup)
        self.root.bind("<Control-KeyPress-i>", self._on_toggle_jobs_popup)

        self.caption_view.bind("<Double-Button-1>", self._on_double_click)

        self.root.after(50, lambda: self.filter_entry.focus_set())

    def _build_vlc(self) -> None:
        self.instance = vlc.Instance("--quiet", "--no-video-title-show")
        if not self.instance:
            raise Exception('self.instance failed to load.')

        self.player = self.instance.media_player_new()
        if self.video_path:
            if not self.video_path.exists():
                raise FileNotFoundError(f"video path not found: {self.video_path}")
            self._set_player_media(self.video_path, self.audio_path, self.start_sec)

    def _set_player_media(self, video_path: Path, audio_path: Path | None, start_sec: float = 0.0) -> None:
        if not video_path.exists():
            raise FileNotFoundError(f"video path not found: {video_path}")
        self.video_path = video_path
        self.audio_path = audio_path

        try:
            self.player.stop()
        except Exception:
            pass

        media = self.instance.media_new_path(str(video_path))
        if audio_path:
            if not audio_path.exists():
                raise FileNotFoundError(f"audio path not found: {audio_path}")
            media.add_option(f"input-slave={audio_path}")
        self.player.set_media(media)

        self.root.update_idletasks()
        handle = self.video_panel.winfo_id()

        self._bind_video_output(handle)

        self.player.play()
        self.root.after(550, lambda: self._post_media_load(start_sec, retry_without_audio=audio_path is not None))

    def _bind_video_output(self, handle: int) -> None:
        if sys.platform.startswith("linux"):
            self.player.set_xwindow(handle)
            return
        if sys.platform == "win32":
            self.player.set_hwnd(handle)
            return
        if sys.platform == "darwin":
            self.player.set_nsobject(handle)

    def _post_media_load(self, start_sec: float, *, retry_without_audio: bool) -> None:
        state = self.player.get_state()
        if state in {vlc.State.Ended, vlc.State.Error, vlc.State.Stopped}:
            if retry_without_audio and self.audio_path is not None:
                self.status_var.set("Media failed with sidecar audio, retrying video-only...")
                self._set_player_media(self.video_path, None, start_sec=start_sec)
                return

            alt = self._pick_alternate_video_path()
            if alt is not None:
                self._load_fail_count += 1
                self.status_var.set(
                    f"Media load failed ({self.video_path.name}, {state}); trying {alt.name}..."
                )
                self._set_player_media(alt, None, start_sec=start_sec)
                return

            self.status_var.set(f"Failed to load media: {self.video_path} (state={state})")
            return
        if start_sec > 0:
            self.player.set_time(int(start_sec * 1000.0))
        self.player.set_pause(1)
        self._load_fail_count = 0

    def _pick_alternate_video_path(self) -> Path | None:
        if not self.current_video_id:
            return None
        if self._load_fail_count >= 2:
            return None

        candidates: list[Path] = []
        for p in sorted(self.ingester_config.media_dir.glob(f"{self.current_video_id}*")):
            if not p.is_file() or p == self.video_path:
                continue
            if _media_has_video_stream(p) is True:
                candidates.append(p)
        if not candidates:
            return None

        ext_rank = {".mkv": 5, ".mp4": 4, ".webm": 3, ".mov": 2, ".m4v": 2}
        candidates.sort(
            key=lambda p: (ext_rank.get(p.suffix.lower(), 0), p.stat().st_size),
            reverse=True,
        )
        return candidates[0]

    def _load_segments(self, transcript_json: Path) -> list[SegmentRow]:
        if not transcript_json.exists():
            raise FileNotFoundError(f"transcript json not found: {transcript_json}")
        payload = json.loads(transcript_json.read_text(encoding="utf-8"))
        raw_segments = payload.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ValueError("transcript JSON has no valid 'segments' list")

        rows: list[SegmentRow] = []
        for i, seg in enumerate(raw_segments):
            text = str(seg.get("text", "")).strip().replace("\n", " ")
            if not text:
                continue
            start_sec = float(seg.get("start", 0.0))
            end_sec = float(seg.get("end", start_sec))
            rows.append(
                SegmentRow(
                    index=i,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    text=text,
                    text_lc=text.lower(),
                )
            )
        return rows

    def _on_filter_change(self, *_args: object) -> None:
        query = self.filter_var.get().strip().lower()
        if not query:
            self.filtered_indexes = list(range(len(self.segments)))
        else:
            self.filtered_indexes = [
                idx for idx, seg in enumerate(self.segments) if query in seg.text_lc
            ]
        self.selected_filtered_pos = 0
        self._refresh_caption_view()

    def _refresh_caption_view(self) -> None:
        self.caption_view.configure(state="normal")
        self.caption_view.delete("1.0", tk.END)
        self._row_ranges = []
        self._row_text_ranges = []
        query = self.filter_var.get().strip().lower()

        for seg_idx in self.filtered_indexes:
            seg = self.segments[seg_idx]
            line_start = self.caption_view.index("end-1c")
            prefix = f"[{_fmt_hms(seg.start_sec)}] "
            self.caption_view.insert(tk.END, prefix + seg.text + "\n", ("row",))
            self.caption_view.tag_add("ts", line_start, f"{line_start}+{len(prefix)}c")
            self.caption_view.tag_add(
                "txt",
                f"{line_start}+{len(prefix)}c",
                f"{line_start}+{len(prefix) + len(seg.text)}c",
            )
            self._row_text_ranges.append((
                f"{line_start}+{len(prefix)}c",
                f"{line_start}+{len(prefix) + len(seg.text)}c",
            ))
            line_end = self.caption_view.index("end-1c")
            self._row_ranges.append((line_start, line_end))

            if query:
                pos = 0
                while True:
                    hit = seg.text_lc.find(query, pos)
                    if hit == -1:
                        break
                    s = f"{line_start}+{len(prefix) + hit}c"
                    e = f"{line_start}+{len(prefix) + hit + len(query)}c"
                    self.caption_view.tag_add("match", s, e)
                    pos = hit + len(query)

        if self.filtered_indexes:
            self._select_pos(self.selected_filtered_pos)
        else:
            self.status_var.set("No matching transcript segments")
        self.caption_view.configure(state="disabled")

    def _select_pos(self, pos: int) -> None:
        if not self.filtered_indexes:
            return
        pos = max(0, min(pos, len(self.filtered_indexes) - 1))
        self.selected_filtered_pos = pos

        self.caption_view.configure(state="normal")
        self.caption_view.tag_remove("selected", "1.0", tk.END)
        self.caption_view.tag_remove("selected_txt", "1.0", tk.END)
        line_start, line_end = self._row_ranges[pos]
        self.caption_view.tag_add("selected", line_start, line_end)
        text_start, text_end = self._row_text_ranges[pos]
        self.caption_view.tag_add("selected_txt", text_start, text_end)
        self.caption_view.see(line_start)
        self.caption_view.configure(state="disabled")

        seg = self.segments[self.filtered_indexes[pos]]
        self.status_var.set(
            f"Hovering segment #{seg.index} @ {_fmt_hms(seg.start_sec)} | "
            f"matches={len(self.filtered_indexes)}"
        )

    def _current_segment(self) -> SegmentRow | None:
        if not self.filtered_indexes: return None
        return self.segments[self.filtered_indexes[self.selected_filtered_pos]]

    def _on_up(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos - 1)
        return "break"

    def _on_down(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos + 1)
        return "break"

    def _on_return(self, _event: tk.Event[tk.Misc]) -> str:
        seg = self._current_segment()
        if seg is None: return "break"
        self.player.set_time(int(seg.start_sec * 1000.0))
        self.status_var.set(f"Jumped to {_fmt_hms(seg.start_sec)}")
        return "break"

    def _on_double_click(self, event: tk.Event[tk.Misc]) -> str:
        click_index = self.caption_view.index(f"@{event.x},{event.y}")
        line = int(click_index.split(".")[0]) - 1
        if 0 <= line < len(self.filtered_indexes):
            self._select_pos(line)
        return self._on_return(event)

    def _on_toggle_play(self, _event: tk.Event[tk.Misc]) -> str:
        state = self.player.get_state()
        if state == vlc.State.Playing:
            self.player.pause()
            self.status_var.set("Paused")
        elif state in {vlc.State.Ended, vlc.State.Error, vlc.State.Stopped}:
            self.player.set_time(0)
            self.player.play()
            self.status_var.set("Playing")
        elif state == vlc.State.Paused:
            self.player.play()
            self.status_var.set("Playing")
        else:
            self.player.play()
            self.status_var.set("Playing")
        return "break"

    def _on_left(self, _event: tk.Event[tk.Misc]) -> str:
        self._seek_relative(-self.skim_seconds)
        return "break"

    def _on_right(self, _event: tk.Event[tk.Misc]) -> str:
        self._seek_relative(self.skim_seconds)
        return "break"

    def _on_escape(self, _event: tk.Event[tk.Misc]) -> str:
        now = time.monotonic()
        if now - self._last_esc_time <= 0.45:
            self.close()
            return "break"
        self._last_esc_time = now
        self.status_var.set("Press Esc again to quit")
        return "break"

    def _on_page_up(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos - 10)
        return "break"

    def _on_page_down(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos + 10)
        return "break"

    def _on_home(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(0)
        return "break"

    def _on_end(self, _event: tk.Event[tk.Misc]) -> str:
        if self.filtered_indexes:
            self._select_pos(len(self.filtered_indexes) - 1)
        return "break"

    def _on_clear_filter(self, _event: tk.Event[tk.Misc]) -> str:
        self.filter_var.set("")
        self.filter_entry.focus_set()
        self.status_var.set("Filter cleared")
        return "break"

    def _on_font_smaller(self, _event: tk.Event[tk.Misc]) -> str:
        self._resize_caption_font(-1)
        return "break"

    def _on_font_larger(self, _event: tk.Event[tk.Misc]) -> str:
        self._resize_caption_font(1)
        return "break"

    def _resize_caption_font(self, delta: int) -> None:
        current = int(self._text_font.cget("size"))
        new_size = max(8, min(30, current + delta))
        if new_size == current: return
        self._text_font.configure(size=new_size)
        self._text_font_bold.configure(size=new_size)
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)
        self.caption_view.tag_configure("row", lmargin1=0, lmargin2=self._wrap_indent_px)
        self._refresh_caption_view()
        self.status_var.set(f"Caption text size: {new_size}")

    def _seek_relative(self, delta_sec: float) -> None:
        now_ms = self.player.get_time()
        if now_ms < 0: now_ms = 0
        target_ms = int(max(0.0, (now_ms / 1000.0) + delta_sec) * 1000.0)
        self.player.set_time(target_ms)
        self.status_var.set(f"Seek -> {_fmt_hms(target_ms / 1000.0)}")

    def _tick_ui(self) -> None:
        state = self.player.get_state()
        pos_ms = self.player.get_time()
        if pos_ms < 0: pos_ms = 0
        pos_sec = pos_ms / 1000.0
        length_ms = self.player.get_length()
        length_sec = max(0.0, length_ms / 1000.0) if length_ms and length_ms > 0 else 0.0
        self.clock_var.set(self._render_time_progress(pos_sec, length_sec))
        self.caption_now_var.set(self._caption_text_at(pos_sec))
        if state == vlc.State.Playing:
            self.status_var.set(f"Playing @ {_fmt_hms(pos_sec)}")
        self.root.after(250, self._tick_ui)

    def _caption_text_at(self, pos_sec: float) -> str:
        if not self.segments: return ""
        idx = bisect_right(self._segment_starts, pos_sec) - 1
        if idx < 0 or idx >= len(self.segments): return ""
        seg = self.segments[idx]
        if seg.start_sec <= pos_sec <= seg.end_sec: return seg.text
        return ""

    def _render_time_progress(self, pos_sec: float, length_sec: float) -> str:
        bar_width = 28
        if length_sec <= 0:
            return f"[{_fmt_hms(pos_sec)}] {'░' * bar_width}"
        ratio = max(0.0, min(1.0, pos_sec / length_sec))
        filled = int(round(ratio * bar_width))
        bar = ("█" * filled) + ("░" * (bar_width - filled))
        return f"[{_fmt_hms(pos_sec)}] {bar}"

    def _on_left_resize(self, event: tk.Event[tk.Misc]) -> None:
        width = int(getattr(event, "width", 0))
        if width <= 0: return
        # Keep caption wrapping inside the left panel with padding.
        self.caption_now_box.configure(wraplength=max(120, width - 24))

    def _set_initial_split_ratio(self) -> None:
        total_w = self.shell.winfo_width()
        if total_w <= 0: return
        # 3:2 split means left pane should occupy 60% of total width.
        x = int(total_w * 3 / 5)
        self.shell.sash_place(0, x, 0)

    def _on_open_search_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._open_search_popup()
        return "break"

    def _on_open_ingest_popup(self, _event: tk.Event[tk.Misc]) -> str:
        self._open_ingest_popup()
        return "break"

    def _on_toggle_jobs_popup(self, _event: tk.Event[tk.Misc]) -> str:
        if self._jobs_popup and self._jobs_popup.winfo_exists():
            self._close_jobs_popup()
        else:
            self._open_jobs_popup()
        return "break"

    def _apply_popup_style(self, popup: tk.Toplevel, title: str, size: str) -> None:
        popup.title(title)
        popup.geometry(size)
        popup.configure(bg="#111111")
        popup.transient(self.root)

    def _open_search_popup(self) -> None:
        if self._search_popup and self._search_popup.winfo_exists():
            self._search_popup.focus_force()
            return

        popup = tk.Toplevel(self.root)
        self._apply_popup_style(popup, "Search DB", "900x620")
        self._search_popup = popup
        popup.rowconfigure(1, weight=1)
        popup.columnconfigure(0, weight=1)

        query_var = tk.StringVar(value=self.filter_var.get().strip())
        query_entry = ttk.Entry(popup, textvariable=query_var, style="Filter.TEntry")
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        listbox = tk.Listbox(
            popup,
            bg="#000000",
            fg="#ffffff",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            exportselection=False,
        )
        listbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        hint = tk.Label(
            popup,
            text="Type query, Up/Down select, Enter open video, Esc close",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        hint.grid(row=2, column=0, sticky="ew")

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            listbox.delete(0, tk.END)
            self._search_results = []
            if not query:
                return
            rows = self.ingester.search_videos(query, limit=200)
            self._search_results = [dict(r) for r in rows]
            for row in self._search_results:
                title = str(row.get("title") or row.get("video_id") or "untitled").replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                listbox.insert(tk.END, f"{count:>4}  {title}")
            if self._search_results:
                listbox.selection_set(0)
                listbox.activate(0)

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._search_results):
                return "break"
            row = self._search_results[idx]
            query = query_var.get().strip()
            video_id = str(row.get("video_id") or "")
            transcript_path = Path(str(row.get("transcript_json_path") or ""))
            preferred = Path(str(row.get("local_video_path") or "")) if row.get("local_video_path") else None
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

        query_var.trace_add("write", refresh_results)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.bind("<Return>", open_selected)
        listbox.bind("<Double-Button-1>", open_selected)
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        refresh_results()
        query_entry.focus_set()

    def _open_ingest_popup(self) -> None:
        if self._ingest_popup and self._ingest_popup.winfo_exists():
            self._ingest_popup.focus_force()
            return

        popup = tk.Toplevel(self.root)
        self._apply_popup_style(popup, "Ingest URL", "880x160")
        self._ingest_popup = popup
        popup.columnconfigure(0, weight=1)

        url_var = tk.StringVar()
        entry = ttk.Entry(popup, textvariable=url_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        status = tk.StringVar(value="Enter URL and press Enter")
        status_lbl = tk.Label(
            popup,
            textvariable=status,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))

        def enqueue_now(_event: tk.Event[tk.Misc] | None = None) -> str:
            url = url_var.get().strip()
            if not url:
                status.set("URL required")
                return "break"
            try:
                ids = self.ingester.enqueue([url])
                status.set(f"Queued job_id={ids[0]}")
                self.status_var.set(f"Queued ingest job {ids[0]}")
                url_var.set("")
            except Exception as exc:
                status.set(f"Error: {exc}")
            return "break"

        popup.bind("<Return>", enqueue_now)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        entry.focus_set()

    def _open_jobs_popup(self) -> None:
        popup = tk.Toplevel(self.root)
        self._apply_popup_style(popup, "Ingest Jobs", "900x520")
        self._jobs_popup = popup
        popup.rowconfigure(0, weight=1)
        popup.columnconfigure(0, weight=1)

        text = tk.Text(
            popup,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="none",
            padx=8,
            pady=8,
        )
        text.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 8))
        text.configure(state="disabled")
        self._jobs_text = text

        popup.bind("<Escape>", lambda _e: self._close_jobs_popup())
        popup.protocol("WM_DELETE_WINDOW", self._close_jobs_popup)
        self._refresh_jobs_popup()

    def _close_jobs_popup(self) -> None:
        if self._jobs_after_id:
            try:
                self.root.after_cancel(self._jobs_after_id)
            except Exception:
                pass
            self._jobs_after_id = None
        if self._jobs_popup and self._jobs_popup.winfo_exists():
            self._jobs_popup.destroy()
        self._jobs_popup = None
        self._jobs_text = None
        self.filter_entry.focus_set()

    def _refresh_jobs_popup(self) -> None:
        if not self._jobs_popup or not self._jobs_popup.winfo_exists() or not self._jobs_text:
            return
        try:
            snapshot = self.ingester.jobs_summary(limit=30)
            counts = snapshot.get("counts", {})
            jobs = snapshot.get("jobs", [])
            lines = [
                f"queued={counts.get('queued', 0)}  "
                f"downloading={counts.get('downloading', 0)}  "
                f"transcribing={counts.get('transcribing', 0)}  "
                f"done={counts.get('done', 0)}  "
                f"failed={counts.get('failed', 0)}",
                "",
                "id    status        video_id       created_at",
            ]
            for row in jobs:
                lines.append(
                    f"{str(row.get('id', '')):<5} "
                    f"{str(row.get('status', '')):<12} "
                    f"{str(row.get('video_id') or '-'):<13} "
                    f"{str(row.get('created_at') or '-')}"
                )
            payload = "\n".join(lines)
        except Exception as exc:
            payload = f"Failed to load ingest jobs: {exc}"

        self._jobs_text.configure(state="normal")
        self._jobs_text.delete("1.0", tk.END)
        self._jobs_text.insert("1.0", payload)
        self._jobs_text.configure(state="disabled")
        self._jobs_after_id = self.root.after(1000, self._refresh_jobs_popup)

    def _find_audio_sidecar(self, video_id: str, video_path: Path) -> Path | None:
        if _media_has_audio_stream(video_path) is True:
            return None
        candidates = sorted(self.ingester_config.media_dir.glob(f"{video_id}*"))
        audio_only: list[Path] = []
        for p in candidates:
            if not p.is_file() or p == video_path:
                continue
            has_audio = _media_has_audio_stream(p)
            has_video = _media_has_video_stream(p)
            if has_audio is True and has_video is False:
                audio_only.append(p)
        if not audio_only:
            return None
        return sorted(audio_only, key=lambda p: p.stat().st_size, reverse=True)[0]

    def _load_session(
        self,
        *,
        video_id: str,
        transcript_json: Path,
        video_path: Path,
        audio_path: Path | None,
        start_sec: float,
        filter_text: str,
    ) -> None:
        self.current_video_id = video_id
        self._load_fail_count = 0
        self.transcript_json = transcript_json
        self.segments = self._load_segments(transcript_json)
        self._segment_starts = [seg.start_sec for seg in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        self._set_player_media(video_path, audio_path, start_sec=start_sec)
        self.filter_var.set(filter_text)
        if not filter_text:
            self._refresh_caption_view()
        self.status_var.set(f"Loaded video at {_fmt_hms(start_sec)}")

    def close(self) -> None:
        self._close_jobs_popup()
        if self._search_popup and self._search_popup.winfo_exists():
            self._search_popup.destroy()
        if self._ingest_popup and self._ingest_popup.winfo_exists():
            self._ingest_popup.destroy()
        try:
            self.player.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        try: self.root.mainloop()
        finally:
            try: self.player.stop()
            except Exception:
                pass


def run_player(
    transcript_json: Path | None = None,
    video_path: Path | None = None,
    *,
    audio_path: Path | None = None,
    skim_seconds: float = 5.0,
    start_sec: float = 0.0,
) -> None:
    app = TranscriptPlayer(
        transcript_json=transcript_json,
        video_path=video_path,
        audio_path=audio_path,
        skim_seconds=skim_seconds,
        start_sec=start_sec,
    )
    app.run()

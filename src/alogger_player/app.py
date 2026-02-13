from __future__ import annotations

import json
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any

import vlc


def _fmt_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


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
        transcript_json: Path,
        video_path: Path,
        audio_path: Path | None = None,
        skim_seconds: float = 5.0,
    ) -> None:
        self.transcript_json = transcript_json
        self.video_path = video_path
        self.audio_path = audio_path
        self.skim_seconds = skim_seconds

        self.segments = self._load_segments(transcript_json)
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        self._last_esc_time = 0.0

        self.root = tk.Tk()
        self.root.title("Alogger Player")
        self.root.geometry("1400x850")
        self.root.configure(bg="#111216")

        self._setup_styles()
        self._build_layout()
        self._bind_keys()
        self._build_vlc()

        self._refresh_listbox()
        self._tick_ui()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Filter.TEntry", fieldbackground="#171923", foreground="#e6e6e6")

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, sashwidth=4)
        shell.grid(row=0, column=0, sticky="nsew")

        left = tk.Frame(shell, bg="#000000")
        right = tk.Frame(shell, bg="#111216")
        shell.add(left, minsize=700)
        shell.add(right, minsize=450)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.video_panel = tk.Frame(left, bg="#000000", highlightthickness=0, bd=0)
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        self.status_var = tk.StringVar(value="Idle")
        left_status = tk.Label(
            left,
            textvariable=self.status_var,
            anchor="w",
            bg="#0d0e12",
            fg="#d2d2d2",
            font=("DejaVu Sans Mono", 10),
            padx=10,
            pady=6,
        )
        left_status.grid(row=1, column=0, sticky="ew")

        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(right, textvariable=self.filter_var, style="Filter.TEntry")
        self.filter_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        self.listbox = tk.Listbox(
            right,
            bg="#12141c",
            fg="#dce0e8",
            selectbackground="#2d3f78",
            selectforeground="#f8faff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=("DejaVu Sans Mono", 11),
            exportselection=False,
        )
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        self.hint_var = tk.StringVar(
            value=(
                "Type=precise filter | Up/Down=hover | Enter=jump | Ctrl-Space=play/pause | "
                "Left/Right=skim | Esc Esc=quit"
            )
        )
        hint = tk.Label(
            right,
            textvariable=self.hint_var,
            anchor="w",
            justify="left",
            bg="#0d0e12",
            fg="#9ca2b5",
            font=("DejaVu Sans Mono", 9),
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
        self.root.bind("<Escape>", self._on_escape)

        self.listbox.bind("<Double-Button-1>", self._on_return)

        self.root.after(50, lambda: self.filter_entry.focus_set())

    def _build_vlc(self) -> None:
        if not self.video_path.exists():
            raise FileNotFoundError(f"video path not found: {self.video_path}")

        self.instance = vlc.Instance("--quiet", "--no-video-title-show")
        self.player = self.instance.media_player_new()

        media = self.instance.media_new_path(str(self.video_path))
        if self.audio_path:
            if not self.audio_path.exists():
                raise FileNotFoundError(f"audio path not found: {self.audio_path}")
            media.add_option(f"input-slave={self.audio_path}")
        self.player.set_media(media)

        self.root.update_idletasks()
        handle = self.video_panel.winfo_id()

        # Linux/X11 embedding
        self.player.set_xwindow(handle)

        self.player.play()
        self.root.after(200, lambda: self.player.set_pause(1))

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
        self._refresh_listbox()

    def _refresh_listbox(self) -> None:
        self.listbox.delete(0, tk.END)
        for seg_idx in self.filtered_indexes:
            seg = self.segments[seg_idx]
            line = f"[{_fmt_hms(seg.start_sec)}] {seg.text}"
            self.listbox.insert(tk.END, line)

        if self.filtered_indexes:
            self._select_pos(self.selected_filtered_pos)
        else:
            self.status_var.set("No matching transcript segments")

    def _select_pos(self, pos: int) -> None:
        if not self.filtered_indexes:
            return
        pos = max(0, min(pos, len(self.filtered_indexes) - 1))
        self.selected_filtered_pos = pos

        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(pos)
        self.listbox.activate(pos)
        self.listbox.see(pos)

        seg = self.segments[self.filtered_indexes[pos]]
        self.status_var.set(
            f"Hovering segment #{seg.index} @ {_fmt_hms(seg.start_sec)} | "
            f"matches={len(self.filtered_indexes)}"
        )

    def _current_segment(self) -> SegmentRow | None:
        if not self.filtered_indexes:
            return None
        return self.segments[self.filtered_indexes[self.selected_filtered_pos]]

    def _on_up(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos - 1)
        return "break"

    def _on_down(self, _event: tk.Event[tk.Misc]) -> str:
        self._select_pos(self.selected_filtered_pos + 1)
        return "break"

    def _on_return(self, _event: tk.Event[tk.Misc]) -> str:
        seg = self._current_segment()
        if seg is None:
            return "break"
        self.player.set_time(int(seg.start_sec * 1000.0))
        self.status_var.set(f"Jumped to {_fmt_hms(seg.start_sec)}")
        return "break"

    def _on_toggle_play(self, _event: tk.Event[tk.Misc]) -> str:
        self.player.pause()
        state = self.player.get_state()
        if state == vlc.State.Playing:
            self.status_var.set("Playing")
        elif state == vlc.State.Paused:
            self.status_var.set("Paused")
        else:
            self.status_var.set(f"State: {state}")
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

    def _seek_relative(self, delta_sec: float) -> None:
        now_ms = self.player.get_time()
        if now_ms < 0:
            now_ms = 0
        target_ms = int(max(0.0, (now_ms / 1000.0) + delta_sec) * 1000.0)
        self.player.set_time(target_ms)
        self.status_var.set(f"Seek -> {_fmt_hms(target_ms / 1000.0)}")

    def _tick_ui(self) -> None:
        state = self.player.get_state()
        pos_ms = self.player.get_time()
        if pos_ms < 0:
            pos_ms = 0
        if state == vlc.State.Playing:
            self.status_var.set(f"Playing @ {_fmt_hms(pos_ms / 1000.0)}")
        self.root.after(250, self._tick_ui)

    def close(self) -> None:
        try:
            self.player.stop()
        finally:
            self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            try:
                self.player.stop()
            except Exception:
                pass


def run_player(
    transcript_json: Path,
    video_path: Path,
    *,
    audio_path: Path | None = None,
    skim_seconds: float = 5.0,
) -> None:
    app = TranscriptPlayer(
        transcript_json=transcript_json,
        video_path=video_path,
        audio_path=audio_path,
        skim_seconds=skim_seconds,
    )
    app.run()

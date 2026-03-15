from __future__ import annotations

import json
import tkinter as tk
from bisect import bisect_right
from pathlib import Path

import vlc

from .models import SegmentRow
from .utils import format_hms as _fmt_hms, matches_search_query, search_terms


class TranscriptMixin:
    def _load_segments(self, transcript_json: Path) -> list[SegmentRow]:
        if not transcript_json.exists():
            raise FileNotFoundError(
                f"transcript json not found: {transcript_json}")
        payload = json.loads(transcript_json.read_text(encoding="utf-8"))
        raw_segments = payload.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ValueError("transcript JSON has no valid 'segments' list")
        rows: list[SegmentRow] = []
        for index, segment in enumerate(raw_segments):
            text = str(segment.get("text", "")).strip().replace("\n", " ")
            if not text:
                continue
            start_sec = float(segment.get("start", 0.0))
            end_sec = float(segment.get("end", start_sec))
            rows.append(
                SegmentRow(
                    index=index,
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
                idx
                for idx, seg in enumerate(self.segments)
                if matches_search_query(seg.text_lc, query)
            ]
        self.selected_filtered_pos = 0
        self._refresh_caption_view()
        if self._skim_mode:
            self._skim_cursor = 0
            self._start_skim_at_cursor(force_seek=True)

    def _refresh_caption_view(self) -> None:
        self.caption_view.configure(state="normal")
        self.caption_view.delete("1.0", tk.END)
        self._row_ranges = []
        self._row_text_ranges = []
        query = self.filter_var.get().strip().lower()
        terms = search_terms(query)
        for seg_idx in self.filtered_indexes:
            segment = self.segments[seg_idx]
            line_start = self.caption_view.index("end-1c")
            prefix = f"[{_fmt_hms(segment.start_sec)}] "
            self.caption_view.insert(
                tk.END,
                prefix + segment.text + "\n",
                ("row",))
            self.caption_view.tag_add(
                "ts",
                line_start,
                f"{line_start}+{len(prefix)}c")
            self.caption_view.tag_add(
                "txt",
                f"{line_start}+{len(prefix)}c",
                f"{line_start}+{len(prefix) + len(segment.text)}c")
            self._row_text_ranges.append((
                f"{line_start}+{len(prefix)}c",
                f"{line_start}+{len(prefix) + len(segment.text)}c"
            ))
            line_end = self.caption_view.index("end-1c")
            self._row_ranges.append((line_start, line_end))
            for term in terms:
                pos = 0
                while True:
                    hit = segment.text_lc.find(term, pos)
                    if hit == -1:
                        break
                    start = f"{line_start}+{len(prefix) + hit}c"
                    end = f"{line_start}+{len(prefix) + hit + len(term)}c"
                    self.caption_view.tag_add("match", start, end)
                    pos = hit + len(term)
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
        segment = self.segments[self.filtered_indexes[pos]]
        _i = segment.index
        _s = _fmt_hms(segment.start_sec)
        _m = len(self.filtered_indexes)
        self.status_var.set(f"Hovering segment #{_i} @ {_s} | matches={_m}")

    def _current_segment(self) -> SegmentRow | None:
        if not self.filtered_indexes:
            return None
        return self.segments[self.filtered_indexes[self.selected_filtered_pos]]

    def _on_up(self, _event: tk.Event[tk.Misc]) -> str:
        if self._transcript_hidden:
            return "break"
        self._select_pos(self.selected_filtered_pos - 1)
        return "break"

    def _on_down(self, _event: tk.Event[tk.Misc]) -> str:
        if self._transcript_hidden:
            return "break"
        self._select_pos(self.selected_filtered_pos + 1)
        return "break"

    def _on_return(self, _event: tk.Event[tk.Misc]) -> str:
        segment = self._current_segment()
        if segment is None:
            return "break"
        self._seek_to_absolute(segment.start_sec)
        self.status_var.set(f"Jumped to {_fmt_hms(segment.start_sec)}")
        return "break"

    def _on_double_click(self, event: tk.Event[tk.Misc]) -> str:
        click_index = self.caption_view.index(f"@{event.x},{event.y}")
        row = self._row_from_text_index(click_index)
        if row is not None:
            self._select_pos(row)
        return self._on_return(event)

    def _on_click_seek_transcript(self, event: tk.Event[tk.Misc]) -> str:
        click_index = self.caption_view.index(f"@{event.x},{event.y}")
        row = self._row_from_text_index(click_index)
        if row is None:
            return "break"
        self._select_pos(row)
        segment = self.segments[self.filtered_indexes[row]]
        text_start, text_end = self._row_text_ranges[row]
        try:
            click_abs = int(self.caption_view.count(
                "1.0",
                click_index,
                "chars"
            )[0])
            start_abs = int(self.caption_view.count(
                "1.0",
                text_start,
                "chars"
            )[0])
            end_abs = int(self.caption_view.count(
                "1.0",
                text_end,
                "chars"
            )[0])
        except Exception:
            self._seek_to_absolute(segment.start_sec)
            return "break"
        width = max(1, end_abs - start_abs)
        ratio = max(0.0, min(1.0, (click_abs - start_abs) / float(width)))
        target = (
            segment.start_sec
            + (segment.end_sec - segment.start_sec)
            * ratio
        )
        self._seek_to_absolute(target)
        _s = int(ratio * 100)
        _t = _fmt_hms(target)
        self.status_var.set(f"Seek ~{_s}% of segment @ {_t}")
        return "break"

    def _row_from_text_index(self, index: str) -> int | None:
        for row_index, (start, end) in enumerate(self._row_ranges):
            if (
                self.caption_view.compare(index, ">=", start)
                and self.caption_view.compare(index, "<=", end)
            ):
                return row_index
        return None

    def _on_click_video_toggle(self, event: tk.Event[tk.Misc]) -> str:
        return self._on_toggle_play(event)

    def _on_toggle_play(self, _event: tk.Event[tk.Misc]) -> str:
        state = self.player.get_state()
        match state:
            case vlc.State.Playing:
                self.player.set_pause(1)
                self.status_var.set("Paused")
            case vlc.State.Ended | vlc.State.Error | vlc.State.Stopped:
                self._seek_to_absolute(0.0)
                self.status_var.set("Playing")
            case vlc.State.Paused:
                self.player.set_pause(0)
                self.status_var.set("Playing")
            case _:
                self.player.play()
                self.root.after(120, lambda: self.player.set_pause(0))
                self.status_var.set("Playing")
        self._refresh_transport_controls()
        return "break"

    def _on_left(self, _event: tk.Event[tk.Misc]) -> str:
        if self._transcript_hidden:
            self._seek_relative(-self.skim_seconds)
            return "break"
        self.filter_entry.focus_set()
        try:
            pos = int(self.filter_entry.index(tk.INSERT))
            self.filter_entry.icursor(max(0, pos - 1))
        except Exception:
            pass
        return "break"

    def _on_right(self, _event: tk.Event[tk.Misc]) -> str:
        if self._transcript_hidden:
            self._seek_relative(self.skim_seconds)
            return "break"
        self.filter_entry.focus_set()
        try:
            pos = int(self.filter_entry.index(tk.INSERT))
            self.filter_entry.icursor(pos + 1)
        except Exception:
            pass
        return "break"

    def _on_ctrl_left(self, _event: tk.Event[tk.Misc]) -> str:
        self._seek_relative(-self.skim_seconds)
        return "break"

    def _on_ctrl_right(self, _event: tk.Event[tk.Misc]) -> str:
        self._seek_relative(self.skim_seconds)
        return "break"

    def _on_ctrl_up(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(self.selected_filtered_pos - 1)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_ctrl_down(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(self.selected_filtered_pos + 1)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_ctrl_page_up(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(self.selected_filtered_pos - 10)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_ctrl_page_down(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(self.selected_filtered_pos + 10)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_ctrl_home(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(0)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_ctrl_end(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(len(self.filtered_indexes) - 1)
        segment = self._current_segment()
        if segment:
            self._seek_to_absolute(segment.start_sec)
        return "break"

    def _on_quit(self, _event: tk.Event[tk.Misc]) -> str:
        self.close()
        return "break"

    def _on_page_up(self, _event: tk.Event[tk.Misc]) -> str:
        if not self._transcript_hidden:
            self._select_pos(self.selected_filtered_pos - 10)
        return "break"

    def _on_page_down(self, _event: tk.Event[tk.Misc]) -> str:
        if not self._transcript_hidden:
            self._select_pos(self.selected_filtered_pos + 10)
        return "break"

    def _on_home(self, _event: tk.Event[tk.Misc]) -> str:
        if not self._transcript_hidden:
            self._select_pos(0)
        return "break"

    def _on_end(self, _event: tk.Event[tk.Misc]) -> str:
        if not self._transcript_hidden and self.filtered_indexes:
            self._select_pos(len(self.filtered_indexes) - 1)
        return "break"

    def _on_clear_filter(self, _event: tk.Event[tk.Misc]) -> str:
        focused = self.root.focus_get()
        if isinstance(focused, tk.Entry):
            try:
                focused.delete(0, tk.END)
                self.status_var.set("Input cleared")
                return "break"
            except Exception:
                pass
        if isinstance(focused, tk.Text):
            try:
                focused.delete("1.0", tk.END)
                self.status_var.set("Input cleared")
                return "break"
            except Exception:
                pass
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
        try:
            base_size = max(8, int(self._ai_settings.get("font_size") or 12))
        except Exception:
            base_size = 12
        current = base_size + int(self._font_size_delta)
        new_size = max(8, min(64, current + delta))
        if new_size == current:
            return
        self._font_size_delta = new_size - base_size
        self._apply_theme()
        self._apply_font_scale_tree(self.root)
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)
        self.caption_view.tag_configure(
            "row",
            lmargin1=0,
            lmargin2=self._wrap_indent_px
        )
        self._refresh_caption_view()
        self.status_var.set(f"Text size: {new_size}")

    def _seek_relative(self, delta_sec: float) -> None:
        now_ms = self.player.get_time()
        if now_ms < 0:
            now_ms = 0
        target_ms = int(max(0.0, (now_ms / 1000.0) + delta_sec) * 1000.0)
        self._seek_to_absolute(target_ms / 1000.0)
        self.status_var.set(f"Seek -> {_fmt_hms(target_ms / 1000.0)}")

    def _seek_to_absolute(self, sec: float) -> None:
        target_ms = int(max(0.0, sec) * 1000.0)
        state = self.player.get_state()
        if state in {vlc.State.Ended, vlc.State.Stopped, vlc.State.Error}:
            self.player.stop()
            self.player.play()
            self.root.after(120, lambda: self.player.set_time(target_ms))
            self.root.after(200, lambda: self.player.set_pause(0))
            return
        self.player.set_time(target_ms)
        if self._skim_mode:
            self._sync_skim_cursor_with_pos(target_ms / 1000.0)

    def _tick_ui(self) -> None:
        state = self.player.get_state()
        self._refresh_pause_overlay(state)
        if self._active_popup_name is not None:
            self.root.after(250, self._tick_ui)
            return
        pos_ms = max(0, self.player.get_time())
        pos_sec = pos_ms / 1000.0
        self._tick_skim(state, pos_sec)
        length_ms = self.player.get_length()
        length_sec = (
            max(0.0, length_ms / 1000.0)
            if length_ms and length_ms > 0
            else 0.0
        )
        self._update_clock_view(pos_sec, length_sec)
        self.caption_now_var.set(self._caption_text_at(pos_sec))
        if state == vlc.State.Playing:
            next_status = f"Playing @ {_fmt_hms(pos_sec)}"
            if self.status_var.get() != next_status:
                self.status_var.set(next_status)
        self.root.after(250, self._tick_ui)

    def _filtered_clip_ranges(self) -> list[tuple[float, float, int]]:
        if not self.filtered_indexes:
            return []
        pre = max(0, int(self._skim_pre_ms)) / 1000.0
        post = max(0, int(self._skim_post_ms)) / 1000.0
        ranges: list[tuple[float, float, int]] = []
        for seg_idx in self.filtered_indexes:
            segment = self.segments[seg_idx]
            start = max(0.0, segment.start_sec - pre)
            end = max(start, segment.end_sec + post)
            ranges.append((start, end, seg_idx))
        return ranges

    def _sync_skim_cursor_with_pos(self, pos_sec: float) -> None:
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_cursor = 0
            return
        chosen = 0
        for index, (start, end, _seg_idx) in enumerate(ranges):
            if pos_sec < start or start <= pos_sec <= end:
                chosen = index
                break
            chosen = min(index + 1, len(ranges) - 1)
        self._skim_cursor = max(0, min(chosen, len(ranges) - 1))

    def _start_skim_at_cursor(self, *, force_seek: bool = False) -> None:
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_mode = False
            self.status_var.set(
                "Skim mode disabled: no filtered transcript matches"
            )
            return
        self._skim_cursor = max(0, min(self._skim_cursor, len(ranges) - 1))
        start, _end, seg_idx = ranges[self._skim_cursor]
        if force_seek:
            self._seek_to_absolute(start)
            self.player.set_pause(0)
            self.selected_filtered_pos = self.filtered_indexes.index(seg_idx)
            self._select_pos(self.selected_filtered_pos)
            self._skim_last_seek_at = 0.0

    def _toggle_skim_mode(self) -> None:
        self._skim_mode = not self._skim_mode
        if self._skim_mode:
            self._sync_skim_cursor_with_pos(
                max(0, self.player.get_time()) / 1000.0
            )
            self._start_skim_at_cursor(force_seek=True)
            if self._skim_mode:
                _pre = self._skim_pre_ms
                _post = self._skim_post_ms
                self.status_var.set(
                    f"Skim mode ON (pre={_pre}ms, post={_post}ms)"
                )
            return
        self.status_var.set("Skim mode OFF")

    def _tick_skim(self, state: vlc.State, pos_sec: float) -> None:
        if not self._skim_mode:
            return
        if state not in {
            vlc.State.Playing,
            vlc.State.Paused,
            vlc.State.NothingSpecial
        }:
            return
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_mode = False
            self.status_var.set(
                "Skim mode disabled: no filtered transcript matches"
            )
            return
        self._skim_cursor = max(0, min(self._skim_cursor, len(ranges) - 1))
        start, end, _seg_idx = ranges[self._skim_cursor]
        now = 0.0
        try:
            import time
            now = time.monotonic()
        except Exception:
            pass
        if pos_sec < start - 0.2 and (now - self._skim_last_seek_at) > 0.2:
            self._seek_to_absolute(start)
            self.player.set_pause(0)
            self._skim_last_seek_at = now
            return
        if pos_sec <= end:
            return
        self._skim_cursor += 1
        if self._skim_cursor >= len(ranges):
            self._skim_mode = False
            self.player.set_pause(1)
            self.status_var.set("Skim mode complete")
            return
        next_start, _next_end, next_seg_idx = ranges[self._skim_cursor]
        if (now - self._skim_last_seek_at) > 0.2:
            self._seek_to_absolute(next_start)
            self.player.set_pause(0)
            self._skim_last_seek_at = now
            if next_seg_idx in self.filtered_indexes:
                self.selected_filtered_pos = \
                        self.filtered_indexes.index(next_seg_idx)
                self._select_pos(self.selected_filtered_pos)

    def _caption_text_at(self, pos_sec: float) -> str:
        if not self.segments:
            return ""
        idx = bisect_right(self._segment_starts, pos_sec) - 1
        if idx < 0 or idx >= len(self.segments):
            return ""
        segment = self.segments[idx]
        if segment.start_sec <= pos_sec <= segment.end_sec:
            return segment.text
        return ""

    def _render_time_progress_parts(
        self,
        pos_sec: float,
        length_sec: float
    ) -> tuple[str, str]:
        prefix = f"[{_fmt_hms(pos_sec)}] "
        if length_sec <= 0:
            return prefix, "░" * self._progress_bar_width
        ratio = max(0.0, min(1.0, pos_sec / length_sec))
        filled = int(round(ratio * self._progress_bar_width))
        bar = ("█" * filled) + ("░" * (self._progress_bar_width - filled))
        return prefix, bar

    def _update_clock_view(self, pos_sec: float, length_sec: float) -> None:
        self._clock_last_length_sec = max(0.0, float(length_sec))
        prefix, bar = self._render_time_progress_parts(pos_sec, length_sec)
        self._clock_prefix_len = len(prefix)
        payload = prefix + bar
        self.clock_view.configure(state="normal")
        self.clock_view.delete("1.0", tk.END)
        self.clock_view.insert("1.0", payload)
        self.clock_view.tag_remove("bar", "1.0", tk.END)
        self.clock_view.tag_add(
            "bar", f"1.{self._clock_prefix_len}",
            f"1.{self._clock_prefix_len + len(bar)}"
        )
        self.clock_view.tag_configure(
            "bar", foreground=self._theme_color("FG_ACCENT")
        )
        self.clock_view.configure(state="disabled")
        self._refresh_transport_controls()

    def _on_click_clock_progress(self, event: tk.Event[tk.Misc]) -> str:
        if self._clock_last_length_sec <= 0:
            return "break"
        index = self.clock_view.index(f"@{event.x},{event.y}")
        try:
            col = int(index.split(".", 1)[1])
        except Exception:
            return "break"
        start = int(self._clock_prefix_len)
        end = start + int(self._progress_bar_width)
        if col < start:
            target = 0.0
        elif col >= end:
            target = self._clock_last_length_sec
        else:
            cell = max(0, min(self._progress_bar_width - 1, col - start))
            ratio = (cell + 0.5) / float(max(1, self._progress_bar_width))
            target = ratio * self._clock_last_length_sec
        self._seek_to_absolute(target)
        self.status_var.set(f"Jumped to {_fmt_hms(target)}")
        return "break"

    def _on_left_resize(self, event: tk.Event[tk.Misc]) -> None:
        width = int(getattr(event, "width", 0))
        if width <= 0:
            return
        self.caption_now_box.configure(wraplength=max(120, width - 24))
        if hasattr(self, "details_title"):
            wrap = max(120, width - 24)
            self.details_title.configure(wraplength=wrap)
            self.details_meta.configure(wraplength=wrap)
            self.details_genre.configure(wraplength=wrap)
        self._update_progress_bar_width(width)
        self._refresh_clock_now()
        self._position_video_overlay()

    def _update_progress_bar_width(
        self,
        panel_width: int | None = None
    ) -> None:
        width = (
            panel_width
            if panel_width is not None
            else int(self.left_panel.winfo_width())
        )
        if width <= 0:
            return
        available = max(120, width - 24)
        prefix_px = self._text_font.measure("[00:00:00] ")
        block_px = max(1, self._text_font.measure("█"))
        bar_chars = max(12, min(140, int((available - prefix_px) / block_px)))
        self._progress_bar_width = bar_chars + 24

    def _refresh_clock_now(self) -> None:
        if not hasattr(self, "player"):
            self._update_clock_view(0.0, 0.0)
            return
        pos_ms = max(0, self.player.get_time())
        pos_sec = pos_ms / 1000.0
        length_ms = self.player.get_length()
        length_sec = (
            max(0.0, length_ms / 1000.0)
            if length_ms and length_ms > 0
            else 0.0
        )
        self._update_clock_view(pos_sec, length_sec)

    def _set_initial_split_ratio(self) -> None:
        if self._split_initialized:
            return
        total_w = self.shell.winfo_width()
        total_h = self.shell.winfo_height()
        if total_w <= 0 or total_h <= 0:
            return
        self._apply_shell_layout(total_w, total_h)
        self._split_initialized = True

    def _on_shell_configure(self, _event: tk.Event[tk.Misc]) -> None:
        total_w = self.shell.winfo_width()
        total_h = self.shell.winfo_height()
        if total_w > 0 and total_h > 0:
            self._apply_shell_layout(total_w, total_h)
        if not self._split_initialized:
            self._set_initial_split_ratio()

    def _apply_shell_layout(self, total_w: int, total_h: int) -> None:
        should_stack = total_w < 1320 or total_w < int(total_h * 1.45)
        orient = tk.VERTICAL if should_stack else tk.HORIZONTAL
        layout_mode = "vertical" if should_stack else "horizontal"
        layout_changed = self._shell_layout_mode != layout_mode
        if str(self.shell.cget("orient")) != str(orient):
            self.shell.configure(orient=orient)
        if layout_changed:
            if should_stack:
                self.shell.paneconfigure(self.left_panel, minsize=0)
                self.shell.paneconfigure(self.right_panel, minsize=0)
            else:
                self.shell.paneconfigure(self.left_panel, minsize=0)
                self.shell.paneconfigure(self.right_panel, minsize=0)
            self._shell_layout_mode = layout_mode
        self._shell_stacked = should_stack
        if layout_changed or not self._split_initialized:
            if should_stack:
                self.shell.sash_place(0, 0, int(total_h * 2 / 3))
            else:
                self.shell.sash_place(0, int(total_w * 3 / 5), 0)

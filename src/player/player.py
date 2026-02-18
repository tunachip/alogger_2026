from pathlib import Path
from typing import Any, TYPE_CHECKING
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk
import sys
import json

from theme import Colorscheme, Geometry, Theme, Font

if TYPE_CHECKING: from vlc import State

@dataclass(slots=True)
class SegmentRow:
    idx:     int
    start:   float
    end:     float
    text:    str
    text_lc: str

def _fmt_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

class Player:
    def __init__(
        self,
        transcript: Path | None = None,
        video_path: Path | None = None,
        audio_path: Path | None = None,
        start_time: float = 0.0,
        end_time:   float = 0.0,
        workers:    int = 0
    ) -> None:
        self.transcript = transcript
        self.video_path = video_path
        self.audio_path = audio_path
        self.start_time = start_time
        self.end_time   = end_time
        self.workers = max(0, int(workers))

        # caption list values
        self.caption_seg_starts:      list[float] = []
        self.caption_segments:        list[SegmentRow] = []
        self.caption_row_ranges:      list[tuple[str, str]] = []
        self.caption_row_text_ranges: list[tuple[str, str]] = []

        # cursor positions
        self.caption_list_cursor_pos = 0
        self.cursor_jump = 10
        
        # media load counters
        self._load_fail_count = 0
        self._startup_poll_count = 0

        # Startup Methods
        self._load_segments(transcript)


    def _load_segments(self, transcript) -> None:
        if not transcript.exists():
            raise FileNotFoundError(f'Transcript not found: {transcript}')
        payload = json.loads(transcript.read_text(encoding='utf-8'))
        raw_segments = payload.get('segments', [])
        if not isinstance(raw_segments, list):
            raise ValueError("Transcript has no valid 'segments' list")
        
        for i, seg in enumerate(raw_segments):
            text = str(seg.get('text, ')).strip().replace('\n', ' ')
            if not text: continue
            start = float(seg.get('start', 0.0))
            end   = float(seg.get('end', start))
            segment = SegmentRow(idx=i, start=start, end=end, text=text, text_lc=text.lower())
            self.segments.append(segment)
            self.seg_starts.append(start)

    def _setup_styles(self) -> None:
        theme = Theme.USER if Theme.USER is not 'unset' else Theme.BASE
        ttk.Style(self.root).theme_use(theme)
        ttk.Style(self.root).configure(
            "Filter.TEntry",
            fieldbackground = Colorscheme.TEXT_FIELD_BG,
            foreground = Colorscheme.TEXT_FIELD_FG)
        font_family = Font.USER if Font.USER is not 'unset' \
            else f"{Font.BASE} {Font.FALLBACK}"
        self.font = {'family': font_family, 'size': Geometry.FONT_SIZE}

    def _setup_interface(self) -> None:
        self.root = tk.Tk()
        self.root.title('ALOG Video Player')
        self.root.geometry(f'{Geometry.MAIN_X}x{Geometry.MAIN_Y}')
        self.root.configure(bg=Colorscheme.MAIN_BG)

    def _build_layout(self) -> None:
        # === Helper Functions ===

        # shell
        self.shell = tk.PanedWindow(
            self.root,  
            orient = tk.HORIZONTAL,
            sashrelief = tk.FLAT,
            sashwidth = 4)
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # status bar
        self.status = tk.StringVar(value="Ready.")
        self.statusbar = tk.Label(
            self.shell,
            textvariable = self.status,
            anchor = "w",
            bg = Colorscheme.STATUS_BASE_BG,
            fg = Colorscheme.STATUS_BASE_FG,
            font = (self.font['family'], self.font['size']),
            padx = 10,
            pady = 10,
            bd = 1)
        self.statusbar.grid(row=1, column=0, sticky="ew")
        self.root.rowconfigure(1, weight=0, minsize=24)

        # left panel
        self.left_panel = tk.Frame(self.shell, bg=Colorscheme.MAIN_BG)
        self.shell.add(self.left_panel, minsize=Geometry.LEFT_PANEL_MIN_SIZE)
        self.left_panel.columnconfigure(0, weight=1)
        self.left_panel.rowconfigure(0, weight=1)

        # side menu
        self.side_menu = tk.Frame(self.shell, bg=Colorscheme.SIDE_MENU_BG)
        self.shell.add(self.side_menu, minsize=Geometry.SIDE_MENU_MIN_SIZE)
        self.side_menu.columnconfigure(1, weight=1)
        self.side_menu.rowconfigure(0, weight=1)
        
        self.left_panel.bind('<Configure>', self._on_shell_configure)

        # ----- Left Panel Widgets -----

        # player
        self.player_panel = tk.Frame(
            self.left_panel,
            bg = Colorscheme.PLAYER_BG,
            highlightthickness = 0)
        self.player_panel.grid(row=0, column=0, sticky="nsew")
        
        # timeclock
        self.clock_var = tk.StringVar(value="00:00:00")
        player_clock = tk.Label(
            self.left_panel,
            textvariable = self.clock_var,
            anchor = "w",
            justify = "left",
            bg = Colorscheme.PLAYER_BG,
            fg = Colorscheme.PLAYER_ACCENT,
            font = (self.font['family'], self.font['size']),
            padx = 10,
            pady = 10)
        player_clock.grid(row=2, column=0, sticky="ew")

        # player captions
        self.player_caption = tk.StringVar(value="")
        self.player_caption_box = tk.Label(
            self.left_panel,
            textvariable = self.player_caption,
            anchor = "w",
            justify = "left",
            bg = Colorscheme.PLAYER_BG,
            fg = Colorscheme.PLAYER_FG,
            font = (self.font['family'], self.font['size']),
            padx = 10,
            pady = 10,
            wraplength = 400)
        self.player_caption_box.grid(row=1, column=0, sticky="ew")
        self.left_panel.bind('<Configure>', self._on_shell_configure)

        # ----- Side Menu Widgets -----

        # Caption Filter
        self.caption_filter_query = tk.StringVar(value="")
        self.caption_filter_entry = ttk.Entry(
            self.side_menu,
            textvariable = self.caption_filter_query,
            style = "Filter.TEntry")
        self.caption_filter_entry.grid(
            row = 0,
            column = 0,
            sticky = "ew",
            padx = 10,
            pady = 10)

        # Caption List
        self.caption_list = tk.Text(
            self.side_menu,
            bg = Colorscheme.CAPTION_BASE_BG,
            fg = Colorscheme.CAPTION_BASE_FG,
            borderwidth = 0,
            highlightthickness = 0,
            font = self.font['family'],
            wrap = "word",
            padx = 10,
            pady = 10,
            insertbackground = Colorscheme.CAPTION_BASE_ACCENT)
        self.caption_list.grid(
            row = 1,
            column = 1,
            sticky = "nsew",
            padx = 10,
            pady = 10)
        self.caption_list.configure(state="disabled")

        self.caption_list.tag_configure("row", lmargin1=0, lmargin2=self._wrap_indent_px)
        self.caption_list.tag_configure("ts",       foreground=Colorscheme.CAPTION_TIME_FG)
        self.caption_list.tag_configure("txt",      foreground=Colorscheme.CAPTION_BASE_FG)
        self.caption_list.tag_configure("match",    foreground=Colorscheme.CAPTION_MATCH_FG)
        self.caption_list.tag_configure("selected", foreground=Colorscheme.CAPTION_SELECT_FG,
                                                    background=Colorscheme.CAPTION_SELECT_BG)
        self.caption_list.tag_configure("selected_txt", font=self._text_font_bold)

    def _build_vlc(self) -> None:
        self.vlc_instance = vlc.Instance(
            "--quiet",
            "--no-video-title-show",
            "--avcodec-hw=none")
        if not self.vlc_instance:
            raise Exception('self.vlc_instance failed to load.')
        self.vlc_player = self.vlc_instance.media_player_new()
        if self.video_path:
            if not self.video_path.exists():
                raise FileNotFoundError(f'video path not found: {self.video_path}')
            self._set_player_media(self.video_path, self.audio_path, self.start_time)

    def _set_player_media(self, video_path: Path, start_time: float = 0.0) -> None:
        if video_path:
            if not video_path.exists():
                raise FileNotFoundError(f'video path not found: {video_path}')
            self.video_path = video_path
        if self.vlc_player:
            self.vlc_player.stop()

        media = self.vlc_instance.media_new_path(str(video_path))
        self.vlc_player.set_media(media)
        self.root.update_idletasks()
        handle = self.player_panel.winfo_id()
        self._bind_video_output(handle)
        self.vlc_player.play()
        self._startup_poll_count = 0
        self.root.after(350, lambda: self._post_media_load(start_time))

    def _bind_video_output(self, handle: int) -> None:
        match sys.platform[5:]:
            case 'linux': self.vlc_player.set_window(handle); return
            case 'win32': self.vlc_player.set_hand(handle)
            case 'darwi': self.vlc_player.set_nsobject(handle)

    def _post_media_load(self, start_time: float) -> None:
        state = self.vlc_player.get_state()
        if state in (vlc.State.Opening,
                     vlc.State.Buffering,
                     vlc.State.NothingSpecial):
            if self._startup_poll_count < 8:
                self._startup_poll_count += 1
                self.root.after(250, self._post_media_load(start_time))
                return
        if state == vlc.State.Stopped:
            if self._startup_poll_count < 3:
                self._startup_poll_count += 1
                self.vlc_player.play()
                self.root.after(250, self._post_media_load(start_time))
                return
        if state in (vlc.State.Ended,
                     vlc.State.Error,
                     vlc.State.Stopped):
            self.status.set(f"Failed to load media: {self.video_path} [state: {state}]")
            return
        if start_time > 0:
            self.vlc_player.set_time(int(start_time * 1000.0))
        self.vlc_player.set_pause(0)
        self.status.set("Ready.")
        self._load_fail_count = 0
        self._startup_poll_count = 0

    def _refresh_caption_list(self) -> None:
        self.caption_list.configure(state="normal")
        self.caption_list.delete("1.0", tk.END)
        self._caption_row_ranges = []
        self._caption_row_text_ranges = []
        
        query = self.caption_filter_query.get().strip().lower()

        for idx in self.filtered_indexes:
            seg = self.caption_segments[idx]
            line_start = self.caption_list.index("end-1c")
            prefix = f"[{_fmt_hms(seg.start)}] "
            ts_range =  [line_start, f'{line_start}+{len(prefix)}c']
            txt_range = [ts_range[1], f'{line_start}*{len(prefix)+len(seg.text)}c']
            self.caption_list.insert(tk.END, prefix + seg.text + "\n",("row",))
            self.caption_list.tag_add("ts", ts_range[0], ts_range[1])
            self.caption_list.tag_add("txt", txt_range[0], txt_range[1])
            self.caption_row_text_ranges.append((txt_range[0], txt_range[1],))
            line_end = self.caption_list.index("end-1c")
            self.caption_row_ranges.append((line_start, line_end))

            if query:
                pos = 0
                while True:
                    hit = seg.text_lc.find(query, pos)
                    if hit == -1: break
                    pos = hit + len(query)
                    s = f'{line_start}+{len(prefix)+hit}c'
                    e = f'{line_start}+{len(prefix)+pos}c'
                    self.caption_list.tag_add("match", s, e)
        
        if self.filtered_indexes:
            self._select_pos(self.caption_list_cursor_pos)
        else:
            self.status.set('No Matches Found.')
        self.caption_list.configure(state='disabled')

    def _select_pos(self, pos: int) -> None:
        if not self.filtered_indexes: return
        pos = max(0, min(pos, len(self.filtered_indexes) - 1))
        self.caption_list_cursor_pos = pos

        self.caption_list.configure(state='normal')
        self.caption_list.tag_remove('selected', '1.0', tk.END)
        self.caption_list.tag_remove('selected_txt', '1.0', tk.END)
        line_range = self.caption_row_ranges[pos]
        text_range = self.caption_row_text_ranges[pos]
        self.caption_list.tag_add('selected', line_range[0], line_range[1])
        self.caption_list.tag_add('selected_txt', text_range[0], text_range[1])
        self.caption_list.see(line_range[0])
        self.caption_list.configure(state="disabled")
        self.status.set(f"Matches: {len(self.filtered_indexes)}")

    def _current_segment(self) -> SegmentRow | None:
        if not self.filtered_indexes:
            return None
        return self.caption_segments[self.filtered_indexes[self.caption_list_cursor_pos]]

    # ----- Player Seek -----

    def _seek_relative(self, delta: float) -> None:
        now_ms = max(0, self.vlc_player.get_time())
        target_ms = int(max(0.0, (now_ms / 1000.0) + delta) * 1000.0)
        self._seek_absolute(target_ms / 1000.0)
        self.status.set(f'Seek -> {_fmt_hms(target_ms / 1000.0)}')

    def _seek_absolute(self, time) -> None:
        target_ms = int(max(0.0, time) * 1000.0)
        if self.vlc_player.get_state() in {
            vlc.State.Ended,
            vlc.State.Stopped,
            vlc.State.Error
        }:
            self.vlc_player.stop()
            self.vlc_player.play()
            self.root.after(120, lambda: self.vlc_player.set_time(target_ms))
            self.root.after(200, lambda: self.vlc_player.set_pause(0))
            return
        self.vlc_player.set_time(target_ms)

    # ---- Keybinds -----

    def _bind_keys(self) -> None:
        # General Controls
        self.root.bind('<Control-plus', self._on_ctrl_plus)
        self.root.bind('<Control-minus', self._on_ctrl_minus)
        self.root.bind('<Control-equal', self._on_ctrl_plus)

        # Caption Filter Query Controls
        self.caption_filter_query.trace_add("write", self._on_filter_query_change)
        self.caption_list.bind('<Double-Button-1', self._on_double_click)
        self.root.bind('<Up>',     self._on_up)
        self.root.bind('<Down>',   self._on_down)
        #self.root.bind('<Left>',   self._on_left)
        #self.root.bind('<Right>',  self._on_right)
        self.root.bind('<Prior>',  self._on_pg_up)
        self.root.bind('<Next>',   self._on_pg_dn)
        self.root.bind('<Home>',   self._on_home)
        self.root.bind('<End>',    self._on_end)
        self.root.bind('<Return>', self._on_return)

        # Video Player Controls
        self.root.bind('<Alt-Up>',     self._on_alt_up)
        self.root.bind('<Alt-Down>',   self._on_alt_down)
        self.root.bind('<Alt-Left>',   self._on_alt_left)
        self.root.bind('<Alt-Right>',  self._on_alt_right)
        self.root.bind('<Alt-Prior>',  self._on_alt_pg_up)
        self.root.bind('<Alt-Next>',   self._on_alt_pg_down)
        self.root.bind('<Alt-Home>',   self._on_alt_home)
        self.root.bind('<Alt-End>',    self._on_alt_end)
        self.root.bind('<Alt-Return>', self._on_alt_return)

        # Menu Controls
        self.root.bind('Control-KeyPress-c', self._on_ctrl_c)
        self.root.bind('Control-KeyPress-l', self._on_ctrl_l)
        self.root.bind('Control-KeyPress-f', self._on_ctrl_f)
        self.root.bind('Control-KeyPress-o', self._on_ctrl_o)
        self.root.bind('Control-KeyPress-n', self._on_ctrl_n)
        self.root.bind('Control-KeyPress-s', self._on_ctrl_s)
        self.root.bind('Control-KeyPress-q', self._on_ctrl_q)

    # ----- Events -----

    def _on_filter_query_change(self) -> None:
        query = self.caption_filter_query.get().strip().lower()
        if not query:
            self.filtered_indexes = list(range(len(self.caption_segments)))
        else:
            self.filtered_indexes = [idx for idx, seg in enumerate(self.caption_segments) if query in seg.text_lc]
        self.caption_list_cursor_pos = 0
        self._refresh_caption_list()

    def _on_up(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden:
            pos = self.caption_list_cursor_pos - 1
            self._select_pos(pos)
        return 'break'
    
    def _on_down(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden:
            pos = self.caption_list_cursor_pos + 1
            self._select_pos(pos)
        return 'break'
    
    def _on_pg_up(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden:
            pos = self.caption_list_cursor_pos - self.cursor_jump
            self._select_pos(pos)
        return 'break'
    
    def _on_pg_dn(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden:
            pos = self.caption_list_cursor_pos + self.cursor_jump
            self._select_pos(pos)
        return 'break'

    def _on_home(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden:
            self._select_pos(0)
        return 'break'

    def _on_end(self, _:tk.Event[tk.Misc]) -> str:
        if not self.sidemenu_hidden and self.filtered_indexes:
            pos = len(self.filtered_indexes) - 1
            self._select_pos(pos)
        return 'break'

    def _on_return(self, _:tk.Event[tk.Misc]) -> str:
        seg = self._current_segment()
        if seg is not None:
            time = seg.start
            self._seek_to_absolute(time)
            self.status.set(f"Jumped to {_fmt_hms(time)}")
        return 'break'

    def _on_double_click(self, event:tk.Event[tk.misc]) -> str:
        index = self.caption_list.index(f'@{event.x},{event.y}')
        line = int(index.split(".")[0]) - 1
        if 0 <= line < len(self.filtered_indexes):
            self._select_pos(line)
        return self._on_return(event)


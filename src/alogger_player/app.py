from __future__ import annotations
import vlc
import ast
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable
try:
    from PIL import Image, ImageTk
except Exception:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

from alog.config import IngesterConfig
from alog.service import IngesterService
from alog.pipeline import (
    _media_has_audio_stream,
    _media_has_video_stream,
    resolve_playback_media_path,
)

# === Config ===
FONT = {
    'STYLE': 'DejaVu Sans Mono',
    'SIZE': 12,
}

THEME = {
}

DEFAULT_KEYBINDS: dict[str, str] = {
    "open_command": "Ctrl+P",
    "open_ingest": "Ctrl+N",
    "open_workers": "Ctrl+I",
    "open_video": "Ctrl+O",
    "open_finder": "Ctrl+F",
    "open_ai": "Ctrl+A",
    "open_goto": "Ctrl+G",
    "toggle_skim": "Ctrl+S",
    "open_settings": "Ctrl+M",
    "quit": "Ctrl+Q",
    "play_pause": "Ctrl+Space",
    "seek_back": "Ctrl+Left",
    "seek_forward": "Ctrl+Right",
    "prev_match": "Ctrl+Up",
    "next_match": "Ctrl+Down",
    "vim_left": "Ctrl+H",
    "vim_down": "Ctrl+J",
    "vim_up": "Ctrl+K",
    "vim_right": "Ctrl+L",
    "clear_input": "Ctrl+C",
    "toggle_transcript": "Ctrl+T",
    "toggle_details": "Ctrl+D",
}

def _fmt_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

@dataclass(slots=True)
class SegmentRow:
    index:     int
    start_sec: float
    end_sec:   float
    text:      str
    text_lc:   str


class OverlayPanel(tk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(
            root,
            bg="#111111",
            highlightthickness=1,
            highlightbackground="#2b2b2b",
            bd=0,
        )
        self._wm_delete_cb: Callable[[], None] | None = None

    def title(self, _title: str) -> None:
        # Compatibility with Toplevel API.
        return

    def geometry(self, size: str) -> None:
        width = 900
        height = 620
        try:
            token = size.lower().split("+", 1)[0]
            w_s, h_s = token.split("x", 1)
            width = max(320, int(w_s))
            height = max(180, int(h_s))
        except Exception:
            pass
        self.place(relx=0.5, rely=0.5, anchor="center", width=width, height=height)
        self.lift()

    def transient(self, _root: tk.Tk) -> None:
        # Compatibility with Toplevel API.
        return

    def protocol(self, name: str, callback: Callable[[], None]) -> None:
        if name == "WM_DELETE_WINDOW":
            self._wm_delete_cb = callback

    def request_close(self) -> None:
        if self._wm_delete_cb:
            self._wm_delete_cb()
            return
        self.destroy()

class TranscriptPlayer:
    def __init__(
        self,
        transcript_json: Path | None = None,
        video_path:      Path | None = None,
        audio_path:      Path | None = None,
        skim_seconds:    float = 5.0,
        start_sec:       float = 0.0,
        workers:         int = 0,
    ) -> None:
        self.transcript_json = transcript_json
        self.video_path = video_path
        self.audio_path = audio_path
        self.skim_seconds = skim_seconds
        self.start_sec = max(0.0, float(start_sec))
        self.workers = max(0, int(workers))

        self.segments = self._load_segments(transcript_json) if transcript_json else []
        self._segment_starts = [seg.start_sec for seg in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        
        self._search_popup:         tk.Toplevel | None = None
        self._command_popup:        tk.Toplevel | None = None
        self._video_picker_popup:   tk.Toplevel | None = None
        self._ingest_popup:         tk.Toplevel | None = None
        self._ai_popup:             tk.Toplevel | None = None
        self._settings_popup:       tk.Toplevel | None = None
        self._goto_popup:           tk.Toplevel | None = None
        self._channel_popup:        tk.Toplevel | None = None
        self._subscriptions_popup:  tk.Toplevel | None = None
        self._jobs_popup:           tk.Toplevel | None = None
        self._jobs_text:            tk.Text | None = None
        self._workers_cmd_list:     tk.Listbox | None = None
        self._jobs_queue_list:      tk.Listbox | None = None
        self._jobs_done_list:       tk.Listbox | None = None
        self._jobs_dl_text:         tk.Text | None = None
        self._jobs_tr_text:         tk.Text | None = None
        self._jobs_agent_text:      tk.Text | None = None
        self._jobs_after_id:        str | None = None
        self._agent_activity:       list[dict[str, str]] = []
        self._search_results:       list[dict[str, Any]] = []
        self._video_picker_results: list[dict[str, Any]] = []
        self._channel_results:      list[dict[str, Any]] = []
        self._channel_default_ref = ""
        self._browse_preview_cache: dict[str, dict[str, Any]] = {}
        self._browse_thumb_dir: Path | None = None
        self._split_initialized =   False
        self._transcript_hidden =   False
        self._details_hidden =      False
        self._split_x_before_hide:  int | None = None
        self._active_popup_name:    str | None = None
        self.current_video_id:      str | None = None
        self._load_fail_count =     0
        self._startup_poll_count =  0
        self._proxy_attempted =     False
        self._skim_mode =           False
        self._skim_pre_ms =         300
        self._skim_post_ms =        500
        self._skim_cursor =         0
        self._skim_last_seek_at =   0.0
        self._worker_target_count = self.workers
        self._downloader_target_count = self.workers if self.workers > 0 else 1
        self._transcriber_target_count = self.workers if self.workers > 0 else 1
        self._workers_paused =      False
        self._popup_paused_player = False
        self._popup_video_hidden =  False
        self._workers_eta_remaining_sec: float | None = None
        self._workers_eta_next_recalc_at: float = 0.0
        self._workers_eta_last_tick_at: float = time.monotonic()
        self._workers_eta_recalc_interval_sec: float = 60.0
        self._worker_anim_tick = 0
        self._bound_shortcut_sequences: list[str] = []
        self._ollama_proc:          subprocess.Popen[str] | None = None
        self._ollama_started_by_app = False
        self._ollama_bootstrap_thread: threading.Thread | None = None
        self._ollama_bootstrap_lock = threading.Lock()
        self._ollama_bootstrap_done = threading.Event()
        self._ollama_bootstrap_success = False
        self._ollama_bootstrap_error: str | None = None
        self._ollama_bootstrap_state = "idle"

        self.ingester_config = IngesterConfig.from_env()
        self.ingester = IngesterService(self.ingester_config)
        self.ingester.init()
        self._browse_thumb_dir = self.ingester_config.db_path.parent / "browse_previews"
        try:
            self._browse_thumb_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._gui_settings_path = self.ingester_config.db_path.parent / "gui_settings.json"
        self._ai_settings = self._load_gui_settings()
        if self.workers <= 0:
            self.workers = max(0, int(self._ai_settings.get("default_worker_count") or 0))
            self._worker_target_count = self.workers
        self._downloader_target_count = max(0, int(self._ai_settings.get("default_downloaders") or self._worker_target_count or 1))
        self._transcriber_target_count = max(0, int(self._ai_settings.get("default_transcribers") or self._worker_target_count or 1))
        if self._worker_target_count <= 0:
            self._worker_target_count = max(self._downloader_target_count, self._transcriber_target_count)
        self.ingester.set_runtime_options(
            auto_transcribe_default=bool(self._ai_settings.get("auto_transcribe_default", True)),
            subscription_db_max_videos=max(0, int(self._ai_settings.get("subscription_db_max_videos") or 0)),
        )
        if self.workers > 0:
            self.ingester.start_background_workers(
                max(self.workers, self._downloader_target_count + self._transcriber_target_count),
                downloader_count=self._downloader_target_count,
                transcriber_count=self._transcriber_target_count,
            )
        if str(self._ai_settings.get("ai_provider") or "ollama").lower() == "ollama":
            self._start_ollama_bootstrap(background=True)

        self.root = tk.Tk()
        self.root.title("Alogger Player")
        self.root.geometry("1640x880")
        self.root.configure(bg="#111111")
        # Avoid insertion-caret blink pulses that can look like popup flicker on some WMs.
        self.root.option_add("*insertOffTime", 0)
        self.root.option_add("*Entry.insertOffTime", 0)
        self.root.option_add("*Text.insertOffTime", 0)
        self.root.option_add("*TEntry.insertOffTime", 0)

        self._text_font = tkfont.Font(family=FONT['STYLE'], size=FONT['SIZE'])
        self._text_font_bold = tkfont.Font(family=FONT['STYLE'], size=FONT['SIZE'], weight="bold")
        self._timestamp_prefix = "[00:00:00] "
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)
        self._progress_bar_width = 28
        self._clock_prefix_len = len("[00:00:00] ")
        self._clock_last_length_sec = 0.0
        self._font_size_delta = 0

        self._setup_styles()
        self._build_layout()
        self._apply_theme()
        self._apply_font_scale_tree(self.root, reset_base=True)
        self._bind_keys()
        self._build_vlc()

        self._refresh_caption_view()
        if not self.video_path:
            self.status_var.set("No video loaded. Press Ctrl-O to open by title or Ctrl-F to search captions.")
        self._tick_ui()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Filter.TEntry",
            fieldbackground="#151515",
            foreground="#f0f0f0"
        )
        style.configure("Terminal.TNotebook", background="#111111", borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "Terminal.TNotebook.Tab",
            background="#0d0d0d",
            foreground="#8f8f8f",
            borderwidth=0,
            padding=(8, 4),
        )
        style.map(
            "Terminal.TNotebook.Tab",
            background=[("selected", "#000000"), ("active", "#161616")],
            foreground=[("selected", "#ffffff"), ("active", "#d2d2d2")],
        )

    def _apply_theme(self) -> None:
        bg = str(self._ai_settings.get("theme_bg") or "#111111")
        panel_bg = str(self._ai_settings.get("theme_panel_bg") or "#000000")
        fg = str(self._ai_settings.get("theme_fg") or "#ffffff")
        muted = str(self._ai_settings.get("theme_muted_fg") or "#8f8f8f")
        accent = str(self._ai_settings.get("theme_accent_fg") or "#f7d154")
        try:
            family = str(self._ai_settings.get("font_family") or FONT["STYLE"])
            base_size = max(8, int(self._ai_settings.get("font_size") or FONT["SIZE"]))
        except Exception:
            family = FONT["STYLE"]
            base_size = FONT["SIZE"]
        size = max(8, min(64, base_size + int(self._font_size_delta)))
        self._text_font.configure(family=family, size=size)
        self._text_font_bold.configure(family=family, size=size)

        self.root.configure(bg=bg)
        self.left_panel.configure(bg=panel_bg)
        self.right_panel.configure(bg=bg)
        self.video_panel.configure(bg=panel_bg)
        self.clock_view.configure(bg="#0d0d0d", fg=fg, insertbackground=fg, font=(family, max(8, size - 2), "bold"))
        self.caption_now_box.configure(bg="#0d0d0d", fg=fg, font=(family, size - 2))
        self.root_status_box.configure(bg="#0d0d0d", fg=fg, font=(family, size))
        self.caption_view.configure(bg=panel_bg, fg=fg, insertbackground=fg, font=self._text_font)
        self.caption_view.tag_configure("ts", foreground=muted)
        self.caption_view.tag_configure("txt", foreground=fg)
        self.caption_view.tag_configure("match", foreground=accent)
        self.caption_view.tag_configure("selected", background="#282828")
        self.caption_view.tag_configure("selected_txt", font=self._text_font_bold)

        style = ttk.Style(self.root)
        style.configure(
            "Filter.TEntry",
            fieldbackground="#151515",
            foreground=fg,
            font=(family, max(8, size - 1)),
        )
        style.configure("Terminal.TNotebook.Tab", font=(family, max(8, size - 3)))
        self._refresh_clock_now()

    def _apply_font_scale_tree(self, root_widget: tk.Misc | None = None, *, reset_base: bool = False) -> None:
        root = root_widget or self.root
        if not root:
            return

        def _visit(widget: tk.Misc) -> None:
            try:
                keys = set(widget.keys())
            except Exception:
                keys = set()
            if "font" in keys:
                try:
                    if reset_base and hasattr(widget, "_alog_base_font"):
                        delattr(widget, "_alog_base_font")
                    base = getattr(widget, "_alog_base_font", None)
                    if base is None:
                        fobj = tkfont.Font(root=self.root, font=widget.cget("font"))
                        actual = fobj.actual()
                        base = {
                            "family": str(actual.get("family") or FONT["STYLE"]),
                            "size": int(actual.get("size") or FONT["SIZE"]) - int(self._font_size_delta),
                            "weight": str(actual.get("weight") or "normal"),
                            "slant": str(actual.get("slant") or "roman"),
                            "underline": int(actual.get("underline") or 0),
                            "overstrike": int(actual.get("overstrike") or 0),
                        }
                        setattr(widget, "_alog_base_font", base)
                    new_size = max(8, min(64, int(base["size"]) + int(self._font_size_delta)))
                    parts: list[Any] = [base["family"], new_size]
                    if str(base.get("weight")) == "bold":
                        parts.append("bold")
                    if str(base.get("slant")) == "italic":
                        parts.append("italic")
                    if int(base.get("underline") or 0):
                        parts.append("underline")
                    if int(base.get("overstrike") or 0):
                        parts.append("overstrike")
                    widget.configure(font=tuple(parts))
                except Exception:
                    pass
            for child in widget.winfo_children():
                _visit(child)

        _visit(root)

    def _load_gui_settings(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "ai_provider": "ollama",
            "ollama_model": "llama3.2:3b",
            "ollama_base_url": "http://127.0.0.1:11434",
            "api_base_url": "https://api.openai.com",
            "api_key_env": "OPENAI_API_KEY",
            "api_model": "gpt-4o-mini",
            "default_worker_count": 0,
            "default_downloaders": 1,
            "default_transcribers": 1,
            "auto_transcribe_default": True,
            "subscription_db_max_videos": 0,
            "theme_bg": "#111111",
            "theme_panel_bg": "#000000",
            "theme_fg": "#ffffff",
            "theme_muted_fg": "#8f8f8f",
            "theme_accent_fg": "#f7d154",
            "font_family": FONT["STYLE"],
            "font_size": FONT["SIZE"],
            "keybinds": dict(DEFAULT_KEYBINDS),
        }
        path = getattr(self, "_gui_settings_path", None)
        if not path:
            return defaults
        try:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    defaults.update(payload)
                    keybinds = payload.get("keybinds")
                    if isinstance(keybinds, dict):
                        merged = dict(DEFAULT_KEYBINDS)
                        for k, v in keybinds.items():
                            if isinstance(k, str) and isinstance(v, str):
                                merged[k] = v
                        defaults["keybinds"] = merged
        except Exception:
            pass
        return defaults

    def _save_gui_settings(self) -> None:
        try:
            self._gui_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._gui_settings_path.write_text(
                json.dumps(self._ai_settings, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _ollama_base_url(self) -> str:
        return str(self._ai_settings.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")

    def _ollama_model(self) -> str:
        return str(self._ai_settings.get("ollama_model") or "llama3.2:3b").strip() or "llama3.2:3b"

    def _auto_transcribe_default(self) -> bool:
        return bool(self._ai_settings.get("auto_transcribe_default", True))

    def _ollama_server_healthy(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._ollama_base_url()}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                _ = resp.read()
            return True
        except Exception:
            return False

    def _ollama_model_exists(self, model: str) -> bool:
        try:
            req = urllib.request.Request(f"{self._ollama_base_url()}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            models = payload.get("models") or []
            if not isinstance(models, list):
                return False
            aliases = {model, f"{model}:latest"} if ":" not in model else {model}
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name in aliases:
                    return True
            return False
        except Exception:
            return False

    def _start_ollama_server(self) -> None:
        if self._ollama_proc and self._ollama_proc.poll() is None:
            return
        kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "text": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        self._ollama_proc = subprocess.Popen(["ollama", "serve"], **kwargs)
        self._ollama_started_by_app = True

    def _reset_ollama_bootstrap(self) -> None:
        self._ollama_bootstrap_done.clear()
        self._ollama_bootstrap_success = False
        self._ollama_bootstrap_error = None
        self._ollama_bootstrap_state = "idle"

    def _bootstrap_ollama(self) -> None:
        with self._ollama_bootstrap_lock:
            if self._ollama_bootstrap_done.is_set() and self._ollama_bootstrap_success:
                return
            model = self._ollama_model()
            try:
                self._ollama_bootstrap_state = "checking_server"
                if not self._ollama_server_healthy():
                    self._ollama_bootstrap_state = "starting_server"
                    self._start_ollama_server()
                    deadline = time.time() + 25.0
                    while time.time() < deadline:
                        if self._ollama_server_healthy():
                            break
                        time.sleep(0.5)
                    if not self._ollama_server_healthy():
                        raise RuntimeError("ollama server did not become healthy on 127.0.0.1:11434")

                self._ollama_bootstrap_state = "checking_model"
                if not self._ollama_model_exists(model):
                    self._ollama_bootstrap_state = "pulling_model"
                    proc = subprocess.run(
                        ["ollama", "pull", model],
                        capture_output=True,
                        text=True,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"failed to pull Ollama model '{model}': {proc.stderr.strip() or proc.stdout.strip()}"
                        )
                    if not self._ollama_model_exists(model):
                        raise RuntimeError(f"model '{model}' still missing after pull")

                self._ollama_bootstrap_success = True
                self._ollama_bootstrap_error = None
                self._ollama_bootstrap_state = "ready"
            except FileNotFoundError:
                self._ollama_bootstrap_success = False
                self._ollama_bootstrap_error = "ollama binary not found in PATH"
                self._ollama_bootstrap_state = "error"
            except Exception as exc:
                self._ollama_bootstrap_success = False
                self._ollama_bootstrap_error = str(exc)
                self._ollama_bootstrap_state = "error"
            finally:
                self._ollama_bootstrap_done.set()

    def _start_ollama_bootstrap(self, *, background: bool, force_reset: bool = False) -> None:
        if str(self._ai_settings.get("ai_provider") or "ollama").lower() != "ollama":
            return
        if force_reset:
            self._reset_ollama_bootstrap()
        if self._ollama_bootstrap_done.is_set() and self._ollama_bootstrap_success:
            return
        if background:
            if self._ollama_bootstrap_thread and self._ollama_bootstrap_thread.is_alive():
                return
            self._ollama_bootstrap_thread = threading.Thread(
                target=self._bootstrap_ollama,
                daemon=True,
                name="alog-ollama-bootstrap",
            )
            self._ollama_bootstrap_thread.start()
            return
        self._bootstrap_ollama()

    def _ensure_ollama_ready(self, *, block: bool = True) -> None:
        if str(self._ai_settings.get("ai_provider") or "ollama").lower() != "ollama":
            return
        self._start_ollama_bootstrap(background=not block)
        if block:
            self._ollama_bootstrap_done.wait(timeout=600.0)
            if not self._ollama_bootstrap_done.is_set():
                raise RuntimeError("Timed out preparing Ollama")
            if not self._ollama_bootstrap_success:
                raise RuntimeError(self._ollama_bootstrap_error or "Ollama initialization failed")

    def _popup_attr_name(self, name: str) -> str | None:
        return {
            "command": "_command_popup",
            "finder": "_search_popup",
            "open_video": "_video_picker_popup",
            "ingest": "_ingest_popup",
            "workers": "_jobs_popup",
            "ai": "_ai_popup",
            "settings": "_settings_popup",
            "goto": "_goto_popup",
            "channel": "_channel_popup",
            "subscriptions": "_subscriptions_popup",
        }.get(name)

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

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)

        shell = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=4
        )
        shell.grid(row=0, column=0, sticky="nsew")
        self.shell = shell

        left = tk.Frame(shell, bg="#000000")
        right = tk.Frame(shell, bg="#111111")
        self.left_panel = left
        self.right_panel = right
        
        shell.add(left, minsize=1000)
        shell.add(right, minsize=200)
        
        shell.bind("<Configure>", self._on_shell_configure)
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

        clock_view = tk.Text(
            left,
            bg="#0d0d0d",
            fg="#f7d154",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT['STYLE'], FONT['SIZE']-2, "bold"),
            wrap="none",
            height=1,
            padx=10,
            pady=6,
            cursor="hand2",
            insertbackground="#f7d154",
        )
        clock_view.grid(row=2, column=0, sticky="ew")
        clock_view.configure(state="disabled")
        clock_view.bind("<Button-1>", self._on_click_clock_progress)
        self.clock_view = clock_view

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
        self.caption_now_box.grid(row=1, column=0, sticky="ew")
        self.left_panel.bind("<Configure>", self._on_left_resize)

        self.status_var = tk.StringVar(value="Idle")
        root_status = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#d2d2d2",
            font=(FONT['STYLE'], FONT['SIZE']),
            padx=10,
            pady=6,
        )
        root_status.grid(row=1, column=0, sticky="ew")
        self.root_status_box = root_status

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

        self.caption_view.tag_configure("row", lmargin1=0, lmargin2=self._wrap_indent_px)
        self.caption_view.tag_configure("ts", foreground="#8f8f8f")
        self.caption_view.tag_configure("txt", foreground="#ffffff")
        self.caption_view.tag_configure("match", foreground="#f7d154")
        self.caption_view.tag_configure("selected", background="#282828")
        self.caption_view.tag_configure("selected_txt", font=self._text_font_bold)

        self._row_ranges: list[tuple[str, str]] = []
        self._row_text_ranges: list[tuple[str, str]] = []

    def _display_to_tk_sequence(self, value: str) -> str | None:
        token = str(value or "").strip()
        if not token:
            return None
        if token.startswith("<") and token.endswith(">"):
            return token
        parts = [p for p in token.replace("+", "-").split("-") if p]
        if not parts:
            return None
        mods: list[str] = []
        key = parts[-1].lower()
        for raw in parts[:-1]:
            piece = raw.strip().lower()
            if piece in {"ctrl", "control"}:
                mods.append("Control")
            elif piece in {"alt", "meta"}:
                mods.append("Alt")
            elif piece == "shift":
                mods.append("Shift")
        key_map = {
            "space": "space",
            "left": "Left",
            "right": "Right",
            "up": "Up",
            "down": "Down",
            "pgup": "Prior",
            "pageup": "Prior",
            "pgdn": "Next",
            "pagedown": "Next",
            "enter": "Return",
            "return": "Return",
            "esc": "Escape",
            "escape": "Escape",
        }
        tk_key = key_map.get(key, key if len(key) == 1 else key)
        prefix = "-".join(mods)
        return f"<{prefix + '-' if prefix else ''}KeyPress-{tk_key}>"

    def _apply_shortcut_bindings(self) -> None:
        handlers: dict[str, Callable[[tk.Event[tk.Misc]], str]] = {
            "open_command": self._on_open_command_popup,
            "open_ingest": self._on_open_ingest_popup,
            "open_workers": self._on_toggle_jobs_popup,
            "open_video": self._on_open_video_picker_popup,
            "open_finder": self._on_open_search_popup,
            "open_ai": self._on_open_ai_popup,
            "open_goto": self._on_open_goto_popup,
            "toggle_skim": self._on_ctrl_s,
            "open_settings": self._on_open_settings_popup,
            "quit": self._on_quit,
            "play_pause": self._on_toggle_play,
            "seek_back": self._on_ctrl_left,
            "seek_forward": self._on_ctrl_right,
            "prev_match": self._on_ctrl_up,
            "next_match": self._on_ctrl_down,
            "vim_left": self._on_ctrl_left,
            "vim_down": self._on_ctrl_down,
            "vim_up": self._on_ctrl_up,
            "vim_right": self._on_ctrl_right,
            "clear_input": self._on_clear_filter,
            "toggle_transcript": self._on_toggle_transcript_log,
            "toggle_details": self._on_toggle_details,
        }
        for seq in self._bound_shortcut_sequences:
            try:
                self.root.unbind(seq)
            except Exception:
                pass
        self._bound_shortcut_sequences = []
        keybinds = self._ai_settings.get("keybinds")
        if not isinstance(keybinds, dict):
            keybinds = dict(DEFAULT_KEYBINDS)
            self._ai_settings["keybinds"] = keybinds
        for action, handler in handlers.items():
            raw = str(keybinds.get(action, DEFAULT_KEYBINDS.get(action, "")))
            seq = self._display_to_tk_sequence(raw)
            if not seq:
                continue
            try:
                def _wrapped(event: tk.Event[tk.Misc], _h: Callable[[tk.Event[tk.Misc]], str] = handler) -> str:
                    if self._active_popup_name is not None:
                        return "break"
                    return _h(event)
                self.root.bind(seq, _wrapped)
                self._bound_shortcut_sequences.append(seq)
            except Exception:
                continue

    def _guard_key_handler(self, handler: Callable[[tk.Event[tk.Misc]], str]) -> Callable[[tk.Event[tk.Misc]], str]:
        def _wrapped(event: tk.Event[tk.Misc]) -> str:
            if self._active_popup_name is not None:
                return "break"
            return handler(event)
        return _wrapped

    def _bind_keys(self) -> None:
        self.filter_var.trace_add("write", self._on_filter_change)

        self.root.bind("<Up>", self._guard_key_handler(self._on_up))
        self.root.bind("<Down>", self._guard_key_handler(self._on_down))
        self.root.bind("<Return>", self._guard_key_handler(self._on_return))
        self.root.bind("<Left>", self._guard_key_handler(self._on_left))
        self.root.bind("<Right>", self._guard_key_handler(self._on_right))
        self.root.bind("<Prior>", self._guard_key_handler(self._on_page_up))
        self.root.bind("<Next>", self._guard_key_handler(self._on_page_down))
        self.root.bind("<Home>", self._guard_key_handler(self._on_home))
        self.root.bind("<End>", self._guard_key_handler(self._on_end))
        self.root.bind("<Control-minus>", self._guard_key_handler(self._on_font_smaller))
        self.root.bind("<Control-equal>", self._guard_key_handler(self._on_font_larger))
        self.root.bind("<Control-plus>", self._guard_key_handler(self._on_font_larger))
        self.root.bind("<Escape>", self._on_escape, add="+")
        self.root.bind("<KeyPress>", self._on_type_to_filter, add="+")
        self._apply_shortcut_bindings()

        self.caption_view.bind("<Double-Button-1>", self._on_double_click)
        self.caption_view.bind("<Button-1>", self._on_click_seek_transcript)
        self.video_panel.bind("<Button-1>", self._on_click_video_toggle)

        self.root.after(50, lambda: self.filter_entry.focus_set())

    def _build_vlc(self) -> None:
        self.instance = vlc.Instance(
            "--quiet",
            "--no-video-title-show",
            "--avcodec-hw=none",
        )
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
        self.status_var.set(f"Loading media: {video_path.name}")

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
        self._startup_poll_count = 0
        self.root.after(350, lambda: self._post_media_load(start_sec, retry_without_audio=audio_path is not None))

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
        match state:
            case vlc.State.Opening | vlc.State.Buffering | vlc.State.NothingSpecial:
                if self._startup_poll_count < 8:
                    self._startup_poll_count += 1
                    self.root.after(250, lambda: self._post_media_load(start_sec, retry_without_audio=retry_without_audio))
                    return

            case vlc.State.Stopped:
                if self._startup_poll_count < 3:
                    self._startup_poll_count += 1
                    self.player.play()
                    self.root.after(250, lambda: self._post_media_load(start_sec, retry_without_audio=retry_without_audio))
                    return

            case vlc.State.Ended | vlc.State.Error | vlc.State.Stopped:
                if retry_without_audio and self.audio_path is not None:
                    self.status_var.set("Media failed with sidecar audio, retrying video-only...")
                    self._set_player_media(self.video_path, None, start_sec=start_sec)
                    return
                alt = self._pick_alternate_video_path()
                if alt is not None:
                    self._load_fail_count += 1
                    self.status_var.set(f"Media load failed ({self.video_path.name}, {state}); trying {alt.name}...")
                    self._set_player_media(alt, None, start_sec=start_sec)
                    return
                if not self._proxy_attempted:
                    proxy = self._generate_proxy_playback(self.video_path, self.audio_path)
                    if proxy is not None and proxy.exists():
                        self._proxy_attempted = True
                        self.status_var.set(f"Retrying with compatibility proxy: {proxy.name}")
                        self._set_player_media(proxy, None, start_sec=start_sec)
                        return
                self.status_var.set(f"Failed to load media: {self.video_path} (state={state})")
                return

        if start_sec > 0:
            self.player.set_time(int(start_sec * 1000.0))
        self.player.set_pause(0)
        self.status_var.set("Ready")
        self._load_fail_count = 0
        self._startup_poll_count = 0

    def _generate_proxy_playback(self, video_path: Path, audio_path: Path | None) -> Path | None:
        if not self.current_video_id:
            return None
        proxy_path = self.ingester_config.media_dir / f"{self.current_video_id}.proxy.mp4"
        cmd: list[str] = [self.ingester_config.ffmpeg_binary, "-y", "-i", str(video_path)]
        if audio_path and audio_path.exists():
            cmd.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"])
        else:
            cmd.extend(["-map", "0:v:0"])
            if _media_has_audio_stream(video_path) is True:
                cmd.extend(["-map", "0:a:0"])
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "high",
                "-level",
                "4.1",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(proxy_path),
            ]
        )
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        if not proxy_path.exists():
            return None
        return proxy_path

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
            rows.append(SegmentRow(
                index=i,
                start_sec=start_sec,
                end_sec=end_sec,
                text=text,
                text_lc=text.lower(),
            ))
        return rows

    def _on_filter_change(self, *_args: object) -> None:
        query = self.filter_var.get().strip().lower()
        if not query:
            self.filtered_indexes = list(range(len(self.segments)))
        else:
            self.filtered_indexes = [idx for idx, seg in enumerate(self.segments) if query in seg.text_lc]
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

        for seg_idx in self.filtered_indexes:
            seg = self.segments[seg_idx]
            line_start = self.caption_view.index("end-1c")
            prefix = f"[{_fmt_hms(seg.start_sec)}] "
            self.caption_view.insert(tk.END, prefix + seg.text + "\n", ("row",))
            self.caption_view.tag_add("ts", line_start, f"{line_start}+{len(prefix)}c")
            self.caption_view.tag_add( "txt", f"{line_start}+{len(prefix)}c", f"{line_start}+{len(prefix) + len(seg.text)}c",)
            self._row_text_ranges.append(( f"{line_start}+{len(prefix)}c", f"{line_start}+{len(prefix) + len(seg.text)}c",))
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
        seg = self._current_segment()
        if seg is None:
            return "break"
        self._seek_to_absolute(seg.start_sec)
        self.status_var.set(f"Jumped to {_fmt_hms(seg.start_sec)}")
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
        line = row
        self._select_pos(line)

        seg = self.segments[self.filtered_indexes[line]]
        text_start, text_end = self._row_text_ranges[line]
        try:
            click_abs = int(self.caption_view.count("1.0", click_index, "chars")[0])
            start_abs = int(self.caption_view.count("1.0", text_start, "chars")[0])
            end_abs = int(self.caption_view.count("1.0", text_end, "chars")[0])
        except Exception:
            self._seek_to_absolute(seg.start_sec)
            return "break"

        width = max(1, end_abs - start_abs)
        ratio = (click_abs - start_abs) / float(width)
        ratio = max(0.0, min(1.0, ratio))
        target = seg.start_sec + (seg.end_sec - seg.start_sec) * ratio
        self._seek_to_absolute(target)
        self.status_var.set(f"Seek ~{int(ratio * 100)}% of segment @ {_fmt_hms(target)}")
        return "break"

    def _row_from_text_index(self, index: str) -> int | None:
        # Text widgets wrap visual lines, so numeric "line.column" is not a stable row id.
        # Resolve clicked index by comparing against stored row start/end index ranges.
        for i, (start, end) in enumerate(self._row_ranges):
            if self.caption_view.compare(index, ">=", start) and self.caption_view.compare(index, "<=", end):
                return i
        return None

    def _on_click_video_toggle(self, _event: tk.Event[tk.Misc]) -> str:
        return self._on_toggle_play(_event)

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
        seg = self._current_segment()
        if seg:
            self._seek_to_absolute(seg.start_sec)
        return "break"

    def _on_ctrl_down(self, _event: tk.Event[tk.Misc]) -> str:
        if not self.filtered_indexes:
            return "break"
        self._select_pos(self.selected_filtered_pos + 1)
        seg = self._current_segment()
        if seg:
            self._seek_to_absolute(seg.start_sec)
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
        if isinstance(focused, (tk.Entry, ttk.Entry)):
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
            base_size = max(8, int(self._ai_settings.get("font_size") or FONT["SIZE"]))
        except Exception:
            base_size = FONT["SIZE"]
        current = base_size + int(self._font_size_delta)
        new_size = max(8, min(64, current + delta))
        if new_size == current:
            return
        self._font_size_delta = new_size - base_size
        self._apply_theme()
        self._apply_font_scale_tree(self.root)
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)
        self.caption_view.tag_configure("row", lmargin1=0, lmargin2=self._wrap_indent_px)
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
        if state in {
            vlc.State.Ended,
            vlc.State.Stopped,
            vlc.State.Error
        }:
            self.player.stop()
            self.player.play()
            self.root.after(120, lambda: self.player.set_time(target_ms))
            self.root.after(200, lambda: self.player.set_pause(0))
            return
        self.player.set_time(target_ms)
        if self._skim_mode:
            self._sync_skim_cursor_with_pos(target_ms / 1000.0)

    def _tick_ui(self) -> None:
        if self._active_popup_name is not None:
            self.root.after(250, self._tick_ui)
            return
        state = self.player.get_state()
        pos_ms = max(0, self.player.get_time())
        pos_sec = pos_ms / 1000.0
        self._tick_skim(state, pos_sec)
        length_ms = self.player.get_length()
        length_sec = max(0.0, length_ms / 1000.0) if length_ms and length_ms > 0 else 0.0
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
            seg = self.segments[seg_idx]
            start = max(0.0, seg.start_sec - pre)
            end = max(start, seg.end_sec + post)
            ranges.append((start, end, seg_idx))
        return ranges

    def _sync_skim_cursor_with_pos(self, pos_sec: float) -> None:
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_cursor = 0
            return
        chosen = 0
        for i, (start, end, _seg_idx) in enumerate(ranges):
            if pos_sec < start:
                chosen = i
                break
            if start <= pos_sec <= end:
                chosen = i
                break
            chosen = min(i + 1, len(ranges) - 1)
        self._skim_cursor = max(0, min(chosen, len(ranges) - 1))

    def _start_skim_at_cursor(self, *, force_seek: bool = False) -> None:
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_mode = False
            self.status_var.set("Skim mode disabled: no filtered transcript matches")
            return
        self._skim_cursor = max(0, min(self._skim_cursor, len(ranges) - 1))
        start, _end, seg_idx = ranges[self._skim_cursor]
        if force_seek:
            self._seek_to_absolute(start)
            self.player.set_pause(0)
            self.selected_filtered_pos = self.filtered_indexes.index(seg_idx)
            self._select_pos(self.selected_filtered_pos)
            self._skim_last_seek_at = time.monotonic()

    def _toggle_skim_mode(self) -> None:
        self._skim_mode = not self._skim_mode
        if self._skim_mode:
            self._sync_skim_cursor_with_pos(max(0, self.player.get_time()) / 1000.0)
            self._start_skim_at_cursor(force_seek=True)
            if self._skim_mode:
                self.status_var.set(
                    f"Skim mode ON (pre={self._skim_pre_ms}ms, post={self._skim_post_ms}ms)"
                )
            return
        self.status_var.set("Skim mode OFF")

    def _tick_skim(self, state: vlc.State, pos_sec: float) -> None:
        if not self._skim_mode:
            return
        if state not in {vlc.State.Playing, vlc.State.Paused, vlc.State.NothingSpecial}:
            return
        ranges = self._filtered_clip_ranges()
        if not ranges:
            self._skim_mode = False
            self.status_var.set("Skim mode disabled: no filtered transcript matches")
            return
        self._skim_cursor = max(0, min(self._skim_cursor, len(ranges) - 1))
        now = time.monotonic()
        start, end, seg_idx = ranges[self._skim_cursor]
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
                self.selected_filtered_pos = self.filtered_indexes.index(next_seg_idx)
                self._select_pos(self.selected_filtered_pos)

    def _caption_text_at(self, pos_sec: float) -> str:
        if not self.segments:
            return ""
        idx = bisect_right(self._segment_starts, pos_sec) - 1
        if idx < 0 or idx >= len(self.segments):
            return ""
        seg = self.segments[idx]
        if seg.start_sec <= pos_sec <= seg.end_sec:
            return seg.text
        return ""

    def _render_time_progress(self, pos_sec: float, length_sec: float) -> str:
        prefix, bar = self._render_time_progress_parts(pos_sec, length_sec)
        return prefix + bar

    def _render_time_progress_parts(self, pos_sec: float, length_sec: float) -> tuple[str, str]:
        bar_width = self._progress_bar_width
        prefix = f"[{_fmt_hms(pos_sec)}] "
        if length_sec <= 0:
            return prefix, ("░" * bar_width)
        ratio = max(0.0, min(1.0, pos_sec / length_sec))
        filled = int(round(ratio * bar_width))
        bar = ("█" * filled) + ("░" * (bar_width - filled))
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
        self.clock_view.tag_add("bar", f"1.{self._clock_prefix_len}", f"1.{self._clock_prefix_len + len(bar)}")
        self.clock_view.tag_configure("bar", foreground="#f7d154")
        self.clock_view.configure(state="disabled")

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
        # Keep caption wrapping inside the left panel with padding.
        self.caption_now_box.configure(wraplength=max(120, width - 24))
        self._update_progress_bar_width(width)
        self._refresh_clock_now()

    def _update_progress_bar_width(self, panel_width: int | None = None) -> None:
        width = panel_width if panel_width is not None else int(self.left_panel.winfo_width())
        if width <= 0:
            return
        available_px = max(120, width - 24)
        # Prefix is fixed-width and the bar uses mono block chars.
        prefix_px = self._text_font.measure("[00:00:00] ")
        block_px =  max(1, self._text_font.measure("█"))
        bar_chars = max(12, min(140, int((available_px - prefix_px) / block_px)))
        self._progress_bar_width = bar_chars + 24

    def _refresh_clock_now(self) -> None:
        if not hasattr(self, "player"):
            self._update_clock_view(0.0, 0.0)
            return
        pos_ms = max(0, self.player.get_time())
        pos_sec = pos_ms / 1000.0
        length_ms = self.player.get_length()
        length_sec = max(0.0, length_ms / 1000.0) if length_ms and length_ms > 0 else 0.0
        self._update_clock_view(pos_sec, length_sec)

    def _set_initial_split_ratio(self) -> None:
        if self._split_initialized:
            return
        total_w = self.shell.winfo_width()
        if total_w <= 0:
            return
        x = int(total_w * 3 / 5)
        self.shell.sash_place(0, x, 0)
        self._split_initialized = True

    def _on_shell_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if not self._split_initialized:
            self._set_initial_split_ratio()

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
        popup.configure(bg="#111111")
        popup.lift()
        popup.focus_set()
        self._apply_font_scale_tree(popup)

    def _open_command_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Command Menu", "720x520")
        self._command_popup = popup
        self._register_popup("command", popup)
        popup.rowconfigure(1, weight=1)
        popup.columnconfigure(0, weight=1)

        query_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=query_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

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

        commands: list[tuple[str, Callable[[], None]]] = [
            ("Ingest Popup", lambda: self._toggle_popup("ingest", self._open_ingest_popup)),
            ("Workers", lambda: self._toggle_popup("workers", self._open_jobs_popup)),
            ("Open Video", lambda: self._toggle_popup("open_video", self._open_video_picker_popup)),
            ("Finder", lambda: self._toggle_popup("finder", self._open_search_popup)),
            ("AI Agent", lambda: self._toggle_popup("ai", self._open_ai_popup)),
            ("Goto Timestamp", self._open_goto_popup),
            ("Settings", lambda: self._toggle_popup("settings", self._open_settings_popup)),
            ("Channel Browser", lambda: self._toggle_popup("channel", self._open_channel_popup)),
            ("Subscriptions", lambda: self._toggle_popup("subscriptions", self._open_subscriptions_popup)),
            ("Toggle Transcript Log", lambda: self._on_toggle_transcript_log(None)),  # type: ignore[arg-type]
            ("Toggle Details", lambda: self._on_toggle_details(None)),  # type: ignore[arg-type]
            ("Toggle Skim Mode", self._toggle_skim_mode),
            ("Quit", self.close),
        ]
        filtered_indexes: list[int] = list(range(len(commands)))

        def refresh(*_args: object) -> None:
            needle = query_var.get().strip().lower()
            filtered_indexes.clear()
            listbox.delete(0, tk.END)
            for idx, (label, _fn) in enumerate(commands):
                if needle and needle not in label.lower():
                    continue
                filtered_indexes.append(idx)
                listbox.insert(tk.END, label)
            if filtered_indexes:
                listbox.selection_set(0)

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                return "break"
            pos = int(sel[0])
            if pos < 0 or pos >= len(filtered_indexes):
                return "break"
            cmd_idx = filtered_indexes[pos]
            _label, fn = commands[cmd_idx]
            popup.destroy()
            fn()
            return "break"

        def move(delta: int) -> str:
            if not filtered_indexes:
                return "break"
            sel = listbox.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(filtered_indexes) - 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(nxt)
            listbox.activate(nxt)
            listbox.see(nxt)
            return "break"

        query_var.trace_add("write", refresh)
        popup.bind("<Escape>", lambda _e: popup.destroy())
        self._bind_fzf_like_keys(
            popup,
            entry=entry,
            capture_widgets=[entry, listbox],
            on_enter=lambda: run_selected(),
            on_up=lambda: move(-1),
            on_down=lambda: move(1),
            on_home=lambda: move(-10_000),
            on_end=lambda: move(10_000),
            on_page_up=lambda: move(-10),
            on_page_down=lambda: move(10),
        )
        listbox.bind("<Double-Button-1>", run_selected)
        entry.focus_set()
        refresh()

    def _ask_ai(self, prompt: str) -> str:
        api_context = self._agent_api_context()
        if api_context:
            prompt = (
                f"{api_context}\n\nUser request:\n{prompt}\n\n"
                "Return ONLY JSON with top-level key `actions` (array)."
            )
        provider = str(self._ai_settings.get("ai_provider") or "ollama").lower()
        if provider == "ollama":
            self._ensure_ollama_ready(block=True)
            base = str(self._ai_settings.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
            model = str(self._ai_settings.get("ollama_model") or "llama3.2:3b")
            payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/api/generate",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=payload,
            )
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                raw = resp.read()
            body = json.loads(raw.decode("utf-8"))
            return str(body.get("response") or "").strip() or "(empty response)"

        base = str(self._ai_settings.get("api_base_url") or "https://api.openai.com").rstrip("/")
        model = str(self._ai_settings.get("api_model") or "gpt-4o-mini")
        key_env = str(self._ai_settings.get("api_key_env") or "OPENAI_API_KEY")
        api_key = os.getenv(key_env, "")
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {key_env}")
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/v1/chat/completions",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            data=payload,
        )
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            raw = resp.read()
        body = json.loads(raw.decode("utf-8"))
        choices = body.get("choices") or []
        if not isinstance(choices, list) or not choices:
            return "(no choices)"
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first, dict) else {}
        if isinstance(msg, dict):
            return str(msg.get("content") or "").strip() or "(empty response)"
        return "(invalid response)"

    def _agent_api_context(self) -> str:
        return (
            "You are the in-app ALogger agent. Use this API surface:\n"
            "- list_channel_videos(channel_ref, limit:int<=100) -> channel metadata + entries[{title, video_id, url}]\n"
            "- enqueue_with_dedupe(urls:list[str], allow_overwrite:bool=False, auto_transcribe:bool|None)\n"
            "- add_channel_subscription(channel_ref, seed_with_latest:bool=True, auto_transcribe:bool|None)\n"
            "- list_channel_subscriptions(active_only:bool=False)\n"
            "- update_channel_subscription(channel_key, active:bool|None, auto_transcribe:bool|None, clear_auto_transcribe:bool=False)\n"
            "- poll_subscriptions_once()\n"
            "- search_video_titles(query, limit)\n"
            "- search_video_metadata(query, limit)  # matches title/channel/uploader/video_id/url with fuzzy fallback\n"
            "- search_videos(query, limit) and search_segments(query, limit)\n"
            "- jobs_summary(limit), dashboard_snapshot()\n"
            "- delete_video_and_assets(video_id)\n"
            "Preferred action schema (JSON only):\n"
            "{ \"actions\": [ {\"action\": \"<name>\", ...args } ] }\n"
            "Supported actions you can emit:\n"
            "- ingest_recent_matching_channel(channel_ref, query, count, fetch_limit=50, auto_transcribe=true)\n"
            "- enqueue_urls(urls, auto_transcribe=true)\n"
            "- list_channel_videos(channel_ref, limit=30)\n"
            "- subscribe_channel(channel_ref, auto_transcribe=null)\n"
            "- poll_subscriptions()\n"
            "- clear_queue()\n"
            "- kill_jobs()\n"
            "- open_video(video_id=<id>) OR open_video(query=<title query>)\n"
            "Rules:\n"
            "1) Prefer deterministic, minimal API calls.\n"
            "2) When asked to ingest N recent channel videos matching a title phrase:\n"
            "   call list_channel_videos(channel, limit=30-100), filter title case-insensitively, sort by listing order as newest-first, take N, enqueue URLs.\n"
            "3) Return concise action steps and the exact API calls/arguments."
        )

    def _parse_agent_actions(self, text: str) -> list[dict[str, Any]]:
        src = text.strip()
        if not src:
            return []
        candidates: list[str] = []
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", src, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend([c.strip() for c in fenced if c.strip()])
        # Pull potential inline JSON object/array if model added prose around it.
        m_obj = re.search(r"(\{[\s\S]*\})", src)
        if m_obj:
            candidates.append(m_obj.group(1).strip())
        m_arr = re.search(r"(\[[\s\S]*\])", src)
        if m_arr:
            candidates.append(m_arr.group(1).strip())
        candidates.append(src)
        for payload in candidates:
            data: Any
            try:
                data = json.loads(payload)
            except Exception:
                # Accept Python-literal style payloads as a fallback.
                try:
                    data = ast.literal_eval(payload)
                except Exception:
                    continue
            # Common failure mode: model returns a JSON string containing JSON.
            if isinstance(data, str):
                inner = data.strip()
                if inner.startswith("{") or inner.startswith("["):
                    try:
                        data = json.loads(inner)
                    except Exception:
                        try:
                            data = ast.literal_eval(inner)
                        except Exception:
                            continue
            if isinstance(data, dict):
                acts = data.get("actions")
                if isinstance(acts, list):
                    return [a for a in acts if isinstance(a, dict)]
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict)]
        return []

    def _normalize_agent_action_name(self, name: str) -> str:
        key = str(name or "").strip().lower()
        alias_map = {
            "download_most_recent_video": "ingest_recent_matching_channel",
            "download_recent_video": "ingest_recent_matching_channel",
            "ingest_recent_video": "ingest_recent_matching_channel",
            "enqueue_video_urls": "enqueue_urls",
            "play_video": "open_video",
            "launch_video": "open_video",
        }
        return alias_map.get(key, key)

    def _prompt_intent_ast(self, prompt: str) -> dict[str, Any]:
        raw = prompt.strip()
        low = raw.lower()
        wants_download = any(k in low for k in ("download", "ingest", "queue", "enqueue"))
        wants_play = any(k in low for k in ("play", "open", "watch"))
        wants_recent = any(k in low for k in ("most recent", "latest", "newest", "recent"))
        m_from = re.search(r"(?:from|by)\s+([a-z0-9_@.\-]+)", low)
        channel_ref = m_from.group(1).strip() if m_from else ""
        count = 1
        m_count = re.search(r"\b(\d+)\b", low)
        if m_count:
            count = max(1, int(m_count.group(1)))
        else:
            word_to_num = {
                "one": 1,
                "two": 2,
                "three": 3,
                "four": 4,
                "five": 5,
                "six": 6,
                "seven": 7,
                "eight": 8,
                "nine": 9,
                "ten": 10,
            }
            for w, n in word_to_num.items():
                if re.search(rf"\b{w}\b", low):
                    count = n
                    break
        query = ""
        m_title_phrase = re.search(
            r"(?:with|containing)\s+['\"]?(.+?)['\"]?\s+in\s+(?:the\s+)?title\b",
            raw,
            flags=re.IGNORECASE,
        )
        if m_title_phrase:
            query = m_title_phrase.group(1).strip()
        m_contains = re.search(r"(?:containing|contains|with)\s+['\"]?([^'\"]+)['\"]?", raw, flags=re.IGNORECASE)
        if m_contains and not query:
            query = m_contains.group(1).strip()
        if not channel_ref and wants_download:
            # Common phrasing: "download the most recent <channel> video"
            m_recent_channel = re.search(
                r"(?:most\s+recent|latest|newest|recent)\s+([a-z0-9_@.\-]+)\s+video[s]?\b",
                low,
            )
            if m_recent_channel:
                channel_ref = m_recent_channel.group(1).strip()
        if not channel_ref and wants_download:
            # Also support: "download <channel> video(s)"
            m_download_channel = re.search(
                r"\bdownload\s+(?:the\s+)?(?:most\s+recent|latest|newest|recent\s+)?([a-z0-9_@.\-]+)\s+video[s]?\b",
                low,
            )
            if m_download_channel:
                channel_ref = m_download_channel.group(1).strip()
        # If channel wasn't captured via from/by, infer a likely channel token.
        if not channel_ref and wants_download:
            scrub = re.sub(r"[^a-z0-9_@.\-\s]", " ", low)
            tokens = [t for t in scrub.split() if t]
            stop = {
                "download", "ingest", "queue", "enqueue", "the", "a", "an",
                "most", "recent", "latest", "newest", "video", "videos",
                "from", "by", "please", "and", "of", "to", "for", "me",
                "with", "containing", "contains",
            }
            candidates = [t for t in tokens if t not in stop and not t.isdigit()]
            if candidates:
                # Prefer handle-like tokens (longer, includes _-.@).
                candidates.sort(key=lambda t: (any(ch in t for ch in "_-.@"), len(t)), reverse=True)
                channel_ref = candidates[0]
        return {
            "wants_download": wants_download,
            "wants_play": wants_play,
            "wants_recent": wants_recent,
            "channel_ref": channel_ref,
            "query": query,
            "count": count,
            "raw_prompt": raw,
        }

    def _coerce_actions_with_prompt_ast(
        self,
        prompt_ast: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not actions:
            return actions
        wants_download = bool(prompt_ast.get("wants_download"))
        wants_play = bool(prompt_ast.get("wants_play"))
        wants_recent = bool(prompt_ast.get("wants_recent"))
        channel_ref = str(prompt_ast.get("channel_ref") or "").strip()
        query = str(prompt_ast.get("query") or "").strip()
        count = max(1, int(prompt_ast.get("count") or 1))

        normalized: list[dict[str, Any]] = []
        for act in actions:
            item = dict(act)
            item["action"] = self._normalize_agent_action_name(str(item.get("action") or ""))
            normalized.append(item)

        # Download intent ALWAYS means browse/ingest workflow, never open playback.
        if wants_download:
            # If parser missed the channel, salvage one from model args.
            if not channel_ref:
                for a in normalized:
                    maybe = str(
                        a.get("channel_ref")
                        or a.get("channel")
                        or a.get("creator")
                        or a.get("uploader")
                        or ""
                    ).strip()
                    if maybe:
                        channel_ref = maybe
                        break
                if not channel_ref:
                    for a in normalized:
                        if str(a.get("action")) == "open_video":
                            maybe = str(a.get("query") or "").strip()
                            if maybe and " " not in maybe:
                                channel_ref = maybe
                                break
            if channel_ref:
                return [
                    {
                        "action": "ingest_recent_matching_channel",
                        "channel_ref": channel_ref,
                        "query": query,
                        "count": count,
                        "fetch_limit": max(30, min(100, count * 10)),
                        "auto_transcribe": True,
                    }
                ]
            # Last fallback: keep only ingest-safe actions, never playback actions.
            return [
                a
                for a in normalized
                if str(a.get("action")) in {"ingest_recent_matching_channel", "enqueue_urls", "list_channel_videos"}
            ]

        # Play-centric prompts should always produce open_video if missing.
        if wants_play:
            has_open = any(str(a.get("action")) == "open_video" for a in normalized)
            if not has_open:
                q = query or channel_ref or ""
                if q:
                    normalized.insert(0, {"action": "open_video", "query": q})
        return normalized

    def _infer_actions_from_prompt(self, prompt: str) -> list[dict[str, Any]]:
        text = prompt.strip()
        low = text.lower()
        ast = self._prompt_intent_ast(text)
        actions: list[dict[str, Any]] = []

        ref = str(ast.get("channel_ref") or "").strip(" .,!?:;\"'")
        count = max(1, int(ast.get("count") or 1))
        query = str(ast.get("query") or "")

        # Download/ingest intent with a resolved channel can run directly.
        if bool(ast.get("wants_download")) and ref:
            actions.append(
                {
                    "action": "ingest_recent_matching_channel",
                    "channel_ref": ref,
                    "query": query,
                    "count": count,
                    "fetch_limit": max(30, min(100, count * 10)),
                    "auto_transcribe": True,
                }
            )
            return actions

        # Implicit ingest intent:
        # e.g. "most recent northernlion video with 'slay the spire' in the title"
        is_phrase_request = bool(re.search(r"\b(most\s+recent|latest|newest|recent)\b", low)) and "video" in low
        has_explicit_non_ingest_intent = any(k in low for k in ("play", "open", "watch", "subscribe"))
        if ref and is_phrase_request and not has_explicit_non_ingest_intent:
            actions.append(
                {
                    "action": "ingest_recent_matching_channel",
                    "channel_ref": ref,
                    "query": query,
                    "count": count,
                    "fetch_limit": max(30, min(100, count * 10)),
                    "auto_transcribe": True,
                }
            )
            return actions

        # "play/open/watch ... video" -> open by metadata query.
        if bool(ast.get("wants_play")):
            q = re.sub(r"^(play|open|watch)\s+", "", text, flags=re.IGNORECASE).strip()
            q = re.sub(r"\s+video[s]?\s*$", "", q, flags=re.IGNORECASE).strip()
            if q:
                actions.append({"action": "open_video", "query": q})
                return actions
        return actions

    def _hashtags_from_text(self, text: str) -> list[str]:
        tags = re.findall(r"#([a-z0-9_]+)", str(text or ""), flags=re.IGNORECASE)
        out: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            token = f"#{tag.lower()}"
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    def _download_browse_thumbnail(self, video_id: str, thumbnail_url: str) -> Path | None:
        if not video_id or not thumbnail_url:
            return None
        if self._browse_thumb_dir is None:
            return None
        out_path = self._browse_thumb_dir / f"{video_id}.png"
        if out_path.exists():
            return out_path
        try:
            req = urllib.request.Request(
                thumbnail_url,
                headers={"User-Agent": "alogger/1.0 (+https://localhost)"},
            )
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                raw = resp.read()
            if Image is not None:
                with Image.open(io.BytesIO(raw)) as img:
                    img = img.convert("RGB")
                    img.thumbnail((420, 236))
                    img.save(out_path, format="PNG")
                return out_path
            # Fallback when Pillow is unavailable: keep raw image bytes on disk.
            ext = ".jpg"
            lower = thumbnail_url.lower()
            if lower.endswith(".webp"):
                ext = ".webp"
            elif lower.endswith(".png"):
                ext = ".png"
            raw_path = self._browse_thumb_dir / f"{video_id}{ext}"
            raw_path.write_bytes(raw)
            return raw_path
        except Exception:
            return None

    def _get_browse_preview(self, row: dict[str, Any]) -> dict[str, Any]:
        video_id = str(row.get("video_id") or "").strip()
        cache_key = video_id or str(row.get("url") or "").strip()
        if cache_key and cache_key in self._browse_preview_cache:
            cached = dict(self._browse_preview_cache[cache_key])
            has_image = bool(str(cached.get("image_path") or "").strip())
            has_tags = bool(list(cached.get("hashtags") or []))
            meta_ok = bool(cached.get("meta_ok"))
            # Retry incomplete cached rows so transient failures can recover.
            if has_image and (has_tags or meta_ok):
                return cached

        title = str(row.get("title") or row.get("video_id") or "untitled").strip()
        creator = str(row.get("uploader") or row.get("channel") or "").strip()
        hashtags = self._hashtags_from_text(title)
        thumbnail_url = str(row.get("thumbnail") or "").strip()
        url = str(row.get("url") or "").strip()
        meta_ok = False

        if url:
            try:
                meta = dict(self.ingester.fetch_url_metadata(url))
                meta_ok = True
                title = str(meta.get("title") or title).strip()
                creator = str(meta.get("uploader") or meta.get("channel") or creator).strip()
                thumbnail_url = str(meta.get("thumbnail") or thumbnail_url).strip()
                if not hashtags:
                    hashtags = self._hashtags_from_text(str(meta.get("description") or ""))
                if not hashtags:
                    raw_tags = meta.get("tags") or []
                    if isinstance(raw_tags, list):
                        for tag in raw_tags:
                            token = str(tag or "").strip().replace(" ", "")
                            if not token:
                                continue
                            if token.startswith("#"):
                                hashtags.append(token.lower())
                            elif len(hashtags) < 8:
                                hashtags.append(f"#{token.lower()}")
                            if len(hashtags) >= 8:
                                break
            except Exception:
                pass

        if not thumbnail_url and video_id:
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/0.jpg"

        image_path = self._download_browse_thumbnail(video_id, thumbnail_url) if video_id else None
        preview = {
            "video_id": video_id,
            "title": title or "untitled",
            "creator": creator or "unknown",
            "hashtags": hashtags[:8],
            "thumbnail_url": thumbnail_url,
            "image_path": str(image_path) if image_path else "",
            "meta_ok": meta_ok,
        }
        if cache_key and (preview["image_path"] or preview["hashtags"] or preview["meta_ok"]):
            self._browse_preview_cache[cache_key] = dict(preview)
        return preview

    def _record_agent_activity(self, kind: str, message: str) -> None:
        stamp = _fmt_hms(max(0, self.player.get_time()) / 1000.0) if hasattr(self, "player") else "00:00:00"
        self._agent_activity.append(
            {
                "time": stamp,
                "kind": str(kind).strip() or "info",
                "message": str(message).strip(),
            }
        )
        if len(self._agent_activity) > 300:
            self._agent_activity = self._agent_activity[-300:]

    def _open_video_from_row(self, row: dict[str, Any], *, filter_text: str = "") -> tuple[bool, str]:
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            return False, "row has no video_id"
        transcript_raw = str(row.get("transcript_json_path") or "").strip()
        transcript_path = Path(transcript_raw) if transcript_raw else None
        preferred = Path(str(row.get("local_video_path") or "")) if row.get("local_video_path") else None
        if transcript_path is not None and not transcript_path.exists():
            transcript_path = None
        try:
            video_path = resolve_playback_media_path(
                self.ingester_config,
                video_id=video_id,
                preferred_path=preferred,
            )
        except Exception as exc:
            return False, f"playback path error: {exc}"
        audio_path = self._find_audio_sidecar(video_id, video_path)
        self._load_session(
            video_id=video_id,
            transcript_json=transcript_path,
            video_path=video_path,
            audio_path=audio_path,
            start_sec=0.0,
            filter_text=filter_text,
        )
        return True, f"opened video {video_id}"

    def _execute_agent_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        emit: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        should_close_ai = False
        executed = 0
        failed = 0
        events: list[dict[str, str]] = []
        if not actions:
            return {"close_ai": False, "executed": 0, "failed": 0, "events": events}
        for i, act in enumerate(actions, start=1):
            name = str(act.get("action") or "").strip().lower()
            if not name:
                if emit:
                    emit("err", f"action #{i}: missing `action`")
                failed += 1
                continue
            name = self._normalize_agent_action_name(name)
            try:
                if name == "ingest_recent_matching_channel":
                    channel_ref = str(
                        act.get("channel_ref")
                        or act.get("channel")
                        or act.get("creator")
                        or act.get("uploader")
                        or ""
                    ).strip()
                    query = str(act.get("query") or "").strip().lower()
                    count = max(1, int(act.get("count") or 1))
                    fetch_limit = max(count, min(100, int(act.get("fetch_limit") or 50)))
                    auto_transcribe = bool(act.get("auto_transcribe", True))
                    data = self.ingester.list_channel_videos(channel_ref, limit=fetch_limit)
                    entries = [dict(r) for r in (data.get("entries") or [])]
                    matched: list[dict[str, Any]] = []
                    for row in entries:
                        title = str(row.get("title") or "").lower()
                        if query and query not in title:
                            continue
                        matched.append(row)
                        if len(matched) >= count:
                            break
                    urls = [str(r.get("url") or "").strip() for r in matched if str(r.get("url") or "").strip()]
                    result = self.ingester.enqueue_with_dedupe(
                        urls,
                        allow_overwrite=False,
                        auto_transcribe=auto_transcribe,
                    )
                    ids = list(result.get("queued_ids") or [])
                    self.status_var.set(f"Agent queued {len(ids)} ingest jobs")
                    if emit:
                        emit("act", f"{name}: matched={len(matched)} queued={len(ids)}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: matched={len(matched)} queued={len(ids)}"})
                    continue

                if name == "enqueue_urls":
                    raw_urls = act.get("urls") or []
                    urls = [str(u).strip() for u in raw_urls if str(u).strip()]
                    auto_transcribe = bool(act.get("auto_transcribe", True))
                    result = self.ingester.enqueue_with_dedupe(
                        urls,
                        allow_overwrite=False,
                        auto_transcribe=auto_transcribe,
                    )
                    ids = list(result.get("queued_ids") or [])
                    self.status_var.set(f"Agent queued {len(ids)} ingest jobs")
                    if emit:
                        emit("act", f"{name}: queued={len(ids)}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: queued={len(ids)}"})
                    continue

                if name == "list_channel_videos":
                    channel_ref = str(act.get("channel_ref") or "").strip()
                    limit = max(1, min(100, int(act.get("limit") or 30)))
                    data = self.ingester.list_channel_videos(channel_ref, limit=limit)
                    entries = data.get("entries") or []
                    if emit:
                        emit("act", f"{name}: channel={data.get('channel')} entries={len(entries)}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: channel={data.get('channel')} entries={len(entries)}"})
                    continue

                if name == "search_video_metadata":
                    query = str(act.get("query") or "").strip()
                    limit = max(1, min(200, int(act.get("limit") or 30)))
                    rows = self.ingester.search_video_metadata(query, limit=limit)
                    if emit:
                        emit("act", f"{name}: query={query!r} matches={len(rows)}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: query={query!r} matches={len(rows)}"})
                    continue

                if name == "subscribe_channel":
                    channel_ref = str(act.get("channel_ref") or "").strip()
                    auto_mode = act.get("auto_transcribe", None)
                    auto_transcribe = None if auto_mode is None else bool(auto_mode)
                    sub = self.ingester.add_channel_subscription(
                        channel_ref,
                        seed_with_latest=True,
                        auto_transcribe=auto_transcribe,
                    )
                    if emit:
                        emit("act", f"{name}: {sub.get('channel_title')} ({sub.get('channel_key')})")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: {sub.get('channel_title')} ({sub.get('channel_key')})"})
                    continue

                if name == "poll_subscriptions":
                    summary = self.ingester.poll_subscriptions_once()
                    if emit:
                        emit("act", f"{name}: scanned={summary.get('scanned', 0)} queued={summary.get('queued', 0)}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: scanned={summary.get('scanned', 0)} queued={summary.get('queued', 0)}"})
                    continue

                if name == "clear_queue":
                    n = int(self.ingester.clear_queue())
                    if emit:
                        emit("act", f"{name}: cleared={n}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: cleared={n}"})
                    continue

                if name == "kill_jobs":
                    n = int(self.ingester.kill_active_jobs())
                    if emit:
                        emit("act", f"{name}: killed={n}")
                    executed += 1
                    events.append({"kind": "act", "message": f"{name}: killed={n}"})
                    continue

                if name == "open_video":
                    video_id = str(act.get("video_id") or "").strip()
                    query = str(act.get("query") or "").strip()
                    row: dict[str, Any] | None = None
                    if video_id:
                        done = self.ingester.db.get_latest_done_job_for_video(video_id)  # type: ignore[attr-defined]
                        if done:
                            row = dict(done)
                    if row is None and query:
                        rows = self.ingester.search_video_metadata(query, limit=50)
                        if not rows:
                            rows = self.ingester.search_video_titles(query, limit=50)
                        if rows:
                            row = dict(rows[0])
                    if row is None:
                        if emit:
                            emit("err", f"{name}: no matching playable video")
                        continue
                    ok, msg = self._open_video_from_row(row, filter_text=query)
                    if emit:
                        emit("act" if ok else "err", f"{name}: {msg}")
                    if ok:
                        executed += 1
                        events.append({"kind": "act", "message": f"{name}: {msg}"})
                        should_close_ai = True
                    else:
                        failed += 1
                        events.append({"kind": "err", "message": f"{name}: {msg}"})
                    continue

                if emit:
                    emit("err", f"unsupported action: {name}")
                failed += 1
                events.append({"kind": "err", "message": f"unsupported action: {name}"})
            except Exception as exc:
                if emit:
                    emit("err", f"{name} failed: {exc}")
                failed += 1
                events.append({"kind": "err", "message": f"{name} failed: {exc}"})
        summary = {
            "close_ai": should_close_ai,
            "executed": executed,
            "failed": failed,
            "events": events,
        }
        self._record_agent_activity(
            "summary",
            f"actions={len(actions)} executed={executed} failed={failed}",
        )
        for ev in events:
            self._record_agent_activity(str(ev.get("kind") or "act"), str(ev.get("message") or ""))
        return summary

    def _open_ai_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Agent", "980x680")
        self._ai_popup = popup
        self._register_popup("ai", popup)
        popup.rowconfigure(0, weight=1)
        popup.columnconfigure(0, weight=1)

        feed = tk.Text(
            popup,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            wrap="word",
            padx=8,
            pady=8,
            insertbackground="#ffffff",
        )
        feed.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))
        feed.configure(state="disabled")

        input_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=input_var, style="Filter.TEntry")
        entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        provider = str(self._ai_settings.get("ai_provider", "ollama"))
        if provider.lower() == "ollama":
            self._start_ollama_bootstrap(background=True)
        status_var = tk.StringVar(
            value=f"Provider={provider} | Enter send | Esc close"
        )
        status = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status.grid(row=2, column=0, sticky="ew")

        def append_line(prefix: str, text: str) -> None:
            feed.configure(state="normal")
            feed.insert(tk.END, f"[{prefix}] {text}\n\n")
            feed.see("end")
            feed.configure(state="disabled")

        append_line(
            "sys",
            "Agent initialized with app API context. Ask for ingest/search/subscription actions directly.",
        )

        def send_prompt(_event: tk.Event[tk.Misc] | None = None) -> str:
            prompt = input_var.get().strip()
            if not prompt:
                return "break"
            input_var.set("")
            append_line("you", prompt)
            prompt_ast = self._prompt_intent_ast(prompt)
            direct_actions = self._infer_actions_from_prompt(prompt)
            if direct_actions:
                append_line("sys", f"AST resolved prompt directly ({len(direct_actions)} action(s)); skipping model.")
                summary = self._execute_agent_actions(
                    direct_actions,
                    emit=lambda p, t: append_line(p, t),
                )
                append_line(
                    "sys",
                    f"Direct-action summary: executed={summary.get('executed', 0)} failed={summary.get('failed', 0)}",
                )
                status_var.set("Ready")
                if bool(summary.get("close_ai")) and popup.winfo_exists():
                    popup.destroy()
                    self.filter_entry.focus_set()
                return "break"
            if provider.lower() == "ollama" and not self._ollama_bootstrap_done.is_set():
                status_var.set(f"Ollama startup: {self._ollama_bootstrap_state} ...")
            else:
                status_var.set("Running model (AST fallback)...")
            self.root.update_idletasks()
            try:
                reply = self._ask_ai(
                    f"User prompt: {prompt}\n"
                    f"Prompt AST: {json.dumps(prompt_ast, ensure_ascii=False)}\n"
                    "Use AST as source of truth when translating to actions."
                )
                actions = self._parse_agent_actions(reply)
                actions = self._coerce_actions_with_prompt_ast(prompt_ast, actions)
                if actions:
                    append_line("ai", f"Action plan received ({len(actions)} action(s))")
                    append_line("sys", f"Executing {len(actions)} action(s)")
                    summary = self._execute_agent_actions(
                        actions,
                        emit=lambda p, t: append_line(p, t),
                    )
                    append_line(
                        "sys",
                        f"Action summary: executed={summary.get('executed', 0)} failed={summary.get('failed', 0)}",
                    )
                    if bool(summary.get("close_ai")) and popup.winfo_exists():
                        popup.destroy()
                        self.filter_entry.focus_set()
                        return "break"
                else:
                    append_line("ai", reply)
                    inferred = self._infer_actions_from_prompt(prompt)
                    if inferred:
                        append_line("sys", f"No valid action JSON found; inferred {len(inferred)} action(s) from prompt.")
                        summary = self._execute_agent_actions(
                            inferred,
                            emit=lambda p, t: append_line(p, t),
                        )
                        append_line(
                            "sys",
                            f"Inferred-action summary: executed={summary.get('executed', 0)} failed={summary.get('failed', 0)}",
                        )
                        if bool(summary.get("close_ai")) and popup.winfo_exists():
                            popup.destroy()
                            self.filter_entry.focus_set()
                            return "break"
                    else:
                        append_line("sys", "No structured actions found (expected JSON actions array).")
                        self._record_agent_activity("info", "AI reply had no structured actions")
                status_var.set("Ready")
            except urllib.error.URLError as exc:
                append_line("err", str(exc))
                status_var.set(f"AI error: {exc}")
            except Exception as exc:
                append_line("err", str(exc))
                status_var.set(f"AI error: {exc}")
            return "break"

        def _tick_status() -> None:
            if not popup.winfo_exists():
                return
            if provider.lower() == "ollama":
                if self._ollama_bootstrap_done.is_set():
                    if self._ollama_bootstrap_success:
                        status_var.set(f"Provider={provider} ready ({self._ollama_model()})")
                    else:
                        status_var.set(
                            f"Ollama init failed: {self._ollama_bootstrap_error or 'unknown error'}"
                        )
                else:
                    status_var.set(f"Ollama startup: {self._ollama_bootstrap_state} ...")
            popup.after(1000, _tick_status)

        popup.bind("<Escape>", lambda _e: popup.destroy())
        popup.bind("<Return>", send_prompt)
        entry.bind("<Return>", send_prompt)
        entry.bind("<KP_Enter>", send_prompt)
        entry.focus_set()
        _tick_status()

    def _open_goto_popup(self) -> None:
        if self._goto_popup and self._goto_popup.winfo_exists():
            self._goto_popup.focus_force()
            return
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Goto Timestamp", "420x160")
        self._goto_popup = popup
        self._register_popup("goto", popup)
        popup.columnconfigure(0, weight=1)

        value_var = tk.StringVar(value="")
        entry = ttk.Entry(popup, textvariable=value_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        status_var = tk.StringVar(value="Type digits only: 2=SS, 3-4=MMSS, 5+=HHMMSS")
        tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            padx=8,
            pady=6,
        ).grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))

        fmt_guard = {"active": False}

        def format_compact_time(raw: str) -> str:
            digits = re.sub(r"\D", "", str(raw or ""))
            if not digits:
                return ""
            if len(digits) <= 2:
                sec = digits.zfill(2)
                return f"00:00:{sec}"
            if len(digits) <= 4:
                mm = digits[:-2].zfill(2)
                ss = digits[-2:]
                return f"00:{mm}:{ss}"
            hh_raw = digits[:-4]
            hh = hh_raw if len(hh_raw) >= 2 else hh_raw.zfill(2)
            mm = digits[-4:-2]
            ss = digits[-2:]
            return f"{hh}:{mm}:{ss}"

        def on_time_change(*_args: object) -> None:
            if fmt_guard["active"]:
                return
            formatted = format_compact_time(value_var.get())
            if formatted == value_var.get():
                return
            fmt_guard["active"] = True
            value_var.set(formatted)
            entry.icursor(tk.END)
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
        popup.bind("<Escape>", lambda _e: popup.destroy())
        entry.focus_set()

    def _open_settings_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Settings", "980x700")
        self._settings_popup = popup
        self._register_popup("settings", popup)
        popup.rowconfigure(0, weight=1)
        popup.rowconfigure(1, weight=0)
        popup.rowconfigure(2, weight=0)
        popup.columnconfigure(0, weight=1)

        tabs = ttk.Notebook(popup, style="Terminal.TNotebook")
        tabs.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))

        tab_names = ["Ingest", "Theme", "Subscription", "AI", "Keybinds"]
        tab_frames: list[tk.Frame] = []
        key_lists: list[tk.Listbox] = []
        val_lists: list[tk.Listbox] = []
        for name in tab_names:
            frame = tk.Frame(tabs, bg="#111111")
            frame.rowconfigure(1, weight=1)
            frame.columnconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)
            tabs.add(frame, text=name)
            tab_frames.append(frame)

            tk.Label(
                frame,
                text="Key",
                anchor="w",
                bg="#0d0d0d",
                fg="#8f8f8f",
                font=(FONT["STYLE"], FONT["SIZE"] - 3),
                padx=8,
                pady=4,
            ).grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=(8, 4))
            tk.Label(
                frame,
                text="Value",
                anchor="w",
                bg="#0d0d0d",
                fg="#8f8f8f",
                font=(FONT["STYLE"], FONT["SIZE"] - 3),
                padx=8,
                pady=4,
            ).grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=(8, 4))

            k_list = tk.Listbox(
                frame,
                bg="#000000",
                fg="#d2d2d2",
                selectbackground="#161616",
                selectforeground="#ffffff",
                activestyle="none",
                borderwidth=0,
                highlightthickness=0,
                exportselection=False,
                font=(FONT["STYLE"], FONT["SIZE"] - 2),
            )
            v_list = tk.Listbox(
                frame,
                bg="#000000",
                fg="#ffffff",
                selectbackground="#161616",
                selectforeground="#ffffff",
                activestyle="none",
                borderwidth=0,
                highlightthickness=0,
                exportselection=False,
                font=(FONT["STYLE"], FONT["SIZE"] - 2),
            )
            k_list.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))
            v_list.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=(0, 8))
            key_lists.append(k_list)
            val_lists.append(v_list)

        edit_var = tk.StringVar(value="")
        edit_entry = ttk.Entry(popup, textvariable=edit_var, style="Filter.TEntry")
        edit_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        edit_entry.grid_remove()

        status_var = tk.StringVar(value="Up/Down select row | Left/Right change tab | Enter edit | Esc close")
        tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))

        keybind_defs: list[tuple[str, str]] = [
            ("Command menu", "open_command"),
            ("Ingest menu", "open_ingest"),
            ("Workers", "open_workers"),
            ("Open video", "open_video"),
            ("Finder", "open_finder"),
            ("AI", "open_ai"),
            ("Goto", "open_goto"),
            ("Skim toggle", "toggle_skim"),
            ("Settings", "open_settings"),
            ("Quit", "quit"),
            ("Play/Pause", "play_pause"),
            ("Seek back", "seek_back"),
            ("Seek forward", "seek_forward"),
            ("Prev match", "prev_match"),
            ("Next match", "next_match"),
            ("Vim left", "vim_left"),
            ("Vim down", "vim_down"),
            ("Vim up", "vim_up"),
            ("Vim right", "vim_right"),
            ("Clear input", "clear_input"),
            ("Toggle transcript", "toggle_transcript"),
            ("Toggle details", "toggle_details"),
        ]

        color_fields = {"theme_bg", "theme_panel_bg", "theme_fg", "theme_muted_fg", "theme_accent_fg"}

        def _norm_hex(value: str) -> str | None:
            token = value.strip().lower()
            if not token.startswith("#"):
                token = "#" + token
            if len(token) != 7:
                return None
            try:
                int(token[1:], 16)
                return token
            except Exception:
                return None

        def _sub_mode_text(row: dict[str, Any]) -> str:
            mode = row.get("auto_transcribe")
            if mode is None:
                return "default"
            return "on" if int(mode) == 1 else "off"

        def _rows_for_tab(tab_idx: int) -> list[dict[str, str]]:
            if tab_idx == 0:
                return [
                    {"id": "default_downloaders", "key": "Downloaders", "value": str(self._ai_settings.get("default_downloaders", 1))},
                    {"id": "default_transcribers", "key": "Transcribers", "value": str(self._ai_settings.get("default_transcribers", 1))},
                    {"id": "auto_transcribe_default", "key": "Auto Transcribe", "value": ("on" if self._auto_transcribe_default() else "off")},
                    {"id": "skim_pre", "key": "Skim Pre (ms)", "value": str(self._skim_pre_ms)},
                    {"id": "skim_post", "key": "Skim Post (ms)", "value": str(self._skim_post_ms)},
                ]
            if tab_idx == 1:
                return [
                    {"id": "theme_bg", "key": "App Background", "value": str(self._ai_settings.get("theme_bg") or "#111111")},
                    {"id": "theme_panel_bg", "key": "Panel Background", "value": str(self._ai_settings.get("theme_panel_bg") or "#000000")},
                    {"id": "theme_fg", "key": "Primary Text", "value": str(self._ai_settings.get("theme_fg") or "#ffffff")},
                    {"id": "theme_muted_fg", "key": "Muted Text", "value": str(self._ai_settings.get("theme_muted_fg") or "#8f8f8f")},
                    {"id": "theme_accent_fg", "key": "Accent Text", "value": str(self._ai_settings.get("theme_accent_fg") or "#f7d154")},
                    {"id": "font_family", "key": "Font Family", "value": str(self._ai_settings.get("font_family") or FONT["STYLE"])},
                    {"id": "font_size", "key": "Font Size", "value": str(self._ai_settings.get("font_size") or FONT["SIZE"])},
                ]
            if tab_idx == 2:
                rows = [
                    {
                        "id": "subscription_db_max_videos",
                        "key": "DB Max Videos",
                        "value": str(self._ai_settings.get("subscription_db_max_videos") or 0),
                    }
                ]
                try:
                    subs = self.ingester.list_channel_subscriptions(active_only=False)
                    for row in subs:
                        key = str(row.get("channel_key") or "")
                        title = str(row.get("channel_title") or key)
                        active = "on" if int(row.get("active") or 0) == 1 else "off"
                        mode = _sub_mode_text(dict(row))
                        rows.append(
                            {
                                "id": f"sub:{key}",
                                "key": f"{title} ({key})",
                                "value": f"active={active} mode={mode}",
                            }
                        )
                except Exception:
                    pass
                return rows
            if tab_idx == 3:
                return [
                    {"id": "ai_provider", "key": "Provider", "value": str(self._ai_settings.get("ai_provider") or "ollama")},
                    {"id": "ollama_model", "key": "Ollama Model", "value": str(self._ai_settings.get("ollama_model") or "llama3.2:3b")},
                    {"id": "ollama_base_url", "key": "Ollama Base URL", "value": str(self._ai_settings.get("ollama_base_url") or "http://127.0.0.1:11434")},
                    {"id": "api_base_url", "key": "API Base URL", "value": str(self._ai_settings.get("api_base_url") or "https://api.openai.com")},
                    {"id": "api_key_env", "key": "API Key Env", "value": str(self._ai_settings.get("api_key_env") or "OPENAI_API_KEY")},
                    {"id": "api_model", "key": "API Model", "value": str(self._ai_settings.get("api_model") or "gpt-4o-mini")},
                ]
            rows: list[dict[str, str]] = []
            kb = self._ai_settings.get("keybinds") or {}
            for label, key in keybind_defs:
                rows.append({"id": f"kb:{key}", "key": label, "value": str(kb.get(key, DEFAULT_KEYBINDS.get(key, "")))})
            return rows

        selected_idx = [0 for _ in tab_names]
        tab_rows: list[list[dict[str, str]]] = [[] for _ in tab_names]
        editing = {"active": False, "tab": 0, "row": 0, "orig": ""}

        def _sync_selection(tab_idx: int) -> None:
            rows = tab_rows[tab_idx]
            if not rows:
                return
            idx = max(0, min(selected_idx[tab_idx], len(rows) - 1))
            selected_idx[tab_idx] = idx
            k = key_lists[tab_idx]
            v = val_lists[tab_idx]
            k.selection_clear(0, tk.END)
            v.selection_clear(0, tk.END)
            k.selection_set(idx)
            v.selection_set(idx)
            k.activate(idx)
            v.activate(idx)
            k.see(idx)
            v.see(idx)

        def _render_tab(tab_idx: int) -> None:
            tab_rows[tab_idx] = _rows_for_tab(tab_idx)
            k = key_lists[tab_idx]
            v = val_lists[tab_idx]
            k.delete(0, tk.END)
            v.delete(0, tk.END)
            for row in tab_rows[tab_idx]:
                k.insert(tk.END, row["key"])
                v.insert(tk.END, row["value"])
            _sync_selection(tab_idx)

        def _render_current() -> None:
            _render_tab(int(tabs.index("current")))

        def _apply_row_value(row_id: str, value: str) -> str | None:
            prev_provider = str(self._ai_settings.get("ai_provider") or "ollama")
            prev_model = str(self._ai_settings.get("ollama_model") or "llama3.2:3b")
            prev_base = str(self._ai_settings.get("ollama_base_url") or "http://127.0.0.1:11434")
            token = value.strip()
            try:
                if row_id == "default_downloaders":
                    self._ai_settings["default_downloaders"] = max(0, int(token or "0"))
                elif row_id == "default_transcribers":
                    self._ai_settings["default_transcribers"] = max(0, int(token or "0"))
                elif row_id == "auto_transcribe_default":
                    self._ai_settings["auto_transcribe_default"] = token.lower() in {"1", "true", "yes", "on"}
                elif row_id == "skim_pre":
                    self._skim_pre_ms = max(0, int(token or "0"))
                elif row_id == "skim_post":
                    self._skim_post_ms = max(0, int(token or "0"))
                elif row_id in {"theme_bg", "theme_panel_bg", "theme_fg", "theme_muted_fg", "theme_accent_fg"}:
                    color = _norm_hex(token)
                    if not color:
                        return "Invalid hex color; use #RRGGBB"
                    self._ai_settings[row_id] = color
                elif row_id == "font_family":
                    self._ai_settings["font_family"] = token or FONT["STYLE"]
                elif row_id == "font_size":
                    self._ai_settings["font_size"] = max(8, int(token or "12"))
                elif row_id == "subscription_db_max_videos":
                    self._ai_settings["subscription_db_max_videos"] = max(0, int(token or "0"))
                elif row_id.startswith("sub:"):
                    channel_key = row_id.split(":", 1)[1]
                    if token.lower() in {"remove", "rm", "delete"}:
                        self.ingester.remove_channel_subscription(channel_key)
                    else:
                        parts = token.replace(",", " ").split()
                        active_val: bool | None = None
                        mode_val: str | None = None
                        for part in parts:
                            if "=" not in part:
                                continue
                            k, v = part.split("=", 1)
                            kl = k.strip().lower()
                            vl = v.strip().lower()
                            if kl == "active":
                                active_val = vl in {"on", "1", "true", "yes"}
                            if kl in {"mode", "transcribe"}:
                                mode_val = vl
                        if active_val is not None:
                            self.ingester.update_channel_subscription(channel_key, active=active_val)
                        if mode_val is not None:
                            if mode_val == "default":
                                self.ingester.update_channel_subscription(channel_key, clear_auto_transcribe=True)
                            elif mode_val in {"on", "true", "1", "yes"}:
                                self.ingester.update_channel_subscription(channel_key, auto_transcribe=True)
                            elif mode_val in {"off", "false", "0", "no"}:
                                self.ingester.update_channel_subscription(channel_key, auto_transcribe=False)
                elif row_id in {"ai_provider", "ollama_model", "ollama_base_url", "api_base_url", "api_key_env", "api_model"}:
                    self._ai_settings[row_id] = token
                    if row_id == "ai_provider" and not token:
                        self._ai_settings[row_id] = "ollama"
                elif row_id.startswith("kb:"):
                    action = row_id.split(":", 1)[1]
                    kb = self._ai_settings.get("keybinds")
                    if not isinstance(kb, dict):
                        kb = dict(DEFAULT_KEYBINDS)
                    kb[action] = token or DEFAULT_KEYBINDS.get(action, "")
                    self._ai_settings["keybinds"] = kb
                else:
                    return "Unknown setting"

                self._ai_settings["default_worker_count"] = max(
                    int(self._ai_settings.get("default_downloaders") or 0),
                    int(self._ai_settings.get("default_transcribers") or 0),
                )
                self.ingester.set_runtime_options(
                    auto_transcribe_default=bool(self._ai_settings.get("auto_transcribe_default", True)),
                    subscription_db_max_videos=int(self._ai_settings.get("subscription_db_max_videos") or 0),
                )
                target_downloaders = max(0, int(self._ai_settings.get("default_downloaders") or 0))
                target_transcribers = max(0, int(self._ai_settings.get("default_transcribers") or 0))
                target_workers = max(target_downloaders, target_transcribers)
                if (
                    self._worker_target_count != target_workers
                    or self._downloader_target_count != target_downloaders
                    or self._transcriber_target_count != target_transcribers
                ):
                    self._restart_workers(target_workers, target_downloaders, target_transcribers)

                self._apply_theme()
                self._apply_font_scale_tree(self.root, reset_base=True)
                self._refresh_caption_view()
                self._apply_shortcut_bindings()
                self._save_gui_settings()

                new_provider = str(self._ai_settings.get("ai_provider") or "ollama")
                if new_provider.lower() == "ollama":
                    if (
                        prev_provider.lower() != "ollama"
                        or prev_model != str(self._ai_settings.get("ollama_model"))
                        or prev_base != str(self._ai_settings.get("ollama_base_url"))
                    ):
                        self._start_ollama_bootstrap(background=True, force_reset=True)
                self.status_var.set("Settings saved")
                return None
            except Exception as exc:
                return str(exc)

        for i in range(len(tab_names)):
            _render_tab(i)

        def _start_edit() -> None:
            tab = int(tabs.index("current"))
            rows = tab_rows[tab]
            if not rows:
                return
            row = max(0, min(selected_idx[tab], len(rows) - 1))
            editing["active"] = True
            editing["tab"] = tab
            editing["row"] = row
            editing["orig"] = rows[row]["value"]
            edit_var.set(rows[row]["value"])
            edit_entry.grid()
            edit_entry.focus_set()
            edit_entry.selection_range(0, tk.END)
            status_var.set(f"Editing `{rows[row]['key']}` | Enter commit | Esc revert")

        def _stop_edit(*, revert: bool) -> None:
            if not editing["active"]:
                return
            tab = int(editing["tab"])
            row = int(editing["row"])
            rows = tab_rows[tab]
            if not revert and rows and row < len(rows):
                err = _apply_row_value(rows[row]["id"], edit_var.get())
                if err:
                    status_var.set(f"Apply failed: {err}")
                    return
                status_var.set("Saved")
            if revert:
                status_var.set("Edit canceled")
            editing["active"] = False
            edit_entry.grid_remove()
            _render_current()
            key_lists[int(tabs.index("current"))].focus_set()

        def _move_row(delta: int) -> None:
            tab = int(tabs.index("current"))
            rows = tab_rows[tab]
            if not rows:
                return
            selected_idx[tab] = max(0, min(selected_idx[tab] + delta, len(rows) - 1))
            _sync_selection(tab)

        def _switch_tab(delta: int) -> None:
            cur = int(tabs.index("current"))
            nxt = max(0, min(cur + delta, len(tab_names) - 1))
            tabs.select(nxt)
            _render_tab(nxt)
            key_lists[nxt].focus_set()

        def _on_key(event: tk.Event[tk.Misc]) -> str | None:
            keysym = str(getattr(event, "keysym", ""))
            if editing["active"]:
                if keysym == "Return":
                    _stop_edit(revert=False)
                    return "break"
                if keysym == "Escape":
                    _stop_edit(revert=True)
                    return "break"
                return None

            if keysym == "Escape":
                popup.destroy()
                return "break"
            if keysym in {"Left", "h", "H"}:
                _switch_tab(-1)
                return "break"
            if keysym in {"Right", "l", "L"}:
                _switch_tab(1)
                return "break"
            if keysym in {"Up", "k", "K"}:
                _move_row(-1)
                return "break"
            if keysym in {"Down", "j", "J"}:
                _move_row(1)
                return "break"
            if keysym == "Return":
                _start_edit()
                return "break"
            return "break"

        for lb in [*key_lists, *val_lists]:
            lb.bind("<KeyPress>", _on_key, add="+")
            lb.bind("<Button-1>", lambda _e: "break")
        edit_entry.bind("<KeyPress>", _on_key, add="+")
        popup.bind("<KeyPress>", _on_key, add="+")
        tabs.bind("<<NotebookTabChanged>>", lambda _e: (_render_current(), key_lists[int(tabs.index("current"))].focus_set()), add="+")
        key_lists[0].focus_set()

    def _open_search_popup(self) -> None:
        if self._search_popup and self._search_popup.winfo_exists():
            self._search_popup.focus_force()
            return

        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Search DB", "900x620")
        self._search_popup = popup
        self._register_popup("finder", popup)
        popup.rowconfigure(2, weight=1)
        popup.columnconfigure(0, weight=1)

        query_var = tk.StringVar(value=self.filter_var.get().strip())
        query_entry = ttk.Entry(popup, textvariable=query_var, style="Filter.TEntry")
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header = tk.Label(
            popup,
            text="Matches  Title (matches = number of matching caption segments)",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=4,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        body = tk.Frame(popup, bg="#111111")
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        count_list = tk.Listbox(
            body,
            bg="#000000",
            fg="#f7d154",
            selectbackground="#161616",
            selectforeground="#f7d154",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            width=8,
            font=(FONT["STYLE"], FONT["SIZE"] - 2, "bold"),
            exportselection=False,
            takefocus=0,
        )
        title_list = tk.Listbox(
            body,
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
        count_list.grid(row=0, column=0, sticky="ns")
        title_list.grid(row=0, column=1, sticky="nsew")

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
        hint.grid(row=3, column=0, sticky="ew")

        def _set_selection(idx: int) -> None:
            if not self._search_results:
                return
            idx = max(0, min(idx, len(self._search_results) - 1))
            for lb in (count_list, title_list):
                lb.selection_clear(0, tk.END)
                lb.selection_set(idx)
                lb.activate(idx)
                lb.see(idx)

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            count_list.delete(0, tk.END)
            title_list.delete(0, tk.END)
            self._search_results = []
            if not query:
                return
            rows = self.ingester.search_videos(query, limit=200)
            self._search_results = [dict(r) for r in rows]
            for row in self._search_results:
                title = str(
                    row.get("title") \
                    or row.get("video_id") \
                    or "untitled"
                ).replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                count_list.insert(tk.END, f"{count:>4}")
                title_list.insert(tk.END, title)
            if self._search_results:
                _set_selection(0)

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
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
                video_id =video_id,
                transcript_json = transcript_path,
                video_path = video_path,
                audio_path = audio_path,
                start_sec = start_sec,
                filter_text = query,
            )
            popup.destroy()
            self._search_popup = None
            self.filter_entry.focus_set()
            return "break"

        def move_sel(delta: int) -> str:
            sel = title_list.curselection()
            cur = int(sel[0]) if sel else 0
            _set_selection(cur + delta)
            return "break"

        query_var.trace_add("write", refresh_results)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list, count_list],
            on_enter=lambda: open_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        title_list.bind("<Double-Button-1>", open_selected)
        count_list.bind("<Button-1>", lambda _e: "break")
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        refresh_results()
        query_entry.focus_set()

    def _open_video_picker_popup(self) -> None:
        if self._video_picker_popup and \
            self._video_picker_popup.winfo_exists():
            self._video_picker_popup.focus_force()
            return

        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Open Video", "900x620")
        self._video_picker_popup = popup
        self._register_popup("open_video", popup)
        popup.rowconfigure(2, weight=1)
        popup.columnconfigure(0, weight=1)

        query_var = tk.StringVar(value="")
        query_entry = ttk.Entry(popup, textvariable=query_var, style="Filter.TEntry")
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header = tk.Label(
            popup,
            text="Matches  Title (matches = number of title matches)",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=4,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        body = tk.Frame(popup, bg="#111111")
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        count_list = tk.Listbox(
            body,
            bg="#000000",
            fg="#f7d154",
            selectbackground="#161616",
            selectforeground="#f7d154",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            width=8,
            font=(FONT["STYLE"], FONT["SIZE"] - 2, "bold"),
            exportselection=False,
            takefocus=0,
        )
        title_list = tk.Listbox(
            body,
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
        count_list.grid(row=0, column=0, sticky="ns")
        title_list.grid(row=0, column=1, sticky="nsew")

        hint = tk.Label(
            popup,
            text="Type title filter, Up/Down select, Enter open, Delete remove video+transcript, Esc close",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        hint.grid(row=3, column=0, sticky="ew")
        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#d2d2d2",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=4, column=0, sticky="ew")
        pending_delete: dict[str, str | None] = {"video_id": None}

        def _set_selection(idx: int) -> None:
            if not self._video_picker_results:
                return
            idx = max(0, min(idx, len(self._video_picker_results) - 1))
            for lb in (count_list, title_list):
                lb.selection_clear(0, tk.END)
                lb.selection_set(idx)
                lb.activate(idx)
                lb.see(idx)

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            count_list.delete(0, tk.END)
            title_list.delete(0, tk.END)
            self._video_picker_results = []
            rows = self.ingester.search_video_titles(query, limit=300)
            self._video_picker_results = [dict(r) for r in rows]
            for row in self._video_picker_results:
                title = str(
                    row.get("title") \
                    or row.get("video_id") \
                    or "untitled"
                ).replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                count_list.insert(tk.END, f"{count:>4}")
                title_list.insert(tk.END, title)
            if self._video_picker_results:
                _set_selection(0)
            status_var.set(f"{len(self._video_picker_results)} rows")

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
            if not sel:
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._video_picker_results):
                return "break"
            row = self._video_picker_results[idx]
            video_id = str(row.get("video_id") or "")
            transcript_raw = str(row.get("transcript_json_path") or "").strip()
            transcript_path = Path(transcript_raw) if transcript_raw else None
            preferred = Path(str(row.get("local_video_path") or "")) if row.get("local_video_path") else None
            if transcript_path is not None and not transcript_path.exists():
                transcript_path = None
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
            self._load_session(
                video_id=video_id,
                transcript_json=transcript_path,
                video_path=video_path,
                audio_path=audio_path,
                start_sec=0.0,
                filter_text="",
            )
            popup.destroy()
            self._video_picker_popup = None
            self.filter_entry.focus_set()
            return "break"

        def move_sel(delta: int) -> str:
            sel = title_list.curselection()
            cur = int(sel[0]) if sel else 0
            _set_selection(cur + delta)
            return "break"

        def delete_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = title_list.curselection()
            if not sel:
                status_var.set("No video selected")
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(self._video_picker_results):
                status_var.set("Invalid selection")
                return "break"
            row = self._video_picker_results[idx]
            video_id = str(row.get("video_id") or "")
            if not video_id:
                status_var.set("Selection has no video id")
                return "break"
            if pending_delete.get("video_id") != video_id:
                pending_delete["video_id"] = video_id
                status_var.set(f"Press Delete again to remove {video_id}")
                return "break"
            pending_delete["video_id"] = None
            try:
                summary = self.ingester.delete_video_and_assets(video_id)
                status_var.set(
                    f"Deleted {video_id}: files={summary.get('deleted_files', 0)} "
                    f"jobs={summary.get('jobs_deleted', 0)}"
                )
                if self.current_video_id == video_id:
                    self._clear_loaded_session("Deleted currently loaded video")
                refresh_results()
            except Exception as exc:
                status_var.set(f"Delete failed: {exc}")
            return "break"

        query_var.trace_add("write", refresh_results)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list, count_list],
            on_enter=lambda: open_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        title_list.bind("<Double-Button-1>", open_selected)
        title_list.bind("<Delete>", delete_selected)
        count_list.bind("<Button-1>", lambda _e: "break")
        popup.bind("<Delete>", delete_selected)
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        refresh_results()
        query_entry.focus_set()

    def _open_ingest_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Ingest", "900x460")
        self._ingest_popup = popup
        self._register_popup("ingest", popup)
        popup.rowconfigure(2, weight=1)
        popup.columnconfigure(0, weight=1)

        input_var = tk.StringVar()
        entry = ttk.Entry(popup, textvariable=input_var, style="Filter.TEntry")
        entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        hint = tk.Label(
            popup,
            text="Input + Enter on command: Ingest (URL[s]) | Browse (channel) | Subscribe (channel)",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        hint.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

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
        listbox.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        commands = ["Ingest", "Browse", "Subscribe"]
        for c in commands:
            listbox.insert(tk.END, c)
        listbox.selection_set(0)

        status = tk.StringVar(value="Ready")
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
        status_lbl.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))

        def _selected_command() -> str:
            sel = listbox.curselection()
            idx = int(sel[0]) if sel else 0
            return commands[max(0, min(idx, len(commands) - 1))]

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            cmd = _selected_command()
            token = input_var.get().strip()
            if cmd == "Ingest":
                if not token:
                    status.set("Provide one or more URLs separated by spaces")
                    return "break"
                urls = [u.strip() for u in token.split() if u.strip()]
                if not urls:
                    status.set("No URLs parsed")
                    return "break"
                try:
                    result = self.ingester.enqueue_with_dedupe(
                        urls,
                        allow_overwrite=False,
                        auto_transcribe=self._auto_transcribe_default(),
                    )
                    ids = list(result.get("queued_ids") or [])
                    status.set(f"Queued {len(ids)} jobs")
                    if ids:
                        self.status_var.set(f"Queued ingest job {ids[0]}")
                except Exception as exc:
                    status.set(f"Ingest failed: {exc}")
                return "break"

            if cmd == "Browse":
                if not token:
                    status.set("Provide channel URL/@handle/name")
                    return "break"
                self._channel_default_ref = token
                popup.destroy()
                self._open_channel_popup()
                self.status_var.set(f"Channel browse requested: {token}")
                return "break"

            if not token:
                status.set("Provide channel URL/@handle/name")
                return "break"
            try:
                sub = self.ingester.add_channel_subscription(token, seed_with_latest=True)
                status.set(f"Subscribed: {sub.get('channel_title')} ({sub.get('channel_key')})")
            except Exception as exc:
                status.set(f"Subscribe failed: {exc}")
            return "break"

        def move(delta: int) -> str:
            sel = listbox.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(commands) - 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(nxt)
            listbox.activate(nxt)
            listbox.see(nxt)
            return "break"

        self._bind_fzf_like_keys(
            popup,
            entry=entry,
            capture_widgets=[entry, listbox],
            on_enter=lambda: run_selected(),
            on_up=lambda: move(-1),
            on_down=lambda: move(1),
            on_home=lambda: move(-10_000),
            on_end=lambda: move(10_000),
            on_page_up=lambda: move(-10),
            on_page_down=lambda: move(10),
        )
        listbox.bind("<Return>", run_selected)
        listbox.bind("<Double-Button-1>", run_selected)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        entry.focus_set()

    def _open_skim_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Skim Settings", "520x190")
        popup.columnconfigure(0, weight=1)

        pre_var = tk.StringVar(value=str(self._skim_pre_ms))
        post_var = tk.StringVar(value=str(self._skim_post_ms))
        status_var = tk.StringVar(
            value=f"Current: {'ON' if self._skim_mode else 'OFF'} (pre={self._skim_pre_ms}ms, post={self._skim_post_ms}ms)"
        )

        pre_entry = ttk.Entry(popup, textvariable=pre_var, style="Filter.TEntry")
        pre_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        post_entry = ttk.Entry(popup, textvariable=post_var, style="Filter.TEntry")
        post_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        hint = tk.Label(
            popup,
            text="Line1=pre-buffer ms | Line2=post-buffer ms | Enter apply | Ctrl-J toggle skim",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            padx=8,
            pady=6,
        )
        hint.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 6))

        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#d2d2d2",
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))

        def apply_values(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                pre = max(0, int(pre_var.get().strip() or "0"))
                post = max(0, int(post_var.get().strip() or "0"))
            except ValueError:
                status_var.set("Pre/post values must be integers in milliseconds")
                return "break"
            self._skim_pre_ms = pre
            self._skim_post_ms = post
            status_var.set(
                f"Applied: {'ON' if self._skim_mode else 'OFF'} (pre={pre}ms, post={post}ms)"
            )
            return "break"

        popup.bind("<Return>", apply_values)
        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        pre_entry.focus_set()

    def _open_channel_popup(self) -> None:
        if self._channel_popup and self._channel_popup.winfo_exists():
            self._channel_popup.focus_force()
            return

        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Channel Browser", "980x680")
        self._channel_popup = popup
        self._register_popup("channel", popup)
        popup.rowconfigure(2, weight=1)
        popup.columnconfigure(0, weight=1)

        channel_ref = str(self._channel_default_ref or "").strip()
        if not channel_ref:
            self.status_var.set("No channel selected. Open Ctrl-N and choose Browse first.")
            popup.destroy()
            return

        filter_var = tk.StringVar(value="")
        filter_entry = ttk.Entry(popup, textvariable=filter_var, style="Filter.TEntry")
        filter_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        limit_var = tk.StringVar(value="30")
        limit_entry = ttk.Entry(popup, textvariable=limit_var, style="Filter.TEntry")
        limit_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        body = tk.Frame(popup, bg="#111111")
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        listbox = tk.Listbox(
            body,
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
        listbox.grid(row=0, column=0, sticky="nsew")

        preview_frame = tk.Frame(
            body,
            bg="#000000",
            highlightthickness=1,
            highlightbackground="#2b2b2b",
            bd=0,
        )
        preview_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        preview_frame.rowconfigure(0, weight=3)
        preview_frame.rowconfigure(1, weight=0)
        preview_frame.rowconfigure(2, weight=0)
        preview_frame.rowconfigure(3, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        preview_image_lbl = tk.Label(
            preview_frame,
            bg="#000000",
            fg="#8f8f8f",
            text="Preview loading...",
            anchor="center",
            justify="center",
            font=(FONT["STYLE"], FONT["SIZE"] - 2),
        )
        preview_image_lbl.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))
        preview_title_var = tk.StringVar(value="")
        preview_creator_var = tk.StringVar(value="")
        preview_tags_var = tk.StringVar(value="")
        preview_title_lbl = tk.Label(
            preview_frame,
            textvariable=preview_title_var,
            anchor="w",
            justify="left",
            bg="#000000",
            fg="#ffffff",
            font=(FONT["STYLE"], FONT["SIZE"] - 2, "bold"),
            wraplength=320,
        )
        preview_title_lbl.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        preview_creator_lbl = tk.Label(
            preview_frame,
            textvariable=preview_creator_var,
            anchor="w",
            justify="left",
            bg="#000000",
            fg="#b8b8b8",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wraplength=320,
        )
        preview_creator_lbl.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        preview_tags_lbl = tk.Label(
            preview_frame,
            textvariable=preview_tags_var,
            anchor="nw",
            justify="left",
            bg="#000000",
            fg="#7cdfff",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wraplength=320,
        )
        preview_tags_lbl.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        preview_photo: dict[str, Any] = {"image": None}

        status_var = tk.StringVar(
            value=(
                f"Channel: {channel_ref} | Type to filter videos | Enter queues selected | "
                "Ctrl-R reload | Ctrl-S subscribe"
            )
        )
        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=3, column=0, sticky="ew")

        all_rows: list[dict[str, Any]] = []
        filtered_positions: list[int] = []
        preview_seq: dict[str, int] = {"value": 0}

        def _set_preview_placeholders(title: str = "", creator: str = "", tags: str = "") -> None:
            preview_title_var.set(title)
            preview_creator_var.set(creator)
            preview_tags_var.set(tags)
            preview_photo["image"] = None
            preview_image_lbl.configure(image="", text="No preview")

        def _render_preview_row(row: dict[str, Any], preview: dict[str, Any], expected_seq: int) -> None:
            video_id = str(row.get("video_id") or "")
            if not popup.winfo_exists() or expected_seq != preview_seq["value"]:
                return
            if expected_seq != preview_seq["value"] or not popup.winfo_exists():
                return
            preview_title_var.set(str(preview.get("title") or "untitled"))
            preview_creator_var.set(f"creator: {preview.get('creator') or 'unknown'}")
            tags = list(preview.get("hashtags") or [])
            preview_tags_var.set(" ".join(str(t) for t in tags) if tags else "(no hashtags)")
            image_path = str(preview.get("image_path") or "").strip()
            if image_path and Image is not None and ImageTk is not None:
                try:
                    with Image.open(image_path) as img:
                        img = img.convert("RGB")
                        img.thumbnail((420, 236))
                        photo = ImageTk.PhotoImage(img)
                    preview_photo["image"] = photo
                    preview_image_lbl.configure(image=photo, text="")
                except Exception:
                    preview_photo["image"] = None
                    preview_image_lbl.configure(image="", text="Preview unavailable")
            elif image_path:
                try:
                    photo = tk.PhotoImage(file=image_path)
                    preview_photo["image"] = photo
                    preview_image_lbl.configure(image=photo, text="")
                except Exception:
                    preview_photo["image"] = None
                    preview_image_lbl.configure(image="", text="Preview unavailable")
            else:
                preview_photo["image"] = None
                preview_image_lbl.configure(image="", text="Preview unavailable")

        def _refresh_preview_async() -> None:
            sel = listbox.curselection()
            if not sel or not filtered_positions:
                _set_preview_placeholders()
                return
            shown_idx = int(sel[0])
            if shown_idx < 0 or shown_idx >= len(filtered_positions):
                _set_preview_placeholders()
                return
            row = dict(all_rows[filtered_positions[shown_idx]])
            video_id = str(row.get("video_id") or "").strip()
            preview_title_var.set(str(row.get("title") or video_id or "untitled"))
            preview_creator_var.set(f"creator: {row.get('uploader') or row.get('channel') or 'unknown'}")
            preview_tags_var.set("loading metadata...")
            preview_image_lbl.configure(image="", text="Loading preview...")
            preview_seq["value"] += 1
            seq = int(preview_seq["value"])

            def _work() -> None:
                try:
                    preview = self._get_browse_preview(row)
                except Exception as exc:
                    preview = {
                        "title": str(row.get("title") or video_id or "untitled"),
                        "creator": str(row.get("uploader") or row.get("channel") or "unknown"),
                        "hashtags": [],
                        "image_path": "",
                        "error": str(exc),
                    }
                self.root.after(0, lambda: _render_preview_row(row, preview, seq))

            threading.Thread(target=_work, daemon=True, name="alog-browse-preview").start()

        def _apply_filter(*_args: object) -> str:
            query = filter_var.get().strip().lower()
            filtered_positions.clear()
            listbox.delete(0, tk.END)
            for idx, row in enumerate(all_rows):
                title = str(row.get("title") or row.get("video_id") or "untitled").replace("\n", " ").strip()
                creator = str(row.get("uploader") or row.get("channel") or "")
                hay = f"{title} {creator} {row.get('video_id') or ''}".lower()
                if query and query not in hay:
                    continue
                filtered_positions.append(idx)
                listbox.insert(tk.END, title)
            if filtered_positions:
                listbox.selection_set(0)
                listbox.activate(0)
                listbox.see(0)
                _refresh_preview_async()
            else:
                _set_preview_placeholders()
            status_var.set(
                f"Channel: {channel_ref} | {len(filtered_positions)}/{len(all_rows)} videos shown | "
                "Enter queues selected"
            )
            return "break"

        def refresh_channel_videos(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                limit = max(1, min(100, int(limit_var.get().strip() or "30")))
            except ValueError:
                status_var.set("Limit must be an integer between 1 and 100")
                return "break"
            try:
                data = self.ingester.list_channel_videos(channel_ref, limit=limit)
                all_rows.clear()
                all_rows.extend(dict(row) for row in (data.get("entries") or []))
                self._channel_results = list(all_rows)
                _apply_filter()
                def _prefetch_all(rows_snapshot: list[dict[str, Any]]) -> None:
                    for r in rows_snapshot:
                        if not popup.winfo_exists():
                            return
                        try:
                            self._get_browse_preview(dict(r))
                        except Exception:
                            continue
                threading.Thread(
                    target=lambda rows_snapshot=list(all_rows): _prefetch_all(rows_snapshot),
                    daemon=True,
                    name="alog-browse-prefetch",
                ).start()
                if all_rows:
                    listbox.focus_set()
            except Exception as exc:
                status_var.set(f"Failed to list channel videos: {exc}")
            return "break"

        def enqueue_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                status_var.set("No video selected")
                return "break"
            shown_idx = int(sel[0])
            if shown_idx < 0 or shown_idx >= len(filtered_positions):
                status_var.set("Invalid selection")
                return "break"
            idx = filtered_positions[shown_idx]
            if idx < 0 or idx >= len(all_rows):
                status_var.set("Invalid selection")
                return "break"
            row = all_rows[idx]
            url = str(row.get("url") or "").strip()
            if not url:
                status_var.set("Selected row has no URL")
                return "break"
            try:
                result = self.ingester.enqueue_with_dedupe(
                    [url],
                    allow_overwrite=False,
                    auto_transcribe=self._auto_transcribe_default(),
                )
                ids = list(result.get("queued_ids") or [])
                if not ids:
                    status_var.set("Not queued (already exists)")
                    return "break"
                status_var.set(f"Queued job_id={ids[0]}")
                self.status_var.set(f"Queued ingest job {ids[0]}")
            except Exception as exc:
                status_var.set(f"Queue failed: {exc}")
            return "break"

        def subscribe_channel(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                sub = self.ingester.add_channel_subscription(channel_ref, seed_with_latest=True)
                status_var.set(
                    f"Subscribed: {sub.get('channel_title')} ({sub.get('channel_key')})"
                )
            except Exception as exc:
                status_var.set(f"Subscribe failed: {exc}")
            return "break"

        def move_sel(delta: int) -> str:
            if not filtered_positions:
                return "break"
            sel = listbox.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(filtered_positions) - 1))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(nxt)
            listbox.activate(nxt)
            listbox.see(nxt)
            _refresh_preview_async()
            return "break"

        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.bind("<Control-r>", refresh_channel_videos)
        popup.bind("<Control-s>", subscribe_channel)
        self._bind_fzf_like_keys(
            popup,
            entry=filter_entry,
            capture_widgets=[filter_entry, listbox],
            on_enter=lambda: enqueue_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        listbox.bind("<Return>", enqueue_selected)
        listbox.bind("<Double-Button-1>", enqueue_selected)
        listbox.bind("<<ListboxSelect>>", lambda _e: _refresh_preview_async())
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        filter_var.trace_add("write", _apply_filter)
        refresh_channel_videos()
        filter_entry.focus_set()

    def _open_subscriptions_popup(self) -> None:
        if self._subscriptions_popup and self._subscriptions_popup.winfo_exists():
            self._subscriptions_popup.focus_force()
            return
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Subscriptions", "920x620")
        self._subscriptions_popup = popup
        self._register_popup("subscriptions", popup)
        popup.rowconfigure(0, weight=1)
        popup.columnconfigure(0, weight=1)

        listbox = tk.Listbox(
            popup,
            bg="#000000",
            fg="#ffffff",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            exportselection=False,
        )
        listbox.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))

        status_var = tk.StringVar(value="R refresh | Delete remove selected | P poll now")
        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=1, column=0, sticky="ew")
        rows_cache: list[dict[str, Any]] = []

        def refresh_rows(_event: tk.Event[tk.Misc] | None = None) -> str:
            nonlocal rows_cache
            try:
                rows = self.ingester.list_channel_subscriptions(active_only=False)
                rows_cache = [dict(r) for r in rows]
                listbox.delete(0, tk.END)
                for row in rows_cache:
                    title = str(row.get("channel_title") or row.get("channel_key") or "")
                    key = str(row.get("channel_key") or "")
                    last_seen = str(row.get("last_seen_video_id") or "-")
                    mode_raw = row.get("auto_transcribe")
                    mode = "default" if mode_raw is None else ("on" if int(mode_raw) == 1 else "off")
                    listbox.insert(tk.END, f"{title} | {key} | last_seen={last_seen} | transcribe={mode}")
                status_var.set(f"{len(rows_cache)} subscriptions loaded")
                if rows_cache:
                    listbox.selection_set(0)
            except Exception as exc:
                status_var.set(f"Failed to load subscriptions: {exc}")
            return "break"

        def remove_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = listbox.curselection()
            if not sel:
                status_var.set("No subscription selected")
                return "break"
            idx = int(sel[0])
            if idx < 0 or idx >= len(rows_cache):
                status_var.set("Invalid selection")
                return "break"
            channel_key = str(rows_cache[idx].get("channel_key") or "")
            if not channel_key:
                status_var.set("Selected row has no channel key")
                return "break"
            try:
                removed = self.ingester.remove_channel_subscription(channel_key)
                status_var.set(f"Removed={removed} channel_key={channel_key}")
                refresh_rows()
            except Exception as exc:
                status_var.set(f"Remove failed: {exc}")
            return "break"

        def poll_now(_event: tk.Event[tk.Misc] | None = None) -> str:
            try:
                summary = self.ingester.poll_subscriptions_once()
                status_var.set(
                    f"Poll complete: scanned={summary.get('scanned', 0)} queued={summary.get('queued', 0)}"
                )
            except Exception as exc:
                status_var.set(f"Poll failed: {exc}")
            return "break"

        popup.bind("<Escape>", lambda _e: (popup.destroy(), self.filter_entry.focus_set()))
        popup.bind("<KeyPress-r>", refresh_rows)
        popup.bind("<Delete>", remove_selected)
        popup.bind("<KeyPress-p>", poll_now)
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), self.filter_entry.focus_set()))
        refresh_rows()

    def _restart_workers(self, target: int, downloaders: int | None = None, transcribers: int | None = None) -> None:
        target = max(0, int(target))
        if downloaders is None:
            downloaders = self._downloader_target_count
        if transcribers is None:
            transcribers = self._transcriber_target_count
        downloaders = max(0, int(downloaders))
        transcribers = max(0, int(transcribers))
        target = max(target, downloaders, transcribers)
        self.ingester.stop_background_workers()
        if target > 0:
            self.ingester.start_background_workers(
                max(target, downloaders + transcribers),
                downloader_count=downloaders,
                transcriber_count=transcribers,
            )
        self._worker_target_count = target
        self._downloader_target_count = downloaders
        self._transcriber_target_count = transcribers
        self._workers_paused = False
        self._workers_eta_next_recalc_at = 0.0

    def _run_worker_command(self, command: str) -> str:
        label = command.strip().lower()
        if label == "add downloader":
            self._restart_workers(self._worker_target_count, self._downloader_target_count + 1, self._transcriber_target_count)
            return f"Downloaders: {self._downloader_target_count}"
        if label == "remove downloader":
            self._restart_workers(self._worker_target_count, max(0, self._downloader_target_count - 1), self._transcriber_target_count)
            return f"Downloaders: {self._downloader_target_count}"
        if label == "add transcriber":
            self._restart_workers(self._worker_target_count, self._downloader_target_count, self._transcriber_target_count + 1)
            return f"Transcribers: {self._transcriber_target_count}"
        if label == "remove transcriber":
            self._restart_workers(self._worker_target_count, self._downloader_target_count, max(0, self._transcriber_target_count - 1))
            return f"Transcribers: {self._transcriber_target_count}"
        if label == "kill jobs":
            try:
                n = int(self.ingester.kill_active_jobs())
                return f"Killed active jobs: {n}"
            except Exception as exc:
                return f"Kill failed: {exc}"
        if label == "clear queue":
            try:
                n = int(self.ingester.clear_queue())
                return f"Cleared queued jobs: {n}"
            except Exception as exc:
                return f"Clear queue failed: {exc}"
        if label == "poll subscriptions":
            try:
                summary = self.ingester.poll_subscriptions_once()
                return f"Poll: scanned={summary.get('scanned', 0)} queued={summary.get('queued', 0)}"
            except Exception as exc:
                return f"Poll failed: {exc}"
        return "Unknown command"

    def _recalculate_workers_eta(self) -> None:
        now = time.monotonic()
        self._workers_eta_last_tick_at = now
        self._workers_eta_next_recalc_at = now + self._workers_eta_recalc_interval_sec
        if self._workers_paused:
            self._workers_eta_remaining_sec = None
            return
        try:
            snapshot = self.ingester.dashboard_snapshot()
            counts = snapshot.get("counts", {})
            queued = int(counts.get("queued", 0))
            downloading = int(counts.get("downloading", 0))
            transcribing = int(counts.get("transcribing", 0))
            pending = queued + downloading + transcribing
            if pending <= 0:
                self._workers_eta_remaining_sec = 0.0
                return
            avg = snapshot.get("median_duration_sec")
            if avg is None:
                avg = snapshot.get("avg_duration_sec")
            avg_sec = float(avg) if avg is not None else 300.0
            avg_sec = max(30.0, avg_sec)
            workers = max(1, int((self._downloader_target_count + self._transcriber_target_count) or self._worker_target_count or 1))
            self._workers_eta_remaining_sec = float(pending) * avg_sec / float(workers)
        except Exception:
            # Keep prior countdown if snapshot fails.
            if self._workers_eta_remaining_sec is None:
                self._workers_eta_remaining_sec = 0.0

    def _tick_workers_eta_countdown(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._workers_eta_last_tick_at)
        self._workers_eta_last_tick_at = now
        if self._workers_eta_remaining_sec is None:
            return
        self._workers_eta_remaining_sec = max(0.0, self._workers_eta_remaining_sec - elapsed)

    def _workers_eta_line(self) -> str:
        now = time.monotonic()
        if now >= self._workers_eta_next_recalc_at:
            self._recalculate_workers_eta()
        self._tick_workers_eta_countdown()
        recalc_in = max(0, int(self._workers_eta_next_recalc_at - time.monotonic()))
        if self._workers_paused:
            return f"eta_til_finish=paused  recalc_in={_fmt_hms(recalc_in)}"
        if self._workers_eta_remaining_sec is None:
            return f"eta_til_finish=unknown  recalc_in={_fmt_hms(recalc_in)}"
        return (
            f"eta_til_finish=~{_fmt_hms(self._workers_eta_remaining_sec)}  "
            f"recalc_in={_fmt_hms(recalc_in)}"
        )

    def _open_jobs_popup(self) -> None:
        popup = OverlayPanel(self.root)
        self._apply_popup_style(popup, "Workers", "1320x620")
        self._jobs_popup = popup
        self._register_popup("workers", popup)
        popup.rowconfigure(0, weight=1)
        popup.rowconfigure(1, weight=0)
        popup.columnconfigure(0, weight=1)

        visual = tk.Frame(popup, bg="#111111")
        visual.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))
        # Prioritize queue/finished readability over worker stack columns.
        for col, wt in enumerate((5, 2, 2, 5)):
            visual.columnconfigure(col, weight=wt)
        visual.grid_columnconfigure(0, minsize=300)
        visual.grid_columnconfigure(3, minsize=300)
        visual.rowconfigure(1, weight=1)

        def head(col: int, text: str) -> None:
            tk.Label(
                visual,
                text=text,
                anchor="w",
                bg="#0d0d0d",
                fg="#8f8f8f",
                font=(FONT["STYLE"], FONT["SIZE"] - 3),
                padx=8,
                pady=4,
            ).grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0), pady=(0, 4))

        head(0, "Queue")
        head(1, "Downloaders")
        head(2, "Transcribers")
        head(3, "Finished")

        queue_list = tk.Listbox(
            visual,
            bg="#000000",
            fg="#ffffff",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            exportselection=False,
        )
        queue_list.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        self._jobs_queue_list = queue_list

        dl_text = tk.Text(
            visual,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="none",
            padx=8,
            pady=8,
        )
        dl_text.grid(row=1, column=1, sticky="nsew", padx=(0, 6), pady=(0, 8))
        dl_text.configure(state="disabled")
        self._jobs_dl_text = dl_text

        tr_text = tk.Text(
            visual,
            bg="#000000",
            fg="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="none",
            padx=8,
            pady=8,
        )
        tr_text.grid(row=1, column=2, sticky="nsew", padx=(0, 6), pady=(0, 8))
        tr_text.configure(state="disabled")
        self._jobs_tr_text = tr_text

        done_list = tk.Listbox(
            visual,
            bg="#000000",
            fg="#d2d2d2",
            selectbackground="#161616",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            exportselection=False,
        )
        done_list.grid(row=1, column=3, sticky="nsew", pady=(0, 8))
        self._jobs_done_list = done_list

        cmd_list = tk.Listbox(
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
            height=6,
        )
        cmd_list.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self._workers_cmd_list = cmd_list
        commands = ["Add Downloader", "Remove Downloader", "Add Transcriber", "Remove Transcriber", "Kill Jobs", "Clear Queue", "Poll Subscriptions"]
        for cmd in commands:
            cmd_list.insert(tk.END, cmd)
        cmd_list.selection_set(0)

        status_var = tk.StringVar(value="Enter executes command | Up/Down move command cursor")
        status_lbl = tk.Label(
            popup,
            textvariable=status_var,
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=6,
        )
        status_lbl.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))

        agent_head = tk.Label(
            popup,
            text="Agent Activity",
            anchor="w",
            bg="#0d0d0d",
            fg="#8f8f8f",
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            padx=8,
            pady=4,
        )
        agent_head.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
        agent_text = tk.Text(
            popup,
            bg="#000000",
            fg="#d2d2d2",
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] - 3),
            wrap="word",
            height=8,
            padx=8,
            pady=8,
        )
        agent_text.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        agent_text.configure(state="disabled")
        self._jobs_agent_text = agent_text

        def run_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            sel = cmd_list.curselection()
            if not sel:
                return "break"
            label = str(cmd_list.get(sel[0]))
            status_var.set(self._run_worker_command(label))
            self._refresh_jobs_popup()
            return "break"

        def move(delta: int) -> str:
            sel = cmd_list.curselection()
            cur = int(sel[0]) if sel else 0
            nxt = max(0, min(cur + delta, len(commands) - 1))
            cmd_list.selection_clear(0, tk.END)
            cmd_list.selection_set(nxt)
            cmd_list.activate(nxt)
            cmd_list.see(nxt)
            return "break"

        popup.bind("<Escape>", lambda _e: self._close_jobs_popup())
        popup.bind("<Up>", lambda _e: move(-1))
        popup.bind("<Down>", lambda _e: move(1))
        popup.bind("<Return>", run_selected)
        cmd_list.bind("<Double-Button-1>", run_selected)
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
        self._workers_cmd_list = None
        self._jobs_queue_list = None
        self._jobs_done_list = None
        self._jobs_dl_text = None
        self._jobs_tr_text = None
        self._jobs_agent_text = None
        self.filter_entry.focus_set()

    def _refresh_jobs_popup(self) -> None:
        if not self._jobs_popup or not self._jobs_popup.winfo_exists():
            return
        try:
            snapshot = self.ingester.jobs_summary(limit=30)
            dash = self.ingester.dashboard_snapshot()
            counts = snapshot.get("counts", {})
            jobs = [dict(r) for r in (snapshot.get("jobs", []) or [])]
            active_jobs = [dict(r) for r in (dash.get("active_jobs", []) or [])]
            self._worker_anim_tick = (self._worker_anim_tick + 1) % 1000

            queued_jobs = [r for r in jobs if str(r.get("status")) in {"queued", "downloading", "transcribing"}]
            finished_jobs = [r for r in jobs if str(r.get("status")) in {"done", "failed"}]
            if self._jobs_queue_list and self._jobs_queue_list.winfo_exists():
                self._jobs_queue_list.delete(0, tk.END)
                for row in queued_jobs:
                    self._jobs_queue_list.insert(
                        tk.END,
                        f"{row.get('id')} {str(row.get('status')):<11} {str(row.get('video_id') or '-')}",
                    )
            if self._jobs_done_list and self._jobs_done_list.winfo_exists():
                self._jobs_done_list.delete(0, tk.END)
                for row in finished_jobs:
                    self._jobs_done_list.insert(
                        tk.END,
                        f"{row.get('id')} {str(row.get('status')):<6} {str(row.get('video_id') or '-')}",
                    )

            downloading_active = [r for r in active_jobs if str(r.get("status")) == "downloading"]
            transcribing_active = [r for r in active_jobs if str(r.get("status")) == "transcribing"]

            def _worker_lines(kind: str, count: int, active: list[dict[str, Any]]) -> list[str]:
                lines: list[str] = []
                blink = "█" if (self._worker_anim_tick % 2 == 0) else "."
                z_phase = (self._worker_anim_tick % 3) + 1
                for i in range(max(0, count)):
                    if i < len(active):
                        job = active[i]
                        elapsed = float(job.get("elapsed_sec") or 0.0)
                        target = 120.0 if kind == "downloading" else 300.0
                        fill = max(0, min(10, int((elapsed / max(1.0, target)) * 10.0)))
                        bar = "[" + ("█" * max(0, fill - 1)) + (blink if fill > 0 else ".") + ("." * max(0, 10 - fill)) + "]"
                        lines.append(f"⛑{i+1:02d} ({kind})")
                        lines.append(f"{bar} job={job.get('id')}")
                    else:
                        z = "z" * z_phase
                        lines.append(f"⛑{i+1:02d} (idle)")
                        lines.append(f"[{z:<10}]")
                    lines.append("")
                return lines or ["(none)"]

            dl_lines = [
                f"downloaders={self._downloader_target_count}",
                f"transcribers={self._transcriber_target_count}",
                self._workers_eta_line(),
                "",
                *_worker_lines("downloading", self._downloader_target_count, downloading_active),
            ]
            tr_lines = [
                f"queued={counts.get('queued', 0)} done={counts.get('done', 0)} failed={counts.get('failed', 0)}",
                "",
                *_worker_lines("transcribing", self._transcriber_target_count, transcribing_active),
            ]
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.tag_configure("idle", foreground="#39d5ff")
                self._jobs_dl_text.tag_configure("hat", foreground="#f7d154")
                for line in dl_lines:
                    tag = None
                    if line.startswith("[") and "z" in line:
                        tag = "idle"
                    elif line.startswith("⛑"):
                        tag = "hat"
                    self._jobs_dl_text.insert(tk.END, line + "\n", (() if tag is None else (tag,)))
                self._jobs_dl_text.configure(state="disabled")
            if self._jobs_tr_text and self._jobs_tr_text.winfo_exists():
                self._jobs_tr_text.configure(state="normal")
                self._jobs_tr_text.delete("1.0", tk.END)
                self._jobs_tr_text.tag_configure("idle", foreground="#39d5ff")
                self._jobs_tr_text.tag_configure("hat", foreground="#f7d154")
                for line in tr_lines:
                    tag = None
                    if line.startswith("[") and "z" in line:
                        tag = "idle"
                    elif line.startswith("⛑"):
                        tag = "hat"
                    self._jobs_tr_text.insert(tk.END, line + "\n", (() if tag is None else (tag,)))
                self._jobs_tr_text.configure(state="disabled")
            if self._jobs_agent_text and self._jobs_agent_text.winfo_exists():
                self._jobs_agent_text.configure(state="normal")
                self._jobs_agent_text.delete("1.0", tk.END)
                self._jobs_agent_text.tag_configure("act", foreground="#7fd7ff")
                self._jobs_agent_text.tag_configure("err", foreground="#ff8a8a")
                self._jobs_agent_text.tag_configure("summary", foreground="#f7d154")
                self._jobs_agent_text.tag_configure("info", foreground="#b0b0b0")
                for row in self._agent_activity[-80:]:
                    kind = str(row.get("kind") or "info")
                    msg = str(row.get("message") or "")
                    stamp = str(row.get("time") or "00:00:00")
                    self._jobs_agent_text.insert(
                        tk.END,
                        f"[{stamp}] {kind}: {msg}\n",
                        (kind if kind in {"act", "err", "summary", "info"} else "info",),
                    )
                self._jobs_agent_text.configure(state="disabled")
        except Exception as exc:
            if self._jobs_dl_text and self._jobs_dl_text.winfo_exists():
                self._jobs_dl_text.configure(state="normal")
                self._jobs_dl_text.delete("1.0", tk.END)
                self._jobs_dl_text.insert("1.0", f"Failed to load ingest jobs: {exc}")
                self._jobs_dl_text.configure(state="disabled")
            if self._jobs_agent_text and self._jobs_agent_text.winfo_exists():
                self._jobs_agent_text.configure(state="normal")
                self._jobs_agent_text.delete("1.0", tk.END)
                self._jobs_agent_text.insert("1.0", f"Agent pane refresh failed: {exc}")
                self._jobs_agent_text.configure(state="disabled")
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
        transcript_json: Path | None,
        video_path: Path,
        audio_path: Path | None,
        start_sec: float,
        filter_text: str,
    ) -> None:
        self.current_video_id = video_id
        self._load_fail_count = 0
        self._proxy_attempted = False
        self.transcript_json = transcript_json
        self.segments = self._load_segments(transcript_json) if transcript_json and transcript_json.exists() else []
        self._segment_starts = [seg.start_sec for seg in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        self._set_player_media(video_path, audio_path, start_sec=start_sec)
        self.filter_var.set(filter_text)
        if not filter_text:
            self._refresh_caption_view()
        if self.segments:
            self.status_var.set(f"Loaded video at {_fmt_hms(start_sec)}")
        else:
            self.status_var.set(
                f"Loaded video at {_fmt_hms(start_sec)} (transcript not ready yet)"
            )

    def _clear_loaded_session(self, message: str) -> None:
        self.current_video_id = None
        self.transcript_json = None
        self.video_path = None
        self.audio_path = None
        self.segments = []
        self._segment_starts = []
        self.filtered_indexes = []
        self.selected_filtered_pos = 0
        self._skim_mode = False
        self._refresh_caption_view()
        self.caption_now_var.set("")
        try:
            self.player.stop()
        except Exception:
            pass
        self.status_var.set(message)

    def close(self) -> None:
        self._close_jobs_popup()
        if self._search_popup and \
            self._search_popup.winfo_exists():
            self._search_popup.destroy()
        if self._video_picker_popup and \
            self._video_picker_popup.winfo_exists():
            self._video_picker_popup.destroy()
        if self._ingest_popup and \
            self._ingest_popup.winfo_exists():
            self._ingest_popup.destroy()
        if self._channel_popup and \
            self._channel_popup.winfo_exists():
            self._channel_popup.destroy()
        if self._subscriptions_popup and \
            self._subscriptions_popup.winfo_exists():
            self._subscriptions_popup.destroy()
        try:
            self.ingester.stop_background_workers()
            self.player.stop()
            if self._ollama_started_by_app and self._ollama_proc and self._ollama_proc.poll() is None:
                try:
                    self._ollama_proc.terminate()
                    self._ollama_proc.wait(timeout=2.0)
                except Exception:
                    try:
                        self._ollama_proc.kill()
                    except Exception:
                        pass
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
    video_path:      Path | None = None,
    *,
    audio_path:      Path | None = None,
    skim_seconds:    float = 5.0,
    start_sec:       float = 0.0,
    workers:         int = 0,
) -> None:
    app = TranscriptPlayer(
        transcript_json=transcript_json,
        video_path=video_path,
        audio_path=audio_path,
        skim_seconds=skim_seconds,
        start_sec=start_sec,
        workers=workers,
    )
    app.run()

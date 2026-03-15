from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

import vlc

from alog.config import IngesterConfig
from alog.service import IngesterService

from .constants import (
    DEFAULT_KEYBINDS,
    FONT,
    FONT_SIZE_OFFSETS,
    ICONS,
    LAYOUT,
    POPUP_SIZES,
    STATUS_HINTS,
    THEME,
    THEME_SETTING_IDS,
    build_default_gui_settings,
)
from .playback import PlaybackMixin
from .popups import PopupMixin
from .transcript import TranscriptMixin


class TranscriptPlayer(TranscriptMixin, PlaybackMixin, PopupMixin):
    def __init__(
        self,
        transcript_json: Path | None = None,
        video_path: Path | None = None,
        audio_path: Path | None = None,
        skim_seconds: float = 5.0,
        start_sec: float = 0.0,
        workers: int = 0,
    ) -> None:
        self.transcript_json = transcript_json
        self.video_path = video_path
        self.audio_path = audio_path
        self.skim_seconds = skim_seconds
        self.start_sec = max(0.0, float(start_sec))
        self.workers = max(0, int(workers))

        self.segments = (
            self._load_segments(transcript_json)
            if transcript_json
            else []
        )
        self._segment_starts = [segment.start_sec for segment in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0

        self._search_popup = None
        self._command_popup = None
        self._video_picker_popup = None
        self._queue_picker_popup = None
        self._ingest_popup = None
        self._ai_popup = None
        self._settings_popup = None
        self._goto_popup = None
        self._channel_popup = None
        self._subscriptions_popup = None
        self._jobs_popup = None
        self._jobs_text = None
        self._workers_cmd_list = None
        self._jobs_queue_list = None
        self._jobs_done_list = None
        self._jobs_dl_text = None
        self._jobs_tr_text = None
        self._jobs_agent_text = None
        self._jobs_after_id = None
        self._agent_activity: list[dict[str, str]] = []
        self._search_results: list[dict[str, Any]] = []
        self._video_picker_results: list[dict[str, Any]] = []
        self._channel_results: list[dict[str, Any]] = []
        self._channel_default_ref = ""
        self._browse_preview_cache: dict[str, dict[str, Any]] = {}
        self._browse_thumb_dir: Path | None = None
        self._video_thumb_dir: Path | None = None
        self._browse_temp_files: set[Path] = set()
        self._split_initialized = False
        self._transcript_hidden = False
        self._details_hidden = False
        self._split_x_before_hide: int | None = None
        self._active_popup_name: str | None = None
        self.current_video_id: str | None = None
        self._load_fail_count = 0
        self._startup_poll_count = 0
        self._proxy_attempted = False
        self._skim_mode = False
        self._skim_pre_ms = 300
        self._skim_post_ms = 500
        self._skim_cursor = 0
        self._skim_last_seek_at = 0.0
        self._worker_target_count = self.workers
        self._downloader_target_count = (
            self.workers
            if self.workers > 0
            else 1
        )
        self._transcriber_target_count = (
            self.workers
            if self.workers > 0
            else 1
        )
        self._summarizer_target_count = (
            self.workers
            if self.workers > 0
            else 1
        )
        self._workers_paused = False
        self._popup_paused_player = False
        self._popup_video_hidden = False
        self._workers_eta_remaining_sec: float | None = None
        self._workers_eta_next_recalc_at = 0.0
        self._workers_eta_last_tick_at = time.monotonic()
        self._workers_eta_recalc_interval_sec = 60.0
        self._worker_anim_tick = 0
        self._bound_shortcut_sequences: list[str] = []
        self._ollama_proc: subprocess.Popen[str] | None = None
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
        self._browse_thumb_dir = (
            self.ingester_config.db_path.parent / "browse_previews"
        )
        self._video_thumb_dir = (
            self.ingester_config.db_path.parent / "video_previews"
        )
        try:
            self._browse_thumb_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            self._video_thumb_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._gui_settings_path = (
            self.ingester_config.db_path.parent / "gui_settings.json"
        )
        self._ai_settings = self._load_gui_settings()
        if self.workers <= 0:
            self.workers = max(0, int(
                self._ai_settings.get("default_worker_count")
                or 0))
            self._worker_target_count = self.workers
        self._downloader_target_count = max(0, int(
            self._ai_settings.get("default_downloaders")
            or self._worker_target_count
            or 1))
        self._transcriber_target_count = max(0, int(
            self._ai_settings.get("default_transcribers")
            or self._worker_target_count
            or 1))
        self._summarizer_target_count = max(0, int(
            self._ai_settings.get("default_summarizers")
            or self._worker_target_count
            or 1))
        if self._worker_target_count <= 0:
            self._worker_target_count = max(
                self._downloader_target_count,
                self._transcriber_target_count,
                self._summarizer_target_count)
        self.ingester.set_runtime_options(
            auto_transcribe_default=bool(
                self._ai_settings.get("auto_transcribe_default", True)),
            subscription_db_max_videos=max(0, int(
                self._ai_settings.get("subscription_db_max_videos")
                or 0)),
            job_retry_limit=max(0, int(
                self._ai_settings.get("job_retry_limit")
                or 0)),
            ai_runtime_settings=self._build_ai_runtime_settings(),
        )
        if self.workers > 0:
            self.ingester.start_background_workers(
                max(self.workers,
                    self._downloader_target_count
                    + self._transcriber_target_count
                    + self._summarizer_target_count),
                downloader_count=self._downloader_target_count,
                transcriber_count=self._transcriber_target_count,
                summarizer_count=self._summarizer_target_count,
            )
        if str(self._ai_settings.get("ai_provider")
               or "ollama").lower() == "ollama":
            self._start_ollama_bootstrap(background=True)

        self.root = tk.Tk()
        self.root.title("Alogger Player")
        self.root.geometry(POPUP_SIZES["ROOT"])
        self.root.configure(bg=THEME["APP_BG"])
        self.root.option_add("*insertOffTime", 0)
        self.root.option_add("*Entry.insertOffTime", 0)
        self.root.option_add("*Text.insertOffTime", 0)
        self.root.option_add("*TEntry.insertOffTime", 0)

        self._text_font = tkfont.Font(
            family=FONT["STYLE"],
            size=FONT["SIZE"])
        self._text_font_bold = tkfont.Font(
            family=FONT["STYLE"],
            size=FONT["SIZE"],
            weight="bold")
        self._timestamp_prefix = "[00:00:00] "
        self._wrap_indent_px = self._text_font.measure(self._timestamp_prefix)
        self._progress_bar_width = LAYOUT["PROGRESS_BAR_WIDTH"]
        self._clock_prefix_len = len("[00:00:00] ")
        self._clock_last_length_sec = 0.0
        self._font_size_delta = 0
        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._window_origin_x = 0
        self._window_origin_y = 0
        self._shell_stacked = False
        self._shell_layout_mode = "horizontal"
        self._launch_buttons: list[tk.Widget] = []
        self._root_layout_after_id: str | None = None

        self._setup_styles()
        self._build_layout()
        self._apply_theme()
        self._apply_font_scale_tree(self.root, reset_base=True)
        self._bind_keys()
        self._build_vlc()
        self._refresh_caption_view()
        if not self.video_path:
            self.status_var.set(self._status_hint("startup"))
        self._tick_ui()

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Filter.TEntry",
            fieldbackground=THEME["SURFACE_ALT_BG"],
            foreground="#f0f0f0"
        )
        style.configure(
            "Terminal.TNotebook",
            background=THEME["APP_BG"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0)
        )
        style.configure(
            "Terminal.TNotebook.Tab",
            background=THEME["SURFACE_BG"],
            foreground=THEME["FG_MUTED"],
            borderwidth=0,
            padding=(8, 4),
        )
        style.configure(
            "Vertical.TScrollbar",
            background=THEME["SURFACE_BG"],
            troughcolor=THEME["PANEL_BG"],
            arrowcolor=THEME["FG_MUTED"],
            bordercolor=THEME["BORDER"],
            lightcolor=THEME["SURFACE_BG"],
            darkcolor=THEME["SURFACE_BG"],
            gripcount=0,
        )
        style.map(
            "Vertical.TScrollbar",
            background=[
                ("active", THEME["SELECT_BG"]),
                ("pressed", THEME["FG_ACCENT"]),
            ],
            arrowcolor=[
                ("active", THEME["FG_SOFT"]),
                ("pressed", THEME["FG"]),
            ],
        )
        style.map(
            "Terminal.TNotebook.Tab",
            background=[
                ("selected", THEME["PANEL_BG"]),
                ("active", THEME["SELECT_BG"])
            ],
            foreground=[
                ("selected", THEME["FG"]),
                ("active", THEME["FG_SOFT"])
            ],
        )

    def _theme_color(self, key: str) -> str:
        setting_id = THEME_SETTING_IDS.get(key)
        if setting_id:
            return str(self._ai_settings.get(setting_id) or THEME[key])
        return str(THEME.get(key, ""))

    def _ui_font(
        self,
        delta: int = 0,
        *,
        bold: bool = False,
    ) -> tuple[str, int] | tuple[str, int, str]:
        family = str(
            getattr(self, "_ui_font_family", None)
            or self._ai_settings.get("font_family")
            or FONT["STYLE"]
        )
        size = max(
            8,
            int(getattr(self, "_ui_font_size", FONT["SIZE"])) + int(delta),
        )
        if bold:
            return (family, size, "bold")
        return (family, size)

    def _shortcut_label(self, action: str) -> str:
        keybinds = self._ai_settings.get("keybinds")
        if not isinstance(keybinds, dict):
            keybinds = DEFAULT_KEYBINDS
        token = str(
            keybinds.get(action, DEFAULT_KEYBINDS.get(action, ""))
        ).strip()
        return token or DEFAULT_KEYBINDS.get(action, "")

    def _status_hint(self, name: str, **values: str) -> str:
        template = STATUS_HINTS.get(name, "")
        context = {
            action: self._shortcut_label(action)
            for action in DEFAULT_KEYBINDS
        }
        context.update(values)
        try:
            return template.format(**context)
        except Exception:
            return template

    def _apply_theme(self) -> None:
        bg = self._theme_color("APP_BG")
        panel_bg = self._theme_color("PANEL_BG")
        surface_bg = self._theme_color("SURFACE_BG")
        surface_alt_bg = self._theme_color("SURFACE_ALT_BG")
        fg = self._theme_color("FG")
        muted = self._theme_color("FG_MUTED")
        accent = self._theme_color("FG_ACCENT")
        soft = self._theme_color("FG_SOFT")
        border = self._theme_color("BORDER")
        try:
            family = str(self._ai_settings.get("font_family") or FONT["STYLE"])
            base_size = max(8, int(
                self._ai_settings.get("font_size") or FONT["SIZE"]))
        except Exception:
            family = FONT["STYLE"]
            base_size = FONT["SIZE"]
        size = max(8, min(64, base_size + int(self._font_size_delta)))
        self._ui_font_family = family
        self._ui_font_size = size
        self._text_font.configure(family=family, size=size)
        self._text_font_bold.configure(family=family, size=size)
        self.root.configure(bg=bg)
        if hasattr(self, "title_bar"):
            self.title_bar.configure(bg=surface_bg)
        if hasattr(self, "launch_bar"):
            self.launch_bar.configure(bg=surface_bg)
        self.left_panel.configure(bg=panel_bg)
        self.right_panel.configure(bg=bg)
        self.video_panel.configure(bg=panel_bg)
        self.clock_view.configure(
            bg=surface_bg,
            fg=fg,
            insertbackground=fg,
            font=(family, max(8, size + FONT_SIZE_OFFSETS["BODY"]), "bold")
        )
        self.caption_now_box.configure(
            bg=surface_bg,
            fg=fg,
            font=(family, size + FONT_SIZE_OFFSETS["BODY"])
        )
        self.root_status_box.configure(
            bg=surface_bg,
            fg=fg,
            font=(family, size)
        )
        self.caption_view.configure(
            bg=panel_bg,
            fg=fg,
            insertbackground=fg,
            font=self._text_font
        )
        self.caption_view.tag_configure(
            "ts",
            foreground=muted
        )
        self.caption_view.tag_configure(
            "txt",
            foreground=fg
        )
        self.caption_view.tag_configure(
            "match",
            foreground=accent
        )
        self.caption_view.tag_configure(
            "selected",
            background=self._theme_color("SELECTED_ROW_BG")
        )
        self.caption_view.tag_configure(
            "selected_txt",
            font=self._text_font_bold
        )
        style = ttk.Style(self.root)
        style.configure(
            "Filter.TEntry",
            fieldbackground=surface_alt_bg,
            foreground=fg,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
            insertcolor=fg,
            font=(family, max(8, size - 1))
        )
        style.configure("Terminal.TNotebook", background=bg)
        style.configure(
            "Terminal.TNotebook.Tab",
            background=surface_bg,
            foreground=muted,
            font=(family, max(8, size + FONT_SIZE_OFFSETS["SMALL"]))
        )
        style.map(
            "Terminal.TNotebook.Tab",
            background=[
                ("selected", panel_bg),
                ("active", self._theme_color("SELECT_BG"))
            ],
            foreground=[
                ("selected", fg),
                ("active", soft)
            ],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=surface_bg,
            troughcolor=panel_bg,
            arrowcolor=muted,
            bordercolor=border,
            lightcolor=surface_bg,
            darkcolor=surface_bg,
            gripcount=0,
        )
        style.map(
            "Vertical.TScrollbar",
            background=[
                ("active", self._theme_color("SELECT_BG")),
                ("pressed", accent),
            ],
            arrowcolor=[
                ("active", soft),
                ("pressed", fg),
            ],
        )
        if hasattr(self, "title_label"):
            self.title_label.configure(
                bg=surface_bg,
                fg=soft,
                font=(family, max(8, size + FONT_SIZE_OFFSETS["BODY"]), "bold"),
            )
        if hasattr(self, "title_close"):
            self.title_close.configure(
                bg=surface_bg,
                fg=muted,
                font=(family, max(8, size + FONT_SIZE_OFFSETS["BODY"]), "bold"),
            )
        if hasattr(self, "video_pause_overlay"):
            self.video_pause_overlay.configure(
                bg=self._theme_color("VIDEO_OVERLAY_BG"),
                fg=self._theme_color("VIDEO_OVERLAY_FG"),
                font=(family, max(18, size + 18), "bold"),
            )
        if hasattr(self, "filter_entry") and hasattr(self.filter_entry, "configure_colors"):
            try:
                self.filter_entry.configure_colors(
                    bg=surface_alt_bg,
                    fg=fg,
                    accent_fg=accent,
                    border=border,
                    font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
                )
            except Exception:
                pass
        for button in getattr(self, "_launch_buttons", []):
            try:
                button.configure(
                    bg=surface_bg,
                    fg=soft,
                    font=(family, max(8, size + FONT_SIZE_OFFSETS["SMALL"])),
                )
            except Exception:
                pass
        self._apply_root_chrome_settings()
        self._refresh_clock_now()

    def _apply_font_scale_tree(
        self,
        root_widget: tk.Misc | None = None,
        *,
        reset_base: bool = False
    ) -> None:
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
                        font_obj = tkfont.Font(root=self.root, font=widget.cget("font"))
                        actual = font_obj.actual()
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
                    desired_family = str(self._ai_settings.get("font_family") or FONT["STYLE"])
                    parts: list[Any] = [desired_family, new_size]
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
        settings = build_default_gui_settings()
        if not self._gui_settings_path:
            return settings
        try:
            if self._gui_settings_path.exists():
                payload = json.loads(self._gui_settings_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    settings.update(payload)
                    keybinds = payload.get("keybinds")
                    if isinstance(keybinds, dict):
                        merged = dict(DEFAULT_KEYBINDS)
                        for key, value in keybinds.items():
                            if isinstance(key, str) and isinstance(value, str):
                                merged[key] = value
                        settings["keybinds"] = merged
        except Exception:
            pass
        return settings

    def _save_gui_settings(self) -> None:
        try:
            self._gui_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._gui_settings_path.write_text(
                json.dumps(
                    self._ai_settings,
                    indent=2,
                    ensure_ascii=False
                ), encoding="utf-8")
        except Exception:
            pass

    def _ollama_base_url(self) -> str:
        return str(
            self._ai_settings.get("ollama_base_url")
            or "http://127.0.0.1:11434"
        ).rstrip("/")

    def _ollama_model(self) -> str:
        return str(
            self._ai_settings.get("ollama_model")
            or "llama3.2:3b"
        ).strip() or "llama3.2:3b"

    def _auto_transcribe_default(self) -> bool:
        return bool(self._ai_settings.get("auto_transcribe_default", True))

    def _build_ai_runtime_settings(self) -> dict[str, Any]:
        return {
            "ai_provider": str(self._ai_settings.get("ai_provider") or "ollama"),
            "ollama_model": str(self._ai_settings.get("ollama_model") or "llama3.2:3b"),
            "ollama_base_url": str(
                self._ai_settings.get("ollama_base_url")
                or "http://127.0.0.1:11434"
            ),
            "api_base_url": str(
                self._ai_settings.get("api_base_url")
                or "https://api.openai.com"
            ),
            "api_key_env": str(
                self._ai_settings.get("api_key_env")
                or "OPENAI_API_KEY"
            ),
            "api_model": str(self._ai_settings.get("api_model") or "gpt-4o-mini"),
            "auto_summary_default": bool(
                self._ai_settings.get("auto_summary_default", False)
            ),
            "summary_segment_limit": max(
                0,
                int(self._ai_settings.get("summary_segment_limit") or 0),
            ),
            "summary_instructions_path": str(
                self._ai_settings.get("summary_instructions_path") or ""
            ),
        }

    def _ollama_server_healthy(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self._ollama_base_url()}/api/tags",
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
            return True
        except Exception:
            return False

    def _ollama_model_exists(self, model: str) -> bool:
        try:
            req = urllib.request.Request(
                f"{self._ollama_base_url()}/api/tags",
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            models = payload.get("models") or []
            if not isinstance(models, list):
                return False
            aliases = (
                {model, f"{model}:latest"}
                if ":" not in model
                else {model}
            )
            for item in models:
                if (
                    isinstance(item, dict)
                    and str(item.get("name") or "").strip() in aliases
                ):
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
            "text": True
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            # type: ignore[attr-defined]
        self._ollama_proc = subprocess.Popen(["ollama", "serve"], **kwargs)
        self._ollama_started_by_app = True

    def _reset_ollama_bootstrap(self) -> None:
        self._ollama_bootstrap_done.clear()
        self._ollama_bootstrap_success = False
        self._ollama_bootstrap_error = None
        self._ollama_bootstrap_state = "idle"

    def _bootstrap_ollama(self) -> None:
        with self._ollama_bootstrap_lock:
            if (
                self._ollama_bootstrap_done.is_set()
                and self._ollama_bootstrap_success
            ):
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
                    proc = subprocess.run(["ollama", "pull", model], capture_output=True, text=True)
                    if proc.returncode != 0:
                        raise RuntimeError(f"failed to pull Ollama model '{model}': {proc.stderr.strip() or proc.stdout.strip()}")
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

    def _start_ollama_bootstrap(
        self,
        *,
        background: bool,
        force_reset: bool = False
    ) -> None:
        if str(
            self._ai_settings.get("ai_provider")
            or "ollama"
        ).lower() != "ollama":
            return
        if force_reset:
            self._reset_ollama_bootstrap()
        if (
            self._ollama_bootstrap_done.is_set()
            and self._ollama_bootstrap_success
        ):
            return
        if background:
            if (
                self._ollama_bootstrap_thread
                and self._ollama_bootstrap_thread.is_alive()
            ):
                return
            self._ollama_bootstrap_thread = threading.Thread(
                target=self._bootstrap_ollama,
                daemon=True,
                name="alog-ollama-bootstrap",
            )
            self._ollama_bootstrap_thread.start()
            return
        self._bootstrap_ollama()

    def _ensure_ollama_ready(
        self,
        *,
        block: bool = True
    ) -> None:
        if (str(self._ai_settings.get("ai_provider")
                or "ollama").lower() != "ollama"):
            return
        self._start_ollama_bootstrap(background=not block)
        if block:
            self._ollama_bootstrap_done.wait(timeout=600.0)
            if not self._ollama_bootstrap_done.is_set():
                raise RuntimeError("Timed out preparing Ollama")
            if not self._ollama_bootstrap_success:
                raise RuntimeError(
                    self._ollama_bootstrap_error
                    or "Ollama initialization failed"
                )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=0)
        self.root.rowconfigure(2, weight=1)
        self.root.rowconfigure(3, weight=0)
        self.title_bar = tk.Frame(
            self.root,
            bg=THEME["SURFACE_BG"],
            height=LAYOUT["TITLEBAR_HEIGHT"],
            highlightthickness=0,
            bd=0,
        )
        self.title_bar.grid(row=0, column=0, sticky="ew")
        self.title_bar.grid_propagate(False)
        self.title_bar.columnconfigure(0, weight=1)
        self.title_bar.columnconfigure(1, weight=0)
        self.title_label = tk.Label(
            self.title_bar,
            text="Alogger Player",
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_SOFT"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"], "bold"),
            padx=LAYOUT["BAR_PAD_X"],
            pady=LAYOUT["BAR_PAD_Y"],
        )
        self.title_label.grid(row=0, column=0, sticky="ew")
        self.title_close = tk.Label(
            self.title_bar,
            text="x",
            anchor="center",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_MUTED"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"], "bold"),
            padx=LAYOUT["BAR_PAD_X"],
            pady=LAYOUT["BAR_PAD_Y"],
            cursor="hand2",
        )
        self.title_close.grid(row=0, column=1, sticky="e")
        self.launch_bar = tk.Frame(
            self.root,
            bg=THEME["SURFACE_BG"],
            height=LAYOUT["LAUNCHBAR_HEIGHT"],
            highlightthickness=0,
            bd=0,
        )
        self.launch_bar.grid(row=1, column=0, sticky="ew")
        self.launch_bar.grid_propagate(False)
        self.launch_bar.columnconfigure(99, weight=1)
        launchers: list[tuple[str, Callable[[], None]]] = [
            ("Cmd", lambda: self._toggle_popup("command", self._open_command_popup)),
            ("Ingest", lambda: self._toggle_popup("ingest", self._open_ingest_popup)),
            ("Workflows", lambda: self._toggle_popup("workers", self._open_jobs_popup)),
            ("Open", lambda: self._toggle_popup("open_video", self._open_video_picker_popup)),
            ("Find", lambda: self._toggle_popup("finder", self._open_search_popup)),
            ("AI", lambda: self._toggle_popup("ai", self._open_ai_popup)),
            ("Goto", self._open_goto_popup),
            ("Settings", lambda: self._toggle_popup("settings", self._open_settings_popup)),
            ("Browse", lambda: self._toggle_popup("channel", self._open_channel_popup)),
            ("Subs", lambda: self._toggle_popup("subscriptions", self._open_subscriptions_popup)),
        ]
        self._launch_buttons = []
        for idx, (label, command) in enumerate(launchers):
            button = tk.Label(
                self.launch_bar,
                text=label,
                anchor="center",
                bg=THEME["SURFACE_BG"],
                fg=THEME["FG_SOFT"],
                font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
                padx=LAYOUT["BAR_PAD_X"],
                pady=LAYOUT["BAR_PAD_Y"],
                cursor="hand2",
            )
            button.grid(row=0, column=idx, sticky="w", padx=(0 if idx == 0 else 2, 0))
            button.bind("<Button-1>", lambda _e, fn=command: fn(), add="+")
            self._launch_buttons.append(button)
        self.shell = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=LAYOUT["SASH_WIDTH"]
        )
        self.shell.grid(row=2, column=0, sticky="nsew")
        self.left_panel = tk.Frame(self.shell, bg=THEME["PANEL_BG"])
        self.right_panel = tk.Frame(self.shell, bg=THEME["APP_BG"])
        self.shell.add(self.left_panel, minsize=1000)
        self.shell.add(self.right_panel, minsize=200)
        self.shell.bind("<Configure>", self._on_shell_configure)
        self.root.after(0, self._set_initial_split_ratio)
        self.left_panel.rowconfigure(0, weight=1)
        self.left_panel.columnconfigure(0, weight=1)
        self.video_panel = tk.Frame(self.left_panel, bg=THEME["PANEL_BG"], highlightthickness=0, bd=0)
        self.video_panel.grid(row=0, column=0, sticky="nsew")
        self.video_pause_overlay = tk.Label(
            self.left_panel,
            text=ICONS["PAUSE"],
            anchor="center",
            justify="center",
            bg=self._theme_color("VIDEO_OVERLAY_BG"),
            fg=self._theme_color("VIDEO_OVERLAY_FG"),
            font=self._ui_font(18, bold=True),
            padx=18,
            pady=8,
        )
        self.video_pause_overlay.place_forget()
        self.clock_view = tk.Text(
            self.left_panel,
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_ACCENT"],
            borderwidth=0,
            highlightthickness=0,
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"], "bold"),
            wrap="none",
            height=1,
            padx=10,
            pady=6,
            cursor="hand2",
            insertbackground=THEME["FG_ACCENT"],
        )
        self.clock_view.grid(row=2, column=0, sticky="ew")
        self.clock_view.configure(state="disabled")
        self.clock_view.bind("<Button-1>", self._on_click_clock_progress)
        self.caption_now_var = tk.StringVar(value="")
        self.caption_now_box = tk.Label(
            self.left_panel,
            textvariable=self.caption_now_var,
            anchor="w",
            justify="left",
            bg=THEME["PANEL_BG"],
            fg=THEME["FG"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"]),
            padx=10,
            pady=8,
            wraplength=400,
        )
        self.caption_now_box.grid(row=1, column=0, sticky="ew")
        self.left_panel.bind("<Configure>", self._on_left_resize)
        self.status_var = tk.StringVar(value="Idle")
        self.root_status_box = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            bg=THEME["SURFACE_BG"],
            fg=THEME["FG_SOFT"],
            font=(FONT["STYLE"], FONT["SIZE"]),
            padx=10,
            pady=6,
        )
        self.root_status_box.grid(row=3, column=0, sticky="ew")
        self.right_panel.rowconfigure(1, weight=1)
        self.right_panel.columnconfigure(0, weight=1)
        self.filter_var = tk.StringVar()
        self.filter_entry = self._create_query_entry(self.right_panel, self.filter_var)
        self.filter_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        self.caption_view = tk.Text(
            self.right_panel,
            bg=THEME["PANEL_BG"],
            fg=THEME["FG"],
            borderwidth=0,
            highlightthickness=0,
            font=self._text_font,
            wrap="word",
            padx=8,
            pady=8,
            insertbackground=THEME["FG"],
        )
        self.caption_view.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.caption_view.configure(state="disabled")
        self.caption_view.tag_configure("row", lmargin1=0, lmargin2=self._wrap_indent_px)
        self.caption_view.tag_configure("ts", foreground=THEME["FG_MUTED"])
        self.caption_view.tag_configure("txt", foreground=THEME["FG"])
        self.caption_view.tag_configure("match", foreground=THEME["FG_ACCENT"])
        self.caption_view.tag_configure("selected", background="#282828")
        self.caption_view.tag_configure("selected_txt", font=self._text_font_bold)
        self._row_ranges: list[tuple[str, str]] = []
        self._row_text_ranges: list[tuple[str, str]] = []
        self._bind_window_controls()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._apply_root_chrome_settings()

    def _bind_window_controls(self) -> None:
        for widget in (self.title_bar, self.title_label):
            widget.bind("<ButtonPress-1>", self._on_begin_window_drag, add="+")
            widget.bind("<B1-Motion>", self._on_window_drag, add="+")
            widget.bind("<Double-Button-1>", self._on_toggle_window_maximize, add="+")
        self.title_close.bind("<Button-1>", lambda _e: self.close(), add="+")

    def _apply_root_chrome_settings(self) -> None:
        show_root_top_bar = bool(self._ai_settings.get("show_root_top_bar", True))
        show_launch_bar = bool(self._ai_settings.get("show_launch_bar", True))
        if hasattr(self, "title_bar"):
            if show_root_top_bar:
                self.title_bar.grid()
            else:
                self.title_bar.grid_remove()
        if hasattr(self, "launch_bar"):
            if show_launch_bar:
                self.launch_bar.grid()
            else:
                self.launch_bar.grid_remove()
        self._schedule_root_layout_refresh()

    def _on_root_configure(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.root:
            return
        self._schedule_root_layout_refresh()

    def _schedule_root_layout_refresh(self) -> None:
        if self._root_layout_after_id is not None:
            try:
                self.root.after_cancel(self._root_layout_after_id)
            except Exception:
                pass
        self._root_layout_after_id = self.root.after(30, self._refresh_root_layout)

    def _refresh_root_layout(self) -> None:
        self._root_layout_after_id = None
        if not hasattr(self, "shell"):
            return
        try:
            self.root.update_idletasks()
            total_w = int(self.shell.winfo_width())
            total_h = int(self.shell.winfo_height())
        except Exception:
            return
        if total_w > 0 and total_h > 0:
            self._apply_shell_layout(total_w, total_h)
        self._position_video_overlay()

    def _position_video_overlay(self) -> None:
        if not hasattr(self, "video_pause_overlay") or not hasattr(self, "video_panel"):
            return
        try:
            panel_x = int(self.video_panel.winfo_x())
            panel_y = int(self.video_panel.winfo_y())
            panel_w = int(self.video_panel.winfo_width())
            panel_h = int(self.video_panel.winfo_height())
        except Exception:
            return
        if panel_w <= 0 or panel_h <= 0:
            self.video_pause_overlay.place_forget()
            return
        self.video_pause_overlay.place(
            x=panel_x + (panel_w // 2),
            y=panel_y + (panel_h // 2),
            anchor="center",
        )

    def _refresh_pause_overlay(self, state: vlc.State | None = None) -> None:
        if not hasattr(self, "video_pause_overlay"):
            return
        if not self.video_path:
            self.video_pause_overlay.place_forget()
            return
        try:
            current = state if state is not None else self.player.get_state()
        except Exception:
            current = None
        if current in {vlc.State.Paused, vlc.State.Stopped}:
            self._position_video_overlay()
            self.video_pause_overlay.lift()
            return
        self.video_pause_overlay.place_forget()

    def _on_begin_window_drag(self, event: tk.Event[tk.Misc]) -> str:
        self._drag_origin_x = int(getattr(event, "x_root", 0))
        self._drag_origin_y = int(getattr(event, "y_root", 0))
        self._window_origin_x = int(self.root.winfo_x())
        self._window_origin_y = int(self.root.winfo_y())
        return "break"

    def _on_window_drag(self, event: tk.Event[tk.Misc]) -> str:
        delta_x = int(getattr(event, "x_root", 0)) - self._drag_origin_x
        delta_y = int(getattr(event, "y_root", 0)) - self._drag_origin_y
        width = max(1, int(self.root.winfo_width()))
        height = max(1, int(self.root.winfo_height()))
        x = self._window_origin_x + delta_x
        y = self._window_origin_y + delta_y
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        return "break"

    def _on_toggle_window_maximize(self, _event: tk.Event[tk.Misc]) -> str:
        try:
            current = bool(self.root.wm_attributes("-zoomed"))
            self.root.wm_attributes("-zoomed", not current)
        except Exception:
            pass
        return "break"

    def _display_to_tk_sequence(self, value: str) -> str | None:
        token = str(value or "").strip()
        if not token:
            return None
        if token.startswith("<") and token.endswith(">"):
            return token
        parts = [piece for piece in token.replace("+", "-").split("-") if piece]
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
                def _wrapped(event: tk.Event[tk.Misc], current_handler: Callable[[tk.Event[tk.Misc]], str] = handler) -> str:
                    if self._active_popup_name is not None:
                        return "break"
                    return current_handler(event)
                self.root.bind(seq, _wrapped)
                self._bound_shortcut_sequences.append(seq)
            except Exception:
                continue

    def _guard_key_handler(
        self,
        handler: Callable[[tk.Event[tk.Misc]], str]
    ) -> Callable[[tk.Event[tk.Misc]], str]:
        def _wrapped(
            event: tk.Event[tk.Misc]
        ) -> str:
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


def run_player(
    transcript_json: Path | None = None,
    video_path: Path | None = None,
    *,
    audio_path: Path | None = None,
    skim_seconds: float = 5.0,
    start_sec: float = 0.0,
    workers: int = 0,
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

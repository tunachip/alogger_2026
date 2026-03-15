from __future__ import annotations

from typing import Any

FONT = {
    "STYLE": "DejaVu Sans Mono",
    "SIZE": 12,
}

FONT_SIZE_OFFSETS = {
    "SMALL": -3,
    "BODY": -2,
    "BASE": 0,
}

THEME = {
    "APP_BG": "#111111",
    "PANEL_BG": "#000000",
    "SURFACE_BG": "#0d0d0d",
    "SURFACE_ALT_BG": "#151515",
    "BORDER": "#2b2b2b",
    "SELECT_BG": "#161616",
    "FG": "#ffffff",
    "FG_MUTED": "#8f8f8f",
    "FG_SOFT": "#d2d2d2",
    "FG_ACCENT": "#f7d154",
    "FG_INFO": "#7fd7ff",
    "FG_IDLE": "#39d5ff",
    "FG_ERROR": "#ff8a8a",
    "FG_LOG": "#b0b0b0",
    "SELECTED_ROW_BG": "#282828",
    "VIDEO_OVERLAY_BG": "#111111",
    "VIDEO_OVERLAY_FG": "#ffffff",
}

POPUP_SIZES = {
    "ROOT": "1640x880",
    "DEFAULT": "900x620",
    "PICKER": "1540x760",
    "COMMAND": "720x520",
    "AI": "980x680",
    "GOTO": "420x160",
    "SETTINGS": "980x700",
    "INGEST": "900x460",
    "SKIM": "520x190",
    "CHANNEL": "980x680",
    "SUBSCRIPTIONS": "920x620",
    "WORKERS": "1320x620",
}

LAYOUT = {
    "SASH_WIDTH": 4,
    "PROGRESS_BAR_WIDTH": 28,
    "POPUP_MIN_WIDTH": 320,
    "POPUP_MIN_HEIGHT": 180,
    "TITLEBAR_HEIGHT": 24,
    "LAUNCHBAR_HEIGHT": 24,
    "BAR_PAD_X": 8,
    "BAR_PAD_Y": 2,
    "PREVIEW_WIDTH": 360,
    "PREVIEW_HEIGHT": 520,
    "PREVIEW_IMAGE_WIDTH": 344,
    "PREVIEW_IMAGE_HEIGHT": 194,
}

LISTBOX = {
    "COUNT_WIDTH": 8,
    "COMMAND_HEIGHT": 6,
    "AGENT_HEIGHT": 8,
}

PICKER_FIELDS: dict[str, dict[str, Any]] = {
    "title": {
        "label": "Title",
        "width": 56,
    },
    "creator": {
        "label": "Creator",
        "width": 24,
    },
    "length": {
        "label": "Length",
        "width": 8,
    },
    "genre": {
        "label": "Genre",
        "width": 18,
    },
    "summary": {
        "label": "Summary",
        "width": 28,
    },
    "date": {
        "label": "Date",
        "width": 10,
    },
    "video_id": {
        "label": "Video ID",
        "width": 12,
    },
}

ICONS = {
    "WORKER": "",
    "PAUSE": "⏸",
    "PLAY": "▶",
}

PLAYBACK_SPEEDS = [
    0.5,
    0.75,
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
]

THEME_SETTING_IDS: dict[str, str] = {
    "APP_BG": "theme_bg",
    "PANEL_BG": "theme_panel_bg",
    "SURFACE_BG": "theme_surface_bg",
    "SURFACE_ALT_BG": "theme_surface_alt_bg",
    "BORDER": "theme_border",
    "SELECT_BG": "theme_select_bg",
    "FG": "theme_fg",
    "FG_SOFT": "theme_soft_fg",
    "FG_MUTED": "theme_muted_fg",
    "FG_ACCENT": "theme_accent_fg",
    "FG_INFO": "theme_info_fg",
    "FG_IDLE": "theme_idle_fg",
    "FG_ERROR": "theme_error_fg",
    "FG_LOG": "theme_log_fg",
    "SELECTED_ROW_BG": "theme_selected_bg",
    "VIDEO_OVERLAY_BG": "theme_video_overlay_bg",
    "VIDEO_OVERLAY_FG": "theme_video_overlay_fg",
}

THEME_SETTINGS_FIELDS: list[tuple[str, str, str]] = [
    ("theme_bg", "App Background", "APP_BG"),
    ("theme_panel_bg", "Panel Background", "PANEL_BG"),
    ("theme_surface_bg", "Surface Background", "SURFACE_BG"),
    ("theme_surface_alt_bg", "Input Background", "SURFACE_ALT_BG"),
    ("theme_border", "Border", "BORDER"),
    ("theme_select_bg", "Selection Background", "SELECT_BG"),
    ("theme_fg", "Primary Text", "FG"),
    ("theme_soft_fg", "Soft Text", "FG_SOFT"),
    ("theme_muted_fg", "Muted Text", "FG_MUTED"),
    ("theme_accent_fg", "Accent Text", "FG_ACCENT"),
    ("theme_info_fg", "Info Text", "FG_INFO"),
    ("theme_idle_fg", "Idle Text", "FG_IDLE"),
    ("theme_error_fg", "Error Text", "FG_ERROR"),
    ("theme_log_fg", "Log Text", "FG_LOG"),
    ("theme_selected_bg", "Selected Row", "SELECTED_ROW_BG"),
    ("theme_video_overlay_bg", "Pause Overlay BG", "VIDEO_OVERLAY_BG"),
    ("theme_video_overlay_fg", "Pause Overlay FG", "VIDEO_OVERLAY_FG"),
]

STATUS_HINTS: dict[str, str] = {
    "startup": "No video loaded. Press {open_video} to open by title or {open_finder} to search captions.",
    "jobs": "Workflows: Up/Down select command | Enter execute | Esc close",
    "browse": "Type to filter videos | Enter queue selected | Ctrl-R reload | Ctrl-S subscribe",
    "search": "Type query | Up/Down select | Enter open video | Esc close",
    "video_picker": "Type title filter | Enter open | Ctrl-R queue transcript | Ctrl-Y queue summary | Delete remove video+transcript | Esc close",
    "queue_picker": "Type filter | Ctrl-Up/Down add selection while moving | Shift moves cursor only | Enter queues selected + cursor",
    "ingest": "Input + Enter on command: Ingest (URL[s]) | Browse (channel) | Subscribe (channel)",
    "subscriptions": "R refresh | Delete remove selected | P poll now",
    "goto": "Type digits only: 2=SS, 3-4=MMSS, 5+=HHMMSS",
    "skim": "Line1=pre-buffer ms | Line2=post-buffer ms | Enter apply | Ctrl-J toggle skim",
    "settings": "Up/Down select row | Left/Right change tab | Enter edit | Esc close",
    "ai": "Provider={provider} | Enter send | Esc close",
    "browse_prompt": "Enter a channel URL, @handle, or name to browse",
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

POPUP_ATTRS: dict[str, str] = {
    "command": "_command_popup",
    "finder": "_search_popup",
    "open_video": "_video_picker_popup",
    "queue_picker": "_queue_picker_popup",
    "ingest": "_ingest_popup",
    "workers": "_jobs_popup",
    "ai": "_ai_popup",
    "settings": "_settings_popup",
    "goto": "_goto_popup",
    "channel": "_channel_popup",
    "subscriptions": "_subscriptions_popup",
}


def build_default_gui_settings() -> dict[str, Any]:
    return {
        "ai_provider": "ollama",
        "ollama_model": "llama3.2:3b",
        "ollama_base_url": "http://127.0.0.1:11434",
        "api_base_url": "https://api.openai.com",
        "api_key_env": "OPENAI_API_KEY",
        "api_model": "gpt-4o-mini",
        "auto_summary_default": False,
        "summary_segment_limit": 120,
        "summary_instructions_path": "",
        "default_worker_count": 0,
        "default_downloaders": 1,
        "default_transcribers": 1,
        "default_summarizers": 1,
        "job_retry_limit": 0,
        "auto_transcribe_default": True,
        "subscription_db_max_videos": 0,
        "theme_bg": THEME["APP_BG"],
        "theme_panel_bg": THEME["PANEL_BG"],
        "theme_surface_bg": THEME["SURFACE_BG"],
        "theme_surface_alt_bg": THEME["SURFACE_ALT_BG"],
        "theme_border": THEME["BORDER"],
        "theme_select_bg": THEME["SELECT_BG"],
        "theme_fg": THEME["FG"],
        "theme_soft_fg": THEME["FG_SOFT"],
        "theme_muted_fg": THEME["FG_MUTED"],
        "theme_accent_fg": THEME["FG_ACCENT"],
        "theme_info_fg": THEME["FG_INFO"],
        "theme_idle_fg": THEME["FG_IDLE"],
        "theme_error_fg": THEME["FG_ERROR"],
        "theme_log_fg": THEME["FG_LOG"],
        "theme_selected_bg": THEME["SELECTED_ROW_BG"],
        "theme_video_overlay_bg": THEME["VIDEO_OVERLAY_BG"],
        "theme_video_overlay_fg": THEME["VIDEO_OVERLAY_FG"],
        "font_family": FONT["STYLE"],
        "font_size": FONT["SIZE"],
        "show_root_top_bar": True,
        "show_launch_bar": True,
        "show_popup_top_bar": True,
        "video_picker_fields": ["title", "creator", "length"],
        "queue_picker_fields": ["title", "creator", "length"],
        "finder_fields": ["title", "creator", "length"],
        "video_picker_field_widths": {},
        "queue_picker_field_widths": {},
        "finder_field_widths": {},
        "video_picker_sort_fields": [],
        "queue_picker_sort_fields": [],
        "defer_local_picker_preview": True,
        "defer_browse_preview": True,
        "video_picker_default_field": "title",
        "finder_default_field": "ts",
        "keybinds": dict(DEFAULT_KEYBINDS),
    }

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
}

POPUP_SIZES = {
    "ROOT": "1640x880",
    "DEFAULT": "900x620",
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
        "default_worker_count": 0,
        "default_downloaders": 1,
        "default_transcribers": 1,
        "auto_transcribe_default": True,
        "subscription_db_max_videos": 0,
        "theme_bg": THEME["APP_BG"],
        "theme_panel_bg": THEME["PANEL_BG"],
        "theme_fg": THEME["FG"],
        "theme_muted_fg": THEME["FG_MUTED"],
        "theme_accent_fg": THEME["FG_ACCENT"],
        "font_family": FONT["STYLE"],
        "font_size": FONT["SIZE"],
        "keybinds": dict(DEFAULT_KEYBINDS),
    }

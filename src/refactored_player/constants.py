from __future__ import annotations

from typing import Any

FONT = {
    "STYLE": "DejaVu Sans Mono",
    "SIZE": 12,
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
        "theme_bg": "#111111",
        "theme_panel_bg": "#000000",
        "theme_fg": "#ffffff",
        "theme_muted_fg": "#8f8f8f",
        "theme_accent_fg": "#f7d154",
        "font_family": FONT["STYLE"],
        "font_size": FONT["SIZE"],
        "keybinds": dict(DEFAULT_KEYBINDS),
    }

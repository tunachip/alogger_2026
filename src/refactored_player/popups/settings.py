from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from ..constants import (
    DEFAULT_KEYBINDS,
    FONT,
    FONT_SIZE_OFFSETS,
    POPUP_SIZES,
    THEME,
    THEME_SETTINGS_FIELDS,
)


class SettingsPopupMixin:
    def _open_settings_popup(self) -> None:
        popup = self._create_popup_window(
            name="settings",
            title="Settings",
            size=POPUP_SIZES["SETTINGS"],
            attr_name="_settings_popup",
            row_weights={0: 1, 1: 0, 2: 0},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        tabs = ttk.Notebook(content, style="Terminal.TNotebook")
        tabs.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 6))

        tab_names = ["Ingest", "Theme", "Subscription", "AI", "Keybinds"]
        tab_frames: list[tk.Frame] = []
        key_lists: list[tk.Listbox] = []
        val_lists: list[tk.Listbox] = []
        for name in tab_names:
            frame = tk.Frame(tabs, bg=THEME["APP_BG"])
            frame.rowconfigure(1, weight=1)
            frame.columnconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)
            tabs.add(frame, text=name)
            tab_frames.append(frame)

            tk.Label(
                frame,
                text="Key",
                anchor="w",
                bg=self._theme_color("SURFACE_BG"),
                fg=self._theme_color("FG_MUTED"),
                font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                padx=8,
                pady=4,
            ).grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=(8, 4))
            tk.Label(
                frame,
                text="Value",
                anchor="w",
                bg=self._theme_color("SURFACE_BG"),
                fg=self._theme_color("FG_MUTED"),
                font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                padx=8,
                pady=4,
            ).grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=(8, 4))

            k_list = tk.Listbox(
                frame,
                bg=self._theme_color("PANEL_BG"),
                fg=self._theme_color("FG_SOFT"),
                selectbackground=self._theme_color("SELECT_BG"),
                selectforeground=self._theme_color("FG"),
                activestyle="none",
                borderwidth=0,
                highlightthickness=0,
                exportselection=False,
                font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
            )
            v_list = tk.Listbox(
                frame,
                bg=self._theme_color("PANEL_BG"),
                fg=self._theme_color("FG"),
                selectbackground=self._theme_color("SELECT_BG"),
                selectforeground=self._theme_color("FG"),
                activestyle="none",
                borderwidth=0,
                highlightthickness=0,
                exportselection=False,
                font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
            )
            k_list.grid(row=1, column=0, sticky="nsew",
                        padx=(8, 4), pady=(0, 8))
            v_list.grid(row=1, column=1, sticky="nsew",
                        padx=(4, 8), pady=(0, 8))
            key_lists.append(k_list)
            val_lists.append(v_list)

        edit_var = tk.StringVar(value="")
        edit_entry = ttk.Entry(
            content, textvariable=edit_var, style="Filter.TEntry")
        edit_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        edit_entry.grid_remove()

        status_var = tk.StringVar(
            value=self._status_hint("settings"))
        tk.Label(
            content,
            textvariable=status_var,
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
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
                    {
                        "id": "default_downloaders",
                        "key": "Downloaders",
                        "value": str(
                            self._ai_settings.get("default_downloaders", 1))
                    },
                    {
                        "id": "default_transcribers",
                        "key": "Transcribers",
                        "value": str(
                            self._ai_settings.get("default_transcribers", 1))
                    },
                    {
                        "id": "auto_transcribe_default",
                        "key": "Auto Transcribe",
                        "value": (
                            "on"
                            if self._auto_transcribe_default()
                            else "off"
                        )
                    },
                    {
                        "id": "skim_pre",
                        "key": "Skim Pre (ms)",
                        "value": str(self._skim_pre_ms)
                    },
                    {
                        "id": "skim_post",
                        "key": "Skim Post (ms)",
                        "value": str(self._skim_post_ms)
                    },
                ]
            if tab_idx == 1:
                rows = [
                    {
                        "id": setting_id,
                        "key": label,
                        "value": str(
                            self._ai_settings.get(setting_id)
                            or THEME[theme_key]
                        )
                    }
                    for setting_id, label, theme_key in THEME_SETTINGS_FIELDS
                ]
                rows.extend([
                    {
                        "id": "font_family",
                        "key": "Font Family",
                        "value": str(
                            self._ai_settings.get("font_family")
                            or FONT["STYLE"]
                        )
                    },
                    {
                        "id": "font_size",
                        "key": "Font Size",
                        "value": str(
                            self._ai_settings.get("font_size")
                            or FONT["SIZE"]
                        )
                    },
                    {
                        "id": "show_root_top_bar",
                        "key": "Root Top Bar",
                        "value": "on" if bool(self._ai_settings.get("show_root_top_bar", True)) else "off",
                    },
                    {
                        "id": "show_launch_bar",
                        "key": "Launch Bar",
                        "value": "on" if bool(self._ai_settings.get("show_launch_bar", True)) else "off",
                    },
                    {
                        "id": "show_popup_top_bar",
                        "key": "Popup Top Bars",
                        "value": "on" if bool(self._ai_settings.get("show_popup_top_bar", True)) else "off",
                    },
                ])
                return rows
            if tab_idx == 2:
                rows = [
                    {
                        "id": "subscription_db_max_videos",
                        "key": "DB Max Videos",
                        "value": str(
                            self._ai_settings.get("subscription_db_max_videos")
                            or 0
                        ),
                    }
                ]
                try:
                    subs = self.ingester.list_channel_subscriptions(
                        active_only=False)
                    for row in subs:
                        key = str(row.get("channel_key") or "")
                        title = str(row.get("channel_title") or key)
                        active = "on" if int(
                            row.get("active") or 0) == 1 else "off"
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
                    {
                        "id": "ai_provider",
                        "key": "Provider",
                        "value": str(
                            self._ai_settings.get("ai_provider")
                            or "ollama"
                        )
                    },
                    {
                        "id": "ollama_model",
                        "key": "Ollama Model",
                        "value": str(
                            self._ai_settings.get("ollama_model")
                            or "llama3.2:3b"
                        )
                    },
                    {
                        "id": "ollama_base_url",
                        "key": "Ollama Base URL",
                        "value": str(
                            self._ai_settings.get("ollama_base_url")
                            or "http://127.0.0.1:11434"
                        )
                    },
                    {
                        "id": "api_base_url",
                        "key": "API Base URL",
                        "value": str(
                            self._ai_settings.get("api_base_url")
                            or "https://api.openai.com"
                        )
                    },
                    {
                        "id": "api_key_env",
                        "key": "API Key Env",
                        "value": str(
                            self._ai_settings.get("api_key_env")
                            or "OPENAI_API_KEY"
                        )
                    },
                    {
                        "id": "api_model",
                        "key": "API Model",
                        "value": str(
                            self._ai_settings.get("api_model")
                            or "gpt-4o-mini"
                        )
                    },
                ]
            rows: list[dict[str, str]] = []
            kb = self._ai_settings.get("keybinds") or {}
            for label, key in keybind_defs:
                rows.append({
                    "id": f"kb:{key}",
                    "key": label,
                    "value": str(kb.get(key, DEFAULT_KEYBINDS.get(key, "")))
                })
            return rows

        selected_idx = [0 for _ in tab_names]
        tab_rows: list[list[dict[str, str]]] = [[] for _ in tab_names]
        editing = {
            "active": False,
            "tab": 0,
            "row": 0,
            "orig": ""
        }

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
            prev_provider = str(self._ai_settings.get(
                "ai_provider") or "ollama")
            prev_model = str(self._ai_settings.get(
                "ollama_model") or "llama3.2:3b")
            prev_base = str(self._ai_settings.get(
                "ollama_base_url") or "http://127.0.0.1:11434")
            token = value.strip()
            try:
                if row_id == "default_downloaders":
                    self._ai_settings["default_downloaders"] = max(
                        0, int(token or "0"))
                elif row_id == "default_transcribers":
                    self._ai_settings["default_transcribers"] = max(
                        0, int(token or "0"))
                elif row_id == "auto_transcribe_default":
                    self._ai_settings["auto_transcribe_default"] = token.lower() in {
                        "1", "true", "yes", "on"}
                elif row_id == "skim_pre":
                    self._skim_pre_ms = max(0, int(token or "0"))
                elif row_id == "skim_post":
                    self._skim_post_ms = max(0, int(token or "0"))
                elif row_id in {field[0] for field in THEME_SETTINGS_FIELDS}:
                    color = _norm_hex(token)
                    if not color:
                        return "Invalid hex color; use #RRGGBB"
                    self._ai_settings[row_id] = color
                elif row_id == "font_family":
                    self._ai_settings["font_family"] = token or FONT["STYLE"]
                elif row_id == "font_size":
                    self._ai_settings["font_size"] = max(8, int(token or "12"))
                elif row_id in {
                    "show_root_top_bar",
                    "show_launch_bar",
                    "show_popup_top_bar",
                }:
                    self._ai_settings[row_id] = token.lower() in {"1", "true", "yes", "on"}
                elif row_id == "subscription_db_max_videos":
                    self._ai_settings["subscription_db_max_videos"] = max(
                        0, int(token or "0"))
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
                            self.ingester.update_channel_subscription(
                                channel_key, active=active_val)
                        if mode_val is not None:
                            if mode_val == "default":
                                self.ingester.update_channel_subscription(
                                    channel_key, clear_auto_transcribe=True)
                            elif mode_val in {"on", "true", "1", "yes"}:
                                self.ingester.update_channel_subscription(
                                    channel_key, auto_transcribe=True)
                            elif mode_val in {"off", "false", "0", "no"}:
                                self.ingester.update_channel_subscription(
                                    channel_key, auto_transcribe=False)
                elif row_id in {
                    "ai_provider",
                    "ollama_model",
                    "ollama_base_url",
                    "api_base_url",
                    "api_key_env",
                    "api_model"
                }:
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
                    auto_transcribe_default=bool(self._ai_settings.get(
                        "auto_transcribe_default",
                        True
                    )),
                    subscription_db_max_videos=int(self._ai_settings.get(
                        "subscription_db_max_videos"
                    ) or 0),
                )
                target_downloaders = max(0, int(self._ai_settings.get(
                    "default_downloaders"
                ) or 0))
                target_transcribers = max(0, int(self._ai_settings.get(
                    "default_transcribers"
                ) or 0))
                target_workers = max(target_downloaders, target_transcribers)
                if (
                    self._worker_target_count != target_workers
                    or self._downloader_target_count != target_downloaders
                    or self._transcriber_target_count != target_transcribers
                ):
                    self._restart_workers(
                        target_workers,
                        target_downloaders,
                        target_transcribers
                    )

                self._apply_theme()
                self._apply_font_scale_tree(self.root, reset_base=True)
                self._apply_root_chrome_settings()
                self._refresh_caption_view()
                self._apply_shortcut_bindings()
                self._save_gui_settings()

                new_provider = str(self._ai_settings.get(
                    "ai_provider") or "ollama")
                if new_provider.lower() == "ollama":
                    if (
                        prev_provider.lower() != "ollama"
                        or prev_model != str(self._ai_settings.get(
                            "ollama_model"
                        ))
                        or prev_base != str(self._ai_settings.get(
                            "ollama_base_url"
                        ))
                    ):
                        self._start_ollama_bootstrap(
                            background=True, force_reset=True)
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
            status_var.set(
                f"Editing `{rows[row]['key']}` | Enter commit | Esc revert")

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
            selected_idx[tab] = max(
                0, min(selected_idx[tab] + delta, len(rows) - 1))
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
        tabs.bind(
            "<<NotebookTabChanged>>",
            lambda _e: (
                _render_current(),
                key_lists[int(tabs.index("current"))].focus_set()
            ),
            add="+"
        )
        key_lists[0].focus_set()

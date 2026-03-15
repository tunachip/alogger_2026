from __future__ import annotations

import colorsys
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

from alog.pipeline import resolve_playback_media_path

from ..constants import FONT_SIZE_OFFSETS, LISTBOX, PICKER_FIELDS, POPUP_SIZES
from ..models import PickerTable
from ..utils import (
    SearchClause,
    format_hms as _fmt_hms,
    matches_search_query,
    parse_advanced_search_query,
    parse_search_query,
    search_terms,
)

try:
    from PIL import Image, ImageOps, ImageTk
except Exception:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]


class SearchPopupMixin:
    def _default_query_text(self, setting_key: str, fallback_field: str) -> str:
        field = str(self._ai_settings.get(setting_key) or fallback_field).strip().lower().removeprefix("$")
        return f"${field} " if field else ""

    def _picker_sort_setting_key(self, picker_name: str) -> str:
        return "queue_picker_sort_fields" if picker_name == "queue_picker" else "video_picker_sort_fields"

    def _picker_fields_setting_key(self, picker_name: str) -> str:
        return "queue_picker_fields" if picker_name == "queue_picker" else "video_picker_fields"

    def _picker_fields_for(self, picker_name: str) -> list[str]:
        setting_key = self._picker_fields_setting_key(picker_name)
        raw = self._ai_settings.get(setting_key)
        values = raw if isinstance(raw, list) else []
        fields = [
            str(value).strip().lower()
            for value in values
            if str(value).strip().lower() in PICKER_FIELDS
        ]
        if not fields:
            fields = ["title", "creator", "length"]
        return fields

    def _set_picker_fields(self, picker_name: str, fields: list[str]) -> None:
        normalized = [
            str(field).strip().lower()
            for field in fields
            if str(field).strip().lower() in PICKER_FIELDS
        ]
        if not normalized:
            normalized = ["title", "creator", "length"]
        self._ai_settings[self._picker_fields_setting_key(picker_name)] = normalized
        self._save_gui_settings()

    def _picker_sort_fields_for(self, picker_name: str) -> list[str]:
        raw = self._ai_settings.get(self._picker_sort_setting_key(picker_name))
        values = raw if isinstance(raw, list) else []
        return [
            str(value).strip().lower()
            for value in values
            if str(value).strip().lower() in self._picker_fields_for(picker_name)
        ]

    def _set_picker_sort_fields(self, picker_name: str, fields: list[str]) -> None:
        visible = set(self._picker_fields_for(picker_name))
        normalized = []
        for field in fields:
            token = str(field).strip().lower()
            if token in visible and token not in normalized:
                normalized.append(token)
        self._ai_settings[self._picker_sort_setting_key(picker_name)] = normalized
        self._save_gui_settings()

    def _picker_widths_setting_key(self, picker_name: str) -> str:
        return "queue_picker_field_widths" if picker_name == "queue_picker" else "video_picker_field_widths"

    def _picker_field_widths_for(self, picker_name: str) -> dict[str, int]:
        raw = self._ai_settings.get(self._picker_widths_setting_key(picker_name))
        if not isinstance(raw, dict):
            return {}
        widths: dict[str, int] = {}
        for key, value in raw.items():
            token = str(key).strip().lower()
            if token not in PICKER_FIELDS:
                continue
            try:
                widths[token] = max(48, int(value))
            except Exception:
                continue
        return widths

    def _set_picker_field_widths(self, picker_name: str, widths: dict[str, int]) -> None:
        normalized = {
            str(key).strip().lower(): max(48, int(value))
            for key, value in widths.items()
            if str(key).strip().lower() in PICKER_FIELDS
        }
        self._ai_settings[self._picker_widths_setting_key(picker_name)] = normalized
        self._save_gui_settings()

    def _picker_columns_for(self, picker_name: str) -> list[tuple[str, str, int]]:
        widths = self._picker_field_widths_for(picker_name)
        sort_fields = self._picker_sort_fields_for(picker_name)
        columns: list[tuple[str, str, int]] = []
        for field in self._picker_fields_for(picker_name):
            spec = PICKER_FIELDS.get(field, {})
            numeral = "-"
            if field in sort_fields:
                idx = sort_fields.index(field)
                numerals = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ"]
                numeral = numerals[idx] if idx < len(numerals) else str(idx + 1)
            columns.append(
                (
                    field,
                    f"{str(spec.get('label') or field.title())} {numeral}",
                    int(widths.get(field) or spec.get("width") or 120),
                )
            )
        return columns

    def _cycle_picker_sort_field(
        self,
        picker_name: str,
        field_name: str,
        *,
        reverse: bool = False,
    ) -> None:
        field = str(field_name or "").strip().lower()
        visible_fields = self._picker_fields_for(picker_name)
        if field not in visible_fields:
            return
        sort_fields = self._picker_sort_fields_for(picker_name)
        if reverse:
            if field not in sort_fields:
                sort_fields.insert(0, field)
            else:
                idx = sort_fields.index(field)
                if idx >= len(sort_fields) - 1:
                    sort_fields.pop(idx)
                else:
                    sort_fields[idx], sort_fields[idx + 1] = sort_fields[idx + 1], sort_fields[idx]
        else:
            if field not in sort_fields:
                sort_fields.append(field)
            else:
                idx = sort_fields.index(field)
                if idx == 0:
                    sort_fields.pop(idx)
                else:
                    sort_fields[idx], sort_fields[idx - 1] = sort_fields[idx - 1], sort_fields[idx]
        self._set_picker_sort_fields(picker_name, sort_fields)

    def _move_picker_field(
        self,
        picker_name: str,
        field_name: str,
        *,
        delta: int,
    ) -> None:
        field = str(field_name or "").strip().lower()
        fields = self._picker_fields_for(picker_name)
        if field not in fields:
            return
        idx = fields.index(field)
        nxt = max(0, min(len(fields) - 1, idx + int(delta)))
        if idx == nxt:
            return
        fields[idx], fields[nxt] = fields[nxt], fields[idx]
        self._set_picker_fields(picker_name, fields)

    def _picker_field_value(self, row: dict[str, Any], field: str) -> str:
        token = str(field or "").strip().lower()
        if token == "title":
            return str(row.get("title") or row.get("video_id") or "untitled").replace("\n", " ").strip()
        if token == "creator":
            return " ".join(
                part
                for part in [
                    str(row.get("channel") or "").strip(),
                    str(row.get("uploader_id") or "").strip(),
                ]
                if part
            ) or "unknown"
        if token == "length":
            return self._metadata_text_for_field(row, "LENGTH").split(" ", 1)[-1].strip() or "--:--"
        if token == "genre":
            return self._metadata_text_for_field(row, "GENRE").replace("\n", " ").strip()
        if token == "summary":
            return self._metadata_text_for_field(row, "SUMMARY").replace("\n", " ").strip()
        if token == "date":
            return str(row.get("upload_date") or "").strip()
        if token == "video_id":
            return str(row.get("video_id") or "").strip()
        return ""

    def _picker_row_values(
        self,
        row: dict[str, Any],
        picker_name: str,
    ) -> dict[str, str]:
        values = {
            field: self._picker_field_value(row, field)
            for field in self._picker_fields_for(picker_name)
        }
        values["id"] = str(row.get("video_id") or "")
        return values

    def _picker_sort_key(
        self,
        row: dict[str, Any],
        field: str,
    ) -> tuple[int, Any]:
        token = str(field or "").strip().lower()
        if token == "length":
            try:
                return (0, int(row.get("duration_sec") or 0))
            except Exception:
                return (1, 0)
        if token == "date":
            return (0, str(row.get("upload_date") or "").strip().lower())
        return (0, self._picker_field_value(row, token).lower())

    def _sort_picker_rows(
        self,
        rows: list[dict[str, Any]],
        picker_name: str,
    ) -> list[dict[str, Any]]:
        sort_fields = self._picker_sort_fields_for(picker_name)
        if not sort_fields:
            return rows
        ordered = list(rows)
        for field in reversed(sort_fields):
            ordered.sort(key=lambda row, token=field: self._picker_sort_key(row, token))
        return ordered

    def _picker_clause_colors(self, count: int) -> list[str]:
        accent = str(self._theme_color("FG_ACCENT") or "").strip()
        if not accent.startswith("#") or len(accent) != 7:
            return [accent for _ in range(max(1, count))]
        try:
            r = int(accent[1:3], 16) / 255.0
            g = int(accent[3:5], 16) / 255.0
            b = int(accent[5:7], 16) / 255.0
        except Exception:
            return [accent for _ in range(max(1, count))]
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        total = 10
        order = [0, 5, 2, 7, 1, 6, 3, 8, 4, 9]
        palette: list[str] = []
        for idx in range(total):
            nr, ng, nb = colorsys.hls_to_rgb((h + (idx / float(total))) % 1.0, l, max(0.15, s))
            palette.append(
                "#{:02x}{:02x}{:02x}".format(
                    int(max(0, min(255, round(nr * 255.0)))),
                    int(max(0, min(255, round(ng * 255.0)))),
                    int(max(0, min(255, round(nb * 255.0)))),
                )
            )
        arranged = [palette[idx] for idx in order]
        return [arranged[idx % total] for idx in range(max(1, count))]

    def _picker_highlight_specs(
        self,
        query_text: str,
        visible_fields: list[str],
    ) -> list[tuple[set[str], list[str], str]]:
        query = str(query_text or "").strip()
        if not query:
            return []
        specs: list[tuple[set[str], list[str], str]] = []
        if "$" in query or ";" in query or "!" in query or "*" in query:
            clauses = parse_advanced_search_query(query)
            colors = self._picker_clause_colors(len(clauses))
            field_map = {
                "TITLE": {"title"},
                "CREATOR": {"creator"},
                "GENRE": {"genre"},
                "SUMMARY": {"summary"},
                "LENGTH": {"length"},
                "ANY": set(visible_fields),
            }
            for idx, clause in enumerate(clauses):
                if clause.negated or clause.expression.strip() == "*" or clause.field == "TS":
                    continue
                terms = search_terms(clause.expression)
                if not terms:
                    continue
                targets = field_map.get(clause.field, {clause.field.lower()})
                targets = {field for field in targets if field in visible_fields}
                if targets:
                    specs.append((targets, terms, colors[idx]))
            return specs
        clauses = parse_search_query(query)
        colors = self._picker_clause_colors(len(clauses))
        for idx, clause in enumerate(clauses):
            terms = [term for term in clause if term]
            if terms:
                specs.append(({"title"}, terms, colors[idx]))
        return specs

    def _attach_picker_fields_menu(
        self,
        button: tk.Menubutton,
        *,
        picker_name: str,
        on_change: Callable[[], None],
    ) -> dict[str, tk.BooleanVar]:
        field_vars: dict[str, tk.BooleanVar] = {}
        menu = tk.Menu(
            button,
            tearoff=False,
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG"),
            activebackground=self._theme_color("SELECT_BG"),
            activeforeground=self._theme_color("FG"),
            bd=0,
        )
        button.configure(menu=menu)

        def _toggle(field_name: str) -> None:
            fields = self._picker_fields_for(picker_name)
            enabled = bool(field_vars[field_name].get())
            if enabled and field_name not in fields:
                fields.append(field_name)
            elif not enabled and field_name in fields:
                fields = [field for field in fields if field != field_name]
            self._set_picker_fields(picker_name, fields)
            for token, var in field_vars.items():
                var.set(token in self._picker_fields_for(picker_name))
            on_change()

        for field_name, spec in PICKER_FIELDS.items():
            var = tk.BooleanVar(value=field_name in self._picker_fields_for(picker_name))
            field_vars[field_name] = var
            menu.add_checkbutton(
                label=str(spec.get("label") or field_name.title()),
                variable=var,
                command=lambda name=field_name: _toggle(name),
            )
        return field_vars

    def _darken_hex_color(self, color: str, factor: float = 0.82) -> str:
        token = str(color or "").strip()
        if not token.startswith("#") or len(token) != 7:
            return color
        try:
            r = int(token[1:3], 16)
            g = int(token[3:5], 16)
            b = int(token[5:7], 16)
        except Exception:
            return color
        if r == 0 and g == 0 and b == 0:
            return "#000000"
        return "#{:02x}{:02x}{:02x}".format(
            max(0, min(255, int(r * factor))),
            max(0, min(255, int(g * factor))),
            max(0, min(255, int(b * factor))),
        )

    def _video_preview_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = self._row_metadata_payload(row)
        title = str(row.get("title") or row.get("video_id") or "untitled").strip()
        creator = " ".join(
            part for part in [
                str(row.get("channel") or ""),
                str(row.get("uploader_id") or ""),
            ] if part
        ).strip() or "unknown"
        genre = self._metadata_text_for_field(row, "GENRE").strip()
        description = " ".join(
            str(part).strip()
            for part in [
                payload.get("description"),
                payload.get("summary"),
            ]
            if str(part or "").strip()
        ).strip()
        thumbnail_url = str(
            payload.get("thumbnail")
            or row.get("thumbnail")
            or row.get("webpage_url")
            or ""
        ).strip()
        video_id = str(row.get("video_id") or "").strip()
        if thumbnail_url and "youtube.com/watch" in thumbnail_url:
            thumbnail_url = ""
        image_path = None
        if video_id and not bool(row.get("_skip_thumbnail_download")):
            image_path = self._download_browse_thumbnail(
                video_id,
                thumbnail_url or f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                cache_dir=self._video_thumb_dir,
                temporary=False,
            )
        return {
            "title": title,
            "creator": creator,
            "genre": genre,
            "description": description or "(no description)",
            "image_path": str(image_path) if image_path else "",
        }

    def _render_video_preview(
        self,
        row: dict[str, Any],
        *,
        image_label: tk.Label,
        title_var: tk.StringVar,
        creator_var: tk.StringVar,
        genre_var: tk.StringVar,
        description_widget: tk.Text,
        photo_ref: dict[str, Any],
        image_size: tuple[int, int],
    ) -> None:
        preview = self._video_preview_payload(row)
        title_var.set(str(preview.get("title") or "untitled"))
        creator_var.set(f"creator: {preview.get('creator') or 'unknown'}")
        genre_value = str(preview.get("genre") or "").strip()
        genre_var.set(f"genre: {genre_value}" if genre_value else "genre: (none)")
        description_widget.configure(state="normal")
        description_widget.delete("1.0", tk.END)
        description_widget.insert("1.0", str(preview.get("description") or "(no description)"))
        description_widget.configure(state="disabled")
        image_path = str(preview.get("image_path") or "").strip()
        if image_path and Image is not None and ImageTk is not None:
            try:
                with Image.open(image_path) as img:
                    img = img.convert("RGB")
                    if ImageOps is not None:
                        img = ImageOps.fit(img, image_size, method=Image.Resampling.LANCZOS)
                    else:
                        img = img.resize(image_size, Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                photo_ref["image"] = photo
                image_label.configure(image=photo, text="")
                return
            except Exception:
                pass
        photo_ref["image"] = None
        image_label.configure(image="", text="Preview unavailable")

    def _render_video_preview_placeholder(
        self,
        row: dict[str, Any],
        *,
        image_label: tk.Label,
        title_var: tk.StringVar,
        creator_var: tk.StringVar,
        genre_var: tk.StringVar,
        description_widget: tk.Text,
        photo_ref: dict[str, Any],
        loading: bool,
    ) -> None:
        preview = self._video_preview_payload({**row, "_skip_thumbnail_download": True})
        title_var.set(str(preview.get("title") or "untitled"))
        creator_var.set(f"creator: {preview.get('creator') or 'unknown'}")
        genre_value = str(preview.get("genre") or "").strip()
        genre_var.set(f"genre: {genre_value}" if genre_value else "genre: (none)")
        description_widget.configure(state="normal")
        description_widget.delete("1.0", tk.END)
        description_widget.insert("1.0", str(preview.get("description") or "(no description)"))
        description_widget.configure(state="disabled")
        photo_ref["image"] = None
        image_label.configure(image="", text="Loading preview..." if loading else "Preview unavailable")

    def _build_video_preview_pane(
        self,
        parent: tk.Misc,
        *,
        image_width: int = 344,
        image_height: int = 194,
    ) -> dict[str, Any]:
        frame = tk.Frame(
            parent,
            bg=self._theme_color("PANEL_BG"),
            highlightthickness=1,
            highlightbackground=self._theme_color("BORDER"),
            bd=0,
        )
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)
        image_box = tk.Frame(
            frame,
            bg=self._theme_color("PANEL_BG"),
            width=image_width,
            height=image_height,
            highlightthickness=0,
            bd=0,
        )
        image_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        image_box.grid_propagate(False)
        image_box.rowconfigure(0, weight=1)
        image_box.columnconfigure(0, weight=1)
        image_label = tk.Label(
            image_box,
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG_MUTED"),
            text="No preview",
            anchor="center",
            justify="center",
            font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
        )
        image_label.grid(row=0, column=0, sticky="nsew")
        title_var = tk.StringVar(value="")
        creator_var = tk.StringVar(value="")
        genre_var = tk.StringVar(value="")
        tk.Label(
            frame,
            textvariable=title_var,
            anchor="w",
            justify="left",
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG"),
            font=self._ui_font(FONT_SIZE_OFFSETS["BODY"], bold=True),
            wraplength=image_width,
        ).grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        tk.Label(
            frame,
            textvariable=creator_var,
            anchor="w",
            justify="left",
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG_SOFT"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            wraplength=image_width,
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 2))
        tk.Label(
            frame,
            textvariable=genre_var,
            anchor="w",
            justify="left",
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG_INFO"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            wraplength=image_width,
        ).grid(row=3, column=0, sticky="new", padx=8, pady=(0, 2))
        description = tk.Text(
            frame,
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG_SOFT"),
            borderwidth=0,
            highlightthickness=0,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            wrap="word",
            padx=8,
            pady=6,
            height=8,
        )
        description.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        description.configure(state="disabled")
        return {
            "frame": frame,
            "image_label": image_label,
            "title_var": title_var,
            "creator_var": creator_var,
            "genre_var": genre_var,
            "description": description,
            "photo_ref": {"image": None},
            "image_size": (image_width, image_height),
        }

    def _row_metadata_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        cached = row.get("_metadata_payload")
        if isinstance(cached, dict):
            return cached
        raw = str(row.get("metadata_json") or "").strip()
        if not raw:
            payload: dict[str, Any] = {}
        else:
            try:
                loaded = json.loads(raw)
                payload = dict(loaded) if isinstance(loaded, dict) else {}
            except Exception:
                payload = {}
        row["_metadata_payload"] = payload
        return payload

    def _load_transcript_text_for_row(self, row: dict[str, Any]) -> str:
        cached = row.get("_transcript_text")
        if isinstance(cached, str):
            return cached
        transcript_raw = str(row.get("transcript_json_path") or "").strip()
        if not transcript_raw:
            row["_transcript_text"] = ""
            return ""
        transcript_path = Path(transcript_raw)
        if not transcript_path.exists():
            row["_transcript_text"] = ""
            return ""
        try:
            payload = transcript_path.read_text(encoding="utf-8")
        except Exception:
            row["_transcript_text"] = ""
            return ""
        row["_transcript_text"] = payload.lower()
        return row["_transcript_text"]

    def _metadata_text_for_field(self, row: dict[str, Any], field: str) -> str:
        payload = self._row_metadata_payload(row)
        title = str(row.get("title") or row.get("video_id") or "")
        creator = " ".join(
            part for part in [
                str(row.get("channel") or ""),
                str(row.get("uploader_id") or ""),
            ] if part
        )
        genre = " ".join(
            str(part)
            for part in (
                payload.get("genre"),
                payload.get("categories"),
                payload.get("category"),
                payload.get("tags"),
            )
            if part
        )
        if field == "TITLE":
            return title
        if field == "CREATOR":
            return creator
        if field == "GENRE":
            return genre
        if field == "LENGTH":
            duration = row.get("duration_sec")
            if duration is None:
                return ""
            try:
                seconds = int(duration)
            except Exception:
                return str(duration)
            return f"{seconds} {_fmt_hms(float(seconds))}"
        if field == "SUMMARY":
            return " ".join(
                str(part)
                for part in [
                    payload.get("summary"),
                    payload.get("description"),
                    payload.get("fulltitle"),
                ]
                if part
            )
        if field == "ANY":
            return " ".join(
                part for part in [
                    title,
                    creator,
                    genre,
                    self._metadata_text_for_field(row, "SUMMARY"),
                    self._metadata_text_for_field(row, "LENGTH"),
                    str(row.get("video_id") or ""),
                    str(row.get("source_url") or ""),
                    str(row.get("webpage_url") or ""),
                    str(row.get("upload_date") or ""),
                ] if part
            )
        direct = row.get(field.lower())
        if direct is not None:
            return str(direct)
        meta_value = payload.get(field.lower())
        if meta_value is None:
            meta_value = payload.get(field)
        if isinstance(meta_value, list):
            return " ".join(str(item) for item in meta_value if item is not None)
        if meta_value is not None:
            return str(meta_value)
        return ""

    def _clause_matches_row(self, row: dict[str, Any], clause: SearchClause) -> bool:
        expression = clause.expression.strip()
        if clause.field == "TS":
            haystack = self._load_transcript_text_for_row(row)
        elif clause.field == "ANY":
            haystack = self._metadata_text_for_field(row, "ANY")
            if expression != "*":
                transcript_text = self._load_transcript_text_for_row(row)
                if transcript_text:
                    haystack = f"{haystack} {transcript_text}"
        else:
            haystack = self._metadata_text_for_field(row, clause.field)
        if expression == "*":
            matched = bool(str(haystack).strip())
        else:
            matched = matches_search_query(haystack, expression)
        return (not matched) if clause.negated else matched

    def _score_search_row(self, row: dict[str, Any], clauses: list[SearchClause]) -> int:
        score = 0
        for clause in clauses:
            if clause.negated:
                continue
            if clause.field == "TS":
                haystack = self._load_transcript_text_for_row(row)
            elif clause.field == "ANY":
                haystack = self._metadata_text_for_field(row, "ANY")
            else:
                haystack = self._metadata_text_for_field(row, clause.field)
            if clause.expression == "*":
                score += 1
                continue
            score += sum(haystack.lower().count(term) for term in search_terms(clause.expression))
        return max(1, score)

    def _candidate_ids_for_clause(
        self,
        clause: SearchClause,
        *,
        limit: int,
    ) -> set[str] | None:
        if clause.negated or clause.expression.strip() == "*":
            return None
        if clause.field == "TS":
            clause_rows: set[str] | None = None
            for term_group in parse_search_query(clause.expression):
                group_rows = {
                    str(row.get("video_id") or "").strip()
                    for term in term_group
                    for row in self.ingester.search_videos(term, limit=max(limit * 4, 120))
                    if str(row.get("video_id") or "").strip()
                }
                clause_rows = group_rows if clause_rows is None else clause_rows.intersection(group_rows)
            return clause_rows or set()
        if clause.field in {"TITLE", "CREATOR", "GENRE", "ANY"}:
            return None
        return None

    def _search_rows_with_advanced_query(
        self,
        query_text: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = parse_advanced_search_query(query_text)
        if not clauses:
            return []
        candidate_ids: set[str] | None = None
        for clause in clauses:
            clause_ids = self._candidate_ids_for_clause(clause, limit=limit)
            if clause_ids is None:
                continue
            candidate_ids = clause_ids if candidate_ids is None else candidate_ids.intersection(clause_ids)
            if not candidate_ids:
                return []
        rows = [
            dict(row)
            for row in self.ingester.db.list_playable_videos(limit=max(limit * 8, 1200))
        ]
        if candidate_ids is not None:
            rows = [
                row
                for row in rows
                if str(row.get("video_id") or "").strip() in candidate_ids
            ]
        matches = [
            row
            for row in rows
            if all(self._clause_matches_row(row, clause) for clause in clauses)
        ]
        for row in matches:
            row["match_count"] = self._score_search_row(row, clauses)
        matches.sort(
            key=lambda row: (
                -int(row.get("match_count") or 0),
                str(row.get("title") or row.get("video_id") or "").lower(),
            )
        )
        return matches[:limit]

    def _search_videos_with_query(
        self,
        query_text: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if "$" in query_text or ";" in query_text or "!" in query_text or "*" in query_text:
            return self._search_rows_with_advanced_query(query_text, limit=limit)
        clauses = parse_search_query(query_text)
        if not clauses:
            return []
        merged: dict[str, dict[str, Any]] = {}
        for clause in clauses:
            clause_rows: dict[str, dict[str, Any]] | None = None
            for term in clause:
                rows = [
                    dict(row)
                    for row in self.ingester.search_videos(term, limit=max(limit * 4, 100))
                ]
                term_map = {
                    str(row.get("video_id") or "").strip(): row
                    for row in rows
                    if str(row.get("video_id") or "").strip()
                }
                if clause_rows is None:
                    clause_rows = term_map
                    continue
                shared_ids = set(clause_rows).intersection(term_map)
                clause_rows = {
                    video_id: {
                        **clause_rows[video_id],
                        "match_count": int(clause_rows[video_id].get("match_count") or 0)
                        + int(term_map[video_id].get("match_count") or 0),
                        "first_start_ms": min(
                            int(clause_rows[video_id].get("first_start_ms") or 0),
                            int(term_map[video_id].get("first_start_ms") or 0),
                        ),
                    }
                    for video_id in shared_ids
                }
            for video_id, row in (clause_rows or {}).items():
                existing = merged.get(video_id)
                if existing is None:
                    merged[video_id] = row
                    continue
                existing["match_count"] = max(
                    int(existing.get("match_count") or 0),
                    int(row.get("match_count") or 0),
                )
                existing["first_start_ms"] = min(
                    int(existing.get("first_start_ms") or 0),
                    int(row.get("first_start_ms") or 0),
                )
        rows = list(merged.values())
        rows.sort(
            key=lambda row: (
                -int(row.get("match_count") or 0),
                int(row.get("first_start_ms") or 0),
            )
        )
        return rows[:limit]

    def _search_video_titles_with_query(
        self,
        query_text: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if "$" in query_text or ";" in query_text or "!" in query_text or "*" in query_text:
            return self._search_rows_with_advanced_query(query_text, limit=limit)
        rows = [
            dict(row)
            for row in self.ingester.search_video_titles("", limit=max(limit * 4, 300))
        ]
        if not query_text.strip():
            return rows[:limit]
        filtered = [
            row
            for row in rows
            if matches_search_query(
                str(row.get("title") or row.get("video_id") or ""),
                query_text,
            )
        ]
        filtered.sort(
            key=lambda row: str(row.get("title") or row.get("video_id") or "").lower()
        )
        return filtered[:limit]

    def _open_search_popup(self) -> None:
        popup = self._create_popup_window(
                    name="finder",
                    title="Search DB",
                    size=POPUP_SIZES["PICKER"],
            attr_name="_search_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        query_var = tk.StringVar(value=self.filter_var.get().strip() or self._default_query_text("finder_default_field", "ts"))
        query_entry = self._create_query_entry(
            content,
            query_var,
            enable_field_completion=True,
        )
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header = tk.Label(
            content,
            text="Matches  Title (matches = number of matching caption segments)",
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        body = tk.Frame(content, bg=self._theme_color("APP_BG"))
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=360)
        body.columnconfigure(1, weight=1)

        preview = self._build_video_preview_pane(body)
        preview["frame"].grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        count_list = self._create_listbox(
            body,
            fg=self._theme_color("FG_ACCENT"),
            select_fg=self._theme_color("FG_ACCENT"),
            width=LISTBOX["COUNT_WIDTH"],
            bold=True,
            takefocus=0,
        )
        title_list = self._create_listbox(body)
        count_list.grid(row=0, column=1, sticky="ns")
        title_list.grid(row=0, column=2, sticky="nsew")
        body.columnconfigure(2, weight=1)

        hint = tk.Label(
            content,
            text=self._status_hint("search"),
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=6,
        )
        hint.grid(row=3, column=0, sticky="ew")

        def _set_selection(idx: int) -> None:
            self._set_listbox_selection(
                [count_list, title_list],
                idx,
                len(self._search_results),
            )

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            selected_video_id = ""
            sel = title_list.curselection()
            if sel and 0 <= int(sel[0]) < len(self._search_results):
                selected_video_id = str(
                    self._search_results[int(sel[0])].get("video_id") or ""
                ).strip()
            count_list.delete(0, tk.END)
            title_list.delete(0, tk.END)
            self._search_results = []
            if not query:
                return
            rows = self._search_videos_with_query(query, limit=200)
            self._search_results = [dict(r) for r in rows]
            selected_idx = -1
            for row_idx, row in enumerate(self._search_results):
                title = str(
                    row.get("title")
                    or row.get("video_id")
                    or "untitled"
                ).replace("\n", " ").strip()
                count = int(row.get("match_count") or 0)
                count_list.insert(tk.END, f"{count:>4}")
                title_list.insert(tk.END, title)
                if (
                    selected_video_id
                    and str(row.get("video_id") or "").strip() == selected_video_id
                ):
                    selected_idx = row_idx
            if self._search_results:
                _set_selection(selected_idx if selected_idx >= 0 else 0)

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
            preferred = Path(str(row.get("local_video_path") or "")) if row.get(
                "local_video_path") else None
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

        def move_sel(delta: int) -> str:
            sel = title_list.curselection()
            cur = int(sel[0]) if sel else 0
            _set_selection(cur + delta)
            return "break"

        query_var.trace_add("write", refresh_results)
        self._bind_popup_close(
            popup,
            after_close=lambda: setattr(self, "_search_popup", None),
        )
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
        refresh_results()
        query_entry.focus_set()

    def _open_video_picker_popup(self) -> None:
        popup = self._create_popup_window(
                    name="open_video",
                    title="Open Video",
                    size=POPUP_SIZES["PICKER"],
            attr_name="_video_picker_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        query_var = tk.StringVar(value=self._default_query_text("video_picker_default_field", "title"))
        query_entry = self._create_query_entry(
            content,
            query_var,
            enable_field_completion=True,
        )
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header_row = tk.Frame(content, bg=self._theme_color("SURFACE_BG"))
        header_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        header_row.columnconfigure(0, weight=1)
        fields_button = tk.Menubutton(
            header_row,
            text="Fields",
            direction="below",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_SOFT"),
            activebackground=self._theme_color("SELECT_BG"),
            activeforeground=self._theme_color("FG"),
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=4,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
        )
        fields_button.grid(row=0, column=1, sticky="e")

        body = tk.Frame(content, bg=self._theme_color("APP_BG"))
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.columnconfigure(0, weight=0, minsize=360)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        preview = self._build_video_preview_pane(body)
        preview["frame"].grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        list_shell = tk.Frame(
            body,
            bg=self._theme_color("PANEL_BG"),
            highlightthickness=1,
            highlightbackground=self._theme_color("BORDER"),
            bd=0,
        )
        list_shell.grid(row=0, column=1, sticky="nsew")
        list_shell.columnconfigure(0, weight=1)
        list_shell.rowconfigure(1, weight=1)
        header_strip = tk.Frame(list_shell, bg=self._theme_color("PANEL_BG"))
        header_strip.grid(row=0, column=0, sticky="ew")
        title_list = tk.Text(
            list_shell,
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG"),
            insertbackground=self._theme_color("FG"),
            borderwidth=0,
            highlightthickness=0,
            wrap="none",
            state="disabled",
            cursor="arrow",
            padx=8,
            pady=4,
            font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
        )
        title_list.grid(row=1, column=0, sticky="nsew")
        title_scroll = ttk.Scrollbar(list_shell, orient="vertical", command=title_list.yview)
        title_scroll.grid(row=1, column=1, sticky="ns")
        title_list.configure(yscrollcommand=title_scroll.set)

        hint = tk.Label(
            content,
            text=self._status_hint("video_picker"),
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=6,
        )
        hint.grid(row=3, column=0, sticky="ew")
        status_var = tk.StringVar(value="")
        status_lbl = self._create_status_label(
            content,
            status_var,
            fg=self._theme_color("FG_SOFT"),
            font_delta=-3,
        )
        status_lbl.grid(row=4, column=0, sticky="ew")
        pending_delete: dict[str, str | None] = {"video_id": None}
        row_ids: list[str] = []
        selected_index = {"value": 0}
        preview_request_id = {"value": 0}
        char_width = max(1, self._text_font.measure("0"))

        def _selected_video_id() -> str:
            if 0 <= selected_index["value"] < len(row_ids):
                return row_ids[selected_index["value"]]
            return ""

        def _refresh_header() -> None:
            for child in header_strip.winfo_children():
                child.destroy()
            for idx, (field, label, width) in enumerate(self._picker_columns_for("video_picker")):
                width_chars = max(6, int(round(width / float(char_width))))
                base_label, marker = label.rsplit(" ", 1) if " " in label else (label, "-")
                cell = tk.Frame(header_strip, bg=self._theme_color("PANEL_BG"))
                cell.grid(row=0, column=idx, sticky="ew")
                header_strip.columnconfigure(idx, weight=max(1, width_chars))
                tk.Label(
                    cell,
                    text=base_label,
                    anchor="w",
                    bg=self._theme_color("PANEL_BG"),
                    fg=self._theme_color("FG_MUTED"),
                    font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                    padx=8,
                    pady=4,
                    width=max(4, width_chars - 2),
                ).pack(side="left", fill="x", expand=True)
                marker_label = tk.Label(
                    cell,
                    text=marker,
                    anchor="e",
                    bg=self._theme_color("PANEL_BG"),
                    fg=self._theme_color("FG_MUTED"),
                    font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
                    padx=4,
                    pady=4,
                    cursor="hand2",
                )
                marker_label.pack(side="right")
                marker_label.bind(
                    "<Button-1>",
                    lambda _e, name=field: (self._cycle_picker_sort_field("video_picker", name, reverse=False), refresh_results()),
                    add="+",
                )
                marker_label.bind(
                    "<Button-3>",
                    lambda _e, name=field: (self._cycle_picker_sort_field("video_picker", name, reverse=True), refresh_results()),
                    add="+",
                )
                marker_label.bind("<Enter>", lambda e: e.widget.configure(fg=self._theme_color("FG")), add="+")
                marker_label.bind("<Leave>", lambda e: e.widget.configure(fg=self._theme_color("FG_MUTED")), add="+")

        def _configure_tabs() -> None:
            tabs: list[str] = []
            offset = 0
            for _field, _label, width in self._picker_columns_for("video_picker"):
                offset += max(72, int(width))
                tabs.append(f"{offset}p")
            title_list.configure(tabs=tuple(tabs))

        def _render_rows(query: str) -> None:
            visible_fields = self._picker_fields_for("video_picker")
            specs = self._picker_highlight_specs(query, visible_fields)
            title_list.configure(state="normal")
            title_list.delete("1.0", tk.END)
            title_list.tag_configure(
                "selected_row",
                background=self._theme_color("SELECTED_ROW_BG"),
                foreground=self._theme_color("FG"),
            )
            for idx, (_targets, _terms, color) in enumerate(specs):
                title_list.tag_configure(f"match_{idx}", foreground=color)
                title_list.tag_raise(f"match_{idx}")
            row_ids.clear()
            for row in self._video_picker_results:
                row_ids.append(str(row.get("video_id") or ""))
                for col_idx, field in enumerate(visible_fields):
                    if col_idx:
                        title_list.insert(tk.END, "\t")
                    cell_text = self._picker_field_value(row, field)
                    cell_start = title_list.index(tk.END)
                    title_list.insert(tk.END, cell_text)
                    cell_lower = cell_text.lower()
                    for spec_idx, (target_fields, terms, _color) in enumerate(specs):
                        if field not in target_fields:
                            continue
                        for term in terms:
                            token = str(term or "").strip().lower()
                            if not token:
                                continue
                            start_at = 0
                            while True:
                                found = cell_lower.find(token, start_at)
                                if found < 0:
                                    break
                                title_list.tag_add(
                                    f"match_{spec_idx}",
                                    f"{cell_start}+{found}c",
                                    f"{cell_start}+{found + len(token)}c",
                                )
                                start_at = found + len(token)
                title_list.insert(tk.END, "\n")
            title_list.configure(state="disabled")

        def _set_selection(idx: int) -> None:
            if not row_ids:
                return
            idx = max(0, min(idx, len(row_ids) - 1))
            selected_index["value"] = idx
            title_list.configure(state="normal")
            title_list.tag_remove("selected_row", "1.0", tk.END)
            title_list.tag_add("selected_row", f"{idx + 1}.0", f"{idx + 1}.end")
            for spec_idx, _spec in enumerate(self._picker_highlight_specs(query_var.get().strip(), self._picker_fields_for("video_picker"))):
                title_list.tag_raise(f"match_{spec_idx}")
            title_list.configure(state="disabled")
            title_list.see(f"{idx + 1}.0")
            sel_id = _selected_video_id()
            row = next(
                (
                    item
                    for item in self._video_picker_results
                    if str(item.get("video_id") or "") == sel_id
                ),
                None,
            )
            if row is not None:
                preview_request_id["value"] += 1
                request_id = int(preview_request_id["value"])
                defer_preview = bool(self._ai_settings.get("defer_local_picker_preview", True))
                if defer_preview:
                    self._render_video_preview_placeholder(
                        row,
                        image_label=preview["image_label"],
                        title_var=preview["title_var"],
                        creator_var=preview["creator_var"],
                        genre_var=preview["genre_var"],
                        description_widget=preview["description"],
                        photo_ref=preview["photo_ref"],
                        loading=True,
                    )

                    def _worker(target_row: dict[str, Any], ticket: int) -> None:
                        try:
                            payload = self._video_preview_payload(target_row)
                        except Exception:
                            payload = {}

                        def _apply() -> None:
                            if (
                                ticket != int(preview_request_id["value"])
                                or not popup.winfo_exists()
                                or _selected_video_id() != str(target_row.get("video_id") or "")
                            ):
                                return
                            self._render_video_preview(
                                target_row,
                                image_label=preview["image_label"],
                                title_var=preview["title_var"],
                                creator_var=preview["creator_var"],
                                genre_var=preview["genre_var"],
                                description_widget=preview["description"],
                                photo_ref=preview["photo_ref"],
                                image_size=preview["image_size"],
                            )

                        try:
                            self.root.after(0, _apply)
                        except Exception:
                            return

                    threading.Thread(
                        target=_worker,
                        args=(dict(row), request_id),
                        daemon=True,
                    ).start()
                else:
                    self._render_video_preview(
                        row,
                        image_label=preview["image_label"],
                        title_var=preview["title_var"],
                        creator_var=preview["creator_var"],
                        genre_var=preview["genre_var"],
                        description_widget=preview["description"],
                        photo_ref=preview["photo_ref"],
                        image_size=preview["image_size"],
                    )

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            selected_video_id = _selected_video_id()
            _refresh_header()
            _configure_tabs()
            self._video_picker_results = []
            rows = self._search_video_titles_with_query(query, limit=300)
            self._video_picker_results = self._sort_picker_rows([dict(r) for r in rows], "video_picker")
            _render_rows(query)
            if self._video_picker_results:
                found_idx = next(
                    (
                        idx
                        for idx, row in enumerate(self._video_picker_results)
                        if str(row.get("video_id") or "") == selected_video_id
                    ),
                    0,
                )
                _set_selection(found_idx)
            status_var.set(f"{len(self._video_picker_results)} rows")

        def open_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            video_id = _selected_video_id()
            if not video_id:
                return "break"
            row = next(
                (
                    item
                    for item in self._video_picker_results
                    if str(item.get("video_id") or "") == video_id
                ),
                None,
            )
            if row is None:
                return "break"
            transcript_raw = str(row.get("transcript_json_path") or "").strip()
            transcript_path = Path(transcript_raw) if transcript_raw else None
            preferred = Path(str(row.get("local_video_path") or "")) if row.get(
                "local_video_path") else None
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
            _set_selection(selected_index["value"] + delta)
            return "break"

        def delete_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            video_id = _selected_video_id()
            if not video_id:
                status_var.set("No video selected")
                return "break"
            row = next(
                (
                    item
                    for item in self._video_picker_results
                    if str(item.get("video_id") or "") == video_id
                ),
                None,
            )
            if row is None:
                status_var.set("Invalid selection")
                return "break"
            if pending_delete.get("video_id") != video_id:
                pending_delete["video_id"] = video_id
                status_var.set(f"Press Delete again to remove {video_id}")
                return "break"
            pending_delete["video_id"] = None
            try:
                summary = self.ingester.delete_video_and_assets(video_id)
                status_var.set(
                    f"Deleted {video_id}: files={
                        summary.get('deleted_files', 0)} "
                    f"jobs={summary.get('jobs_deleted', 0)}"
                )
                if self.current_video_id == video_id:
                    self._clear_loaded_session(
                        "Deleted currently loaded video")
                refresh_results()
            except Exception as exc:
                status_var.set(f"Delete failed: {exc}")
            return "break"

        def queue_transcribe_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            video_id = _selected_video_id()
            if not video_id:
                status_var.set("No video selected")
                return "break"
            try:
                result = self.ingester.enqueue_video_for_transcription(video_id)
                if not bool(result.get("queued", False)):
                    status_var.set(f"Transcript queue: {result.get('reason')}")
                else:
                    status_var.set(f"Queued transcript job {result.get('job_id')} for {video_id}")
            except Exception as exc:
                status_var.set(f"Transcript queue failed: {exc}")
            return "break"

        def queue_summary_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            video_id = _selected_video_id()
            if not video_id:
                status_var.set("No video selected")
                return "break"
            try:
                result = self.ingester.enqueue_video_for_summary(video_id)
                if not bool(result.get("queued", False)):
                    status_var.set(f"Summary queue: {result.get('reason')}")
                else:
                    status_var.set(f"Queued summary job {result.get('job_id')} for {video_id}")
            except Exception as exc:
                status_var.set(f"Summary queue failed: {exc}")
            return "break"

        query_var.trace_add("write", refresh_results)
        self._attach_picker_fields_menu(
            fields_button,
            picker_name="video_picker",
            on_change=refresh_results,
        )
        self._bind_popup_close(
            popup,
            after_close=lambda: setattr(self, "_video_picker_popup", None),
        )
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list],
            on_enter=lambda: open_selected(),
            on_up=lambda: move_sel(-1),
            on_down=lambda: move_sel(1),
            on_home=lambda: move_sel(-10_000),
            on_end=lambda: move_sel(10_000),
            on_page_up=lambda: move_sel(-10),
            on_page_down=lambda: move_sel(10),
        )
        title_list.bind(
            "<ButtonRelease-1>",
            lambda e: _set_selection(max(0, int(str(title_list.index(f"@{e.x},{e.y}")).split(".", 1)[0]) - 1)),
            add="+",
        )
        title_list.bind("<Double-Button-1>", open_selected)
        title_list.bind("<Delete>", delete_selected)
        title_list.bind("<Control-r>", queue_transcribe_selected)
        title_list.bind("<Control-y>", queue_summary_selected)
        popup.bind("<Delete>", delete_selected)
        popup.bind("<Control-r>", queue_transcribe_selected)
        popup.bind("<Control-y>", queue_summary_selected)
        refresh_results()
        query_entry.focus_set()

    def _open_queue_picker_popup(self, queue_stage: str) -> None:
        stage = str(queue_stage or "transcribe").strip().lower()
        if stage not in {"transcribe", "summarize"}:
            stage = "transcribe"
        if self._jobs_popup and self._jobs_popup.winfo_exists():
            self._close_jobs_popup()
        popup = self._create_popup_window(
            name="queue_picker",
            title=f"Queue Videos: {stage.title()}",
            size=POPUP_SIZES["PICKER"],
            attr_name="_queue_picker_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return
        content = self._popup_content(popup)

        query_var = tk.StringVar(value="")
        query_entry = self._create_query_entry(
            content,
            query_var,
            enable_field_completion=True,
        )
        query_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        header_row = tk.Frame(content, bg=self._theme_color("SURFACE_BG"))
        header_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        header_row.columnconfigure(0, weight=1)
        header = tk.Label(
            header_row,
            text=f"Queue {stage.title()}",
            anchor="w",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_MUTED"),
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            padx=8,
            pady=4,
        )
        header.grid(row=0, column=0, sticky="ew")
        fields_button = tk.Menubutton(
            header_row,
            text="Fields",
            direction="below",
            bg=self._theme_color("SURFACE_BG"),
            fg=self._theme_color("FG_SOFT"),
            activebackground=self._theme_color("SELECT_BG"),
            activeforeground=self._theme_color("FG"),
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=4,
            font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
        )
        fields_button.grid(row=0, column=1, sticky="e")

        body = tk.Frame(content, bg=self._theme_color("APP_BG"))
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=360)
        body.columnconfigure(1, weight=1)

        preview = self._build_video_preview_pane(body)
        preview["frame"].grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        title_list = PickerTable(
            body,
            columns=self._picker_columns_for("queue_picker"),
            bg=self._theme_color("PANEL_BG"),
            fg=self._theme_color("FG"),
            muted_fg=self._theme_color("FG_MUTED"),
            border=self._theme_color("BORDER"),
            select_bg=self._theme_color("SELECTED_ROW_BG"),
            select_fg=self._theme_color("FG"),
            retained_bg=self._darken_hex_color(self._theme_color("SELECTED_ROW_BG")),
            retained_fg=self._theme_color("FG"),
            font=self._ui_font(FONT_SIZE_OFFSETS["BODY"]),
            heading_font=self._ui_font(FONT_SIZE_OFFSETS["SMALL"]),
            on_widths_changed=lambda widths: self._set_picker_field_widths("queue_picker", widths),
            on_heading_click=lambda field: (self._cycle_picker_sort_field("queue_picker", field, reverse=False), refresh_results()),
            on_heading_right_click=lambda field: (self._cycle_picker_sort_field("queue_picker", field, reverse=True), refresh_results()),
        )
        title_list.grid(row=0, column=1, sticky="nsew")

        status_var = tk.StringVar(value=self._status_hint("queue_picker"))
        self._create_status_label(
            content,
            status_var,
            fg=self._theme_color("FG_SOFT"),
            font_delta=-3,
        ).grid(row=3, column=0, sticky="ew")

        selected_indexes: set[int] = set()
        current_index = {"value": 0}
        rows_state: list[dict[str, Any]] = []

        def _apply_state() -> None:
            if rows_state:
                idx = max(0, min(current_index["value"], len(rows_state) - 1))
                current_index["value"] = idx
                current_video_id = str(rows_state[idx].get("video_id") or "")
                title_list.select_item_id(current_video_id)
                title_list.set_retained(
                    {
                        str(rows_state[row_idx].get("video_id") or "")
                        for row_idx in selected_indexes
                        if 0 <= row_idx < len(rows_state)
                    }
                )
                self._render_video_preview(
                    rows_state[idx],
                    image_label=preview["image_label"],
                    title_var=preview["title_var"],
                    creator_var=preview["creator_var"],
                    genre_var=preview["genre_var"],
                    description_widget=preview["description"],
                    photo_ref=preview["photo_ref"],
                    image_size=preview["image_size"],
                )

        def _move_cursor(delta: int, *, add_current: bool = False) -> str:
            if not rows_state:
                return "break"
            cur = current_index["value"]
            if add_current:
                selected_indexes.add(cur)
            nxt = max(0, min(cur + delta, len(rows_state) - 1))
            current_index["value"] = nxt
            _apply_state()
            return "break"

        def _queue_selected(_event: tk.Event[tk.Misc] | None = None) -> str:
            if not rows_state:
                status_var.set("No rows available")
                return "break"
            targets = set(selected_indexes)
            targets.add(current_index["value"])
            queued = 0
            skipped = 0
            for idx in sorted(targets):
                if idx < 0 or idx >= len(rows_state):
                    continue
                video_id = str(rows_state[idx].get("video_id") or "").strip()
                if not video_id:
                    skipped += 1
                    continue
                try:
                    if stage == "transcribe":
                        result = self.ingester.enqueue_video_for_transcription(video_id)
                    else:
                        result = self.ingester.enqueue_video_for_summary(video_id)
                    if bool(result.get("queued", False)):
                        queued += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1
            status_var.set(f"Queued {queued} to {stage}; skipped {skipped}")
            return "break"

        def refresh_results(*_args: object) -> None:
            query = query_var.get().strip()
            previous_video_id = title_list.selected_item_id()
            title_list.configure_columns(self._picker_columns_for("queue_picker"))
            rows_state.clear()
            rows_state.extend(
                self._sort_picker_rows(
                    [dict(r) for r in self._search_video_titles_with_query(query, limit=300)],
                    "queue_picker",
                )
            )
            selected_indexes.clear()
            current_index["value"] = 0
            title_list.replace_rows(
                [
                    self._picker_row_values(row, "queue_picker")
                    for row in rows_state
                ]
            )
            for row_idx, row in enumerate(rows_state):
                if previous_video_id and str(row.get("video_id") or "") == previous_video_id:
                    current_index["value"] = row_idx
            _apply_state()
            status_var.set(f"{len(rows_state)} rows | stage={stage}")

        def _select_clicked(event: tk.Event[tk.Misc]) -> str:
            item_id = title_list.item_id_at_y(int(event.y))
            if not item_id:
                return "break"
            current_index["value"] = next(
                (
                    idx
                    for idx, row in enumerate(rows_state)
                    if str(row.get("video_id") or "") == item_id
                ),
                0,
            )
            if current_index["value"] in selected_indexes:
                selected_indexes.remove(current_index["value"])
            _apply_state()
            return "break"

        query_var.trace_add("write", refresh_results)
        self._attach_picker_fields_menu(
            fields_button,
            picker_name="queue_picker",
            on_change=refresh_results,
        )
        self._bind_popup_close(
            popup,
            after_close=lambda: setattr(self, "_queue_picker_popup", None),
        )
        title_list.bind("<Button-1>", _select_clicked)
        popup.bind("<Return>", _queue_selected)
        title_list.bind("<Return>", _queue_selected)
        popup.bind("<Control-Up>", lambda _e: _move_cursor(-1, add_current=True))
        popup.bind("<Control-Down>", lambda _e: _move_cursor(1, add_current=True))
        popup.bind("<Control-Prior>", lambda _e: _move_cursor(-10, add_current=True))
        popup.bind("<Control-Next>", lambda _e: _move_cursor(10, add_current=True))
        popup.bind("<Control-Home>", lambda _e: _move_cursor(-10_000, add_current=True))
        popup.bind("<Control-End>", lambda _e: _move_cursor(10_000, add_current=True))
        popup.bind("<Shift-Up>", lambda _e: _move_cursor(-1, add_current=False))
        popup.bind("<Shift-Down>", lambda _e: _move_cursor(1, add_current=False))
        popup.bind("<Shift-Prior>", lambda _e: _move_cursor(-10, add_current=False))
        popup.bind("<Shift-Next>", lambda _e: _move_cursor(10, add_current=False))
        popup.bind("<Shift-Home>", lambda _e: _move_cursor(-10_000, add_current=False))
        popup.bind("<Shift-End>", lambda _e: _move_cursor(10_000, add_current=False))
        self._bind_fzf_like_keys(
            popup,
            entry=query_entry,
            capture_widgets=[query_entry, title_list],
            on_enter=lambda: _queue_selected(),
            on_up=lambda: _move_cursor(-1),
            on_down=lambda: _move_cursor(1),
            on_home=lambda: _move_cursor(-10_000),
            on_end=lambda: _move_cursor(10_000),
            on_page_up=lambda: _move_cursor(-10),
            on_page_down=lambda: _move_cursor(10),
        )
        refresh_results()
        query_entry.focus_set()

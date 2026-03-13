from __future__ import annotations

import concurrent.futures
import io
import threading
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import ttk
from typing import Any

try:
    from PIL import Image, ImageOps, ImageTk
except Exception:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

from alog.pipeline import resolve_playback_media_path

from ..constants import FONT, FONT_SIZE_OFFSETS, LAYOUT, POPUP_SIZES, THEME


class ChannelPopupMixin:
    def _youtube_thumbnail_candidates(self, video_id: str) -> list[str]:
        token = str(video_id or "").strip()
        if not token:
            return []
        base = f"https://i.ytimg.com/vi/{token}"
        return [
            f"{base}/maxresdefault.jpg",
            f"{base}/sddefault.jpg",
            f"{base}/hqdefault.jpg",
            f"{base}/mqdefault.jpg",
            f"{base}/default.jpg",
        ]

    def _download_browse_thumbnail(self, video_id: str, thumbnail_url: str) -> Path | None:
        if not video_id or not thumbnail_url:
            return None
        if self._browse_thumb_dir is None:
            return None
        out_path = self._browse_thumb_dir / f"{video_id}.png"
        if out_path.exists():
            return out_path
        candidate_urls: list[str] = []
        seen: set[str] = set()
        for candidate in [*self._youtube_thumbnail_candidates(video_id), thumbnail_url]:
            token = str(candidate or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            candidate_urls.append(token)
        for candidate_url in candidate_urls:
            try:
                req = urllib.request.Request(
                    candidate_url,
                    headers={"User-Agent": "alogger/1.0 (+https://localhost)"},
                )
                with urllib.request.urlopen(req, timeout=6.0) as resp:
                    raw = resp.read()
                if Image is not None:
                    with Image.open(io.BytesIO(raw)) as img:
                        img = img.convert("RGB")
                        img.save(out_path, format="PNG")
                    self._browse_temp_files.add(out_path)
                    return out_path
                raw_path = self._browse_thumb_dir / f"{video_id}.img"
                raw_path.write_bytes(raw)
                self._browse_temp_files.add(raw_path)
                return raw_path
            except Exception:
                continue
        return None

    def _get_browse_preview(
        self,
        row: dict[str, Any],
        *,
        fetch_metadata: bool = True,
    ) -> dict[str, Any]:
        video_id = str(row.get("video_id") or "").strip()
        cache_key = video_id or str(row.get("url") or "").strip()
        if cache_key and cache_key in self._browse_preview_cache:
            cached = dict(self._browse_preview_cache[cache_key])
            has_image = bool(str(cached.get("image_path") or "").strip())
            if has_image:
                return cached

        title = str(row.get("title") or row.get("video_id") or "untitled").strip()
        creator = str(row.get("uploader") or row.get("channel") or "").strip()
        hashtags = self._hashtags_from_text(title)
        thumbnail_url = str(row.get("thumbnail") or "").strip()
        url = str(row.get("url") or "").strip()
        meta_ok = False

        if fetch_metadata and url:
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
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/default.jpg"

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
        if cache_key and (preview["image_path"] or preview["hashtags"] or preview["title"] or preview["creator"]):
            self._browse_preview_cache[cache_key] = dict(preview)
        return preview

    def _open_video_from_row(self, row: dict[str, Any], *, filter_text: str = "") -> tuple[bool, str]:
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            return False, "row has no video_id"
        transcript_raw = str(row.get("transcript_json_path") or "").strip()
        transcript_path = Path(transcript_raw) if transcript_raw else None
        preferred = Path(str(row.get("local_video_path") or "")
                         ) if row.get("local_video_path") else None
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

    def _open_channel_popup(self) -> None:
        popup = self._create_popup_window(
                    name="channel",
                    title="Channel Browser",
                    size=POPUP_SIZES["CHANNEL"],
            attr_name="_channel_popup",
            reuse_existing=True,
            row_weights={2: 1},
            column_weights={0: 1},
        )
        if popup is None:
            return

        channel_ref = str(self._channel_default_ref or "").strip()
        if not channel_ref:
            self.status_var.set(
                "No channel selected. Open Ctrl-N and choose Browse first.")
            self._close_popup_window(popup)
            return

        filter_var = tk.StringVar(value="")
        filter_entry = ttk.Entry(
            popup, textvariable=filter_var, style="Filter.TEntry")
        filter_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        limit_var = tk.StringVar(value="30")
        limit_entry = ttk.Entry(
            popup, textvariable=limit_var, style="Filter.TEntry")
        limit_entry.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

        body = tk.Frame(popup, bg=THEME["APP_BG"])
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=2, minsize=360)
        body.columnconfigure(1, weight=3)
        body.grid_propagate(False)

        preview_frame = tk.Frame(
            body,
            bg=THEME["PANEL_BG"],
            highlightthickness=1,
            highlightbackground=THEME["BORDER"],
            bd=0,
            width=LAYOUT["PREVIEW_WIDTH"],
            height=LAYOUT["PREVIEW_HEIGHT"],
        )
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        preview_frame.grid_propagate(False)
        preview_frame.rowconfigure(0, weight=3)
        preview_frame.rowconfigure(1, weight=0)
        preview_frame.rowconfigure(2, weight=0)
        preview_frame.rowconfigure(3, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        listbox = self._create_listbox(body)
        listbox.grid(row=0, column=1, sticky="nsew")

        preview_image_width = LAYOUT["PREVIEW_IMAGE_WIDTH"]
        preview_image_height = LAYOUT["PREVIEW_IMAGE_HEIGHT"]
        preview_image_box = tk.Frame(
            preview_frame,
            bg=THEME["PANEL_BG"],
            width=preview_image_width,
            height=preview_image_height,
            highlightthickness=0,
            bd=0,
        )
        preview_image_box.grid(
            row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        preview_image_box.grid_propagate(False)
        preview_image_box.rowconfigure(0, weight=1)
        preview_image_box.columnconfigure(0, weight=1)

        preview_image_lbl = tk.Label(
            preview_image_box,
            bg=THEME["PANEL_BG"],
            fg=THEME["FG_MUTED"],
            text="Preview loading...",
            anchor="center",
            justify="center",
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"]),
        )
        preview_image_lbl.grid(row=0, column=0, sticky="nsew")
        preview_title_var = tk.StringVar(value="")
        preview_creator_var = tk.StringVar(value="")
        preview_tags_var = tk.StringVar(value="")
        preview_title_lbl = tk.Label(
            preview_frame,
            textvariable=preview_title_var,
            anchor="w",
            justify="left",
            bg=THEME["PANEL_BG"],
            fg=THEME["FG"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["BODY"], "bold"),
            wraplength=320,
        )
        preview_title_lbl.grid(
            row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        preview_creator_lbl = tk.Label(
            preview_frame,
            textvariable=preview_creator_var,
            anchor="w",
            justify="left",
            bg=THEME["PANEL_BG"],
            fg=THEME["FG_SOFT"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            wraplength=320,
        )
        preview_creator_lbl.grid(
            row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        preview_tags_lbl = tk.Label(
            preview_frame,
            textvariable=preview_tags_var,
            anchor="nw",
            justify="left",
            bg=THEME["PANEL_BG"],
            fg=THEME["FG_INFO"],
            font=(FONT["STYLE"], FONT["SIZE"] + FONT_SIZE_OFFSETS["SMALL"]),
            wraplength=320,
        )
        preview_tags_lbl.grid(
            row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        preview_photo: dict[str, Any] = {"image": None}

        status_var = tk.StringVar(
            value=(
                f"Channel: {channel_ref} | Type to filter videos | Enter queues selected | "
                "Ctrl-R reload | Ctrl-S subscribe"
            )
        )
        status_lbl = self._create_status_label(
            popup,
            status_var,
            font_delta=-3,
        )
        status_lbl.grid(row=3, column=0, sticky="ew")

        all_rows: list[dict[str, Any]] = []
        filtered_positions: list[int] = []
        preview_seq: dict[str, int] = {"value": 0}
        prefetch_seq: dict[str, int] = {"value": 0}

        def _set_preview_placeholders(title: str = "", creator: str = "", tags: str = "") -> None:
            preview_title_var.set(title)
            preview_creator_var.set(creator)
            preview_tags_var.set(tags)
            preview_photo["image"] = None
            preview_image_lbl.configure(image="", text="No preview")

        def _render_preview_row(row: dict[str, Any], preview: dict[str, Any], expected_seq: int) -> None:
            if not popup.winfo_exists() or expected_seq != preview_seq["value"]:
                return
            if expected_seq != preview_seq["value"] or not popup.winfo_exists():
                return
            preview_title_var.set(str(preview.get("title") or "untitled"))
            preview_creator_var.set(
                f"creator: {preview.get('creator') or 'unknown'}")
            tags = list(preview.get("hashtags") or [])
            preview_tags_var.set(" ".join(str(t)
                                 for t in tags) if tags else "(no hashtags)")
            image_path = str(preview.get("image_path") or "").strip()
            if image_path and Image is not None and ImageTk is not None:
                try:
                    with Image.open(image_path) as img:
                        img = img.convert("RGB")
                        target_w = preview_image_width
                        target_h = preview_image_height
                        if ImageOps is not None:
                            img = ImageOps.fit(
                                img, (target_w, target_h), method=Image.Resampling.LANCZOS)
                        else:
                            img = img.resize(
                                (target_w, target_h), Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                    preview_photo["image"] = photo
                    preview_image_lbl.configure(image=photo, text="")
                except Exception:
                    preview_photo["image"] = None
                    preview_image_lbl.configure(
                        image="", text="Preview unavailable")
            elif image_path:
                try:
                    photo = tk.PhotoImage(file=image_path)
                    preview_photo["image"] = photo
                    preview_image_lbl.configure(image=photo, text="")
                except Exception:
                    preview_photo["image"] = None
                    preview_image_lbl.configure(
                        image="", text="Preview unavailable")
            else:
                preview_photo["image"] = None
                preview_image_lbl.configure(
                    image="", text="Preview unavailable")

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
            preview_title_var.set(
                str(row.get("title") or video_id or "untitled"))
            preview_creator_var.set(
                f"creator: {row.get('uploader') or row.get('channel') or 'unknown'}")
            preview_seq["value"] += 1
            seq = int(preview_seq["value"])
            cache_key = video_id or str(row.get("url") or "").strip()
            cached_preview = self._browse_preview_cache.get(cache_key) if cache_key else None
            if isinstance(cached_preview, dict) and str(cached_preview.get("image_path") or "").strip():
                _render_preview_row(row, dict(cached_preview), seq)
                return
            preview_tags_var.set("loading metadata...")
            preview_image_lbl.configure(image="", text="Loading preview...")

            def _work() -> None:
                try:
                    preview = self._get_browse_preview(
                        row, fetch_metadata=True)
                except Exception as exc:
                    preview = {
                        "title": str(row.get("title") or video_id or "untitled"),
                        "creator": str(row.get("uploader") or row.get("channel") or "unknown"),
                        "hashtags": [],
                        "image_path": "",
                        "error": str(exc),
                    }
                self.root.after(
                    0, lambda: _render_preview_row(row, preview, seq))

            threading.Thread(target=_work, daemon=True,
                             name="alog-browse-preview").start()

        def _apply_filter(*_args: object) -> str:
            selected_video_id = ""
            sel = listbox.curselection()
            if sel and filtered_positions:
                shown_idx = int(sel[0])
                if 0 <= shown_idx < len(filtered_positions):
                    selected_video_id = str(
                        all_rows[filtered_positions[shown_idx]].get("video_id") or ""
                    ).strip()
            query = filter_var.get().strip().lower()
            filtered_positions.clear()
            listbox.delete(0, tk.END)
            selected_pos = -1
            for idx, row in enumerate(all_rows):
                title = str(row.get("title") or row.get("video_id")
                            or "untitled").replace("\n", " ").strip()
                creator = str(row.get("uploader") or row.get("channel") or "")
                hay = f"{title} {creator} {row.get('video_id') or ''}".lower()
                if query and query not in hay:
                    continue
                filtered_positions.append(idx)
                listbox.insert(tk.END, title)
                row_video_id = str(row.get("video_id") or "").strip()
                if selected_video_id and row_video_id == selected_video_id:
                    selected_pos = len(filtered_positions) - 1
            if filtered_positions:
                self._set_listbox_selection(
                    [listbox],
                    selected_pos if selected_pos >= 0 else 0,
                    len(filtered_positions),
                )
                _refresh_preview_async()
            else:
                _set_preview_placeholders()
            status_var.set(
                f"Channel: {channel_ref} | {
                    len(filtered_positions)}/{len(all_rows)} videos shown | "
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
                data = self.ingester.list_channel_videos(
                    channel_ref, limit=limit)
                all_rows.clear()
                all_rows.extend(dict(row)
                                for row in (data.get("entries") or []))
                self._channel_results = list(all_rows)
                prefetch_seq["value"] += 1
                current_prefetch = int(prefetch_seq["value"])
                rows_to_prefetch = [dict(row) for row in all_rows[:30]]
                status_var.set(
                    f"Channel: {channel_ref} | preloading {len(rows_to_prefetch)} previews..."
                )
                _apply_filter()
                if all_rows:
                    listbox.focus_set()
                if rows_to_prefetch:
                    def _prefetch_worker(rows_snapshot: list[dict[str, Any]], token: int) -> None:
                        done = 0

                        def _one(row_data: dict[str, Any]) -> None:
                            nonlocal done
                            try:
                                self._get_browse_preview(row_data, fetch_metadata=False)
                            except Exception:
                                pass
                            done += 1
                            if not popup.winfo_exists() or token != prefetch_seq["value"]:
                                return
                            if done == 1 or done % 5 == 0 or done == len(rows_snapshot):
                                self.root.after(
                                    0,
                                    lambda done_count=done: (
                                        status_var.set(
                                            f"Channel: {channel_ref} | "
                                            f"preloaded {done_count}/{len(rows_snapshot)} previews"
                                        ),
                                        _refresh_preview_async(),
                                    ),
                                )
                        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                            futures = [executor.submit(
                                _one, row_data) for row_data in rows_snapshot]
                            for future in concurrent.futures.as_completed(futures):
                                try:
                                    future.result()
                                except Exception:
                                    continue

                    threading.Thread(
                        target=_prefetch_worker,
                        args=(rows_to_prefetch, current_prefetch),
                        daemon=True,
                        name="alog-browse-prefetch",
                    ).start()
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
                sub = self.ingester.add_channel_subscription(
                    channel_ref, seed_with_latest=True)
                status_var.set(
                    f"Subscribed: {sub.get('channel_title')} ({
                        sub.get('channel_key')})"
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
            self._set_listbox_selection(
                [listbox], nxt, len(filtered_positions))
            _refresh_preview_async()
            return "break"

        self._bind_popup_close(popup)
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
        filter_var.trace_add("write", _apply_filter)
        refresh_channel_videos()
        filter_entry.focus_set()

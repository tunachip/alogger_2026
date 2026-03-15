from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import vlc

from alog.pipeline import (
    _media_has_audio_stream,
    _media_has_video_stream,
)

from .utils import format_hms as _fmt_hms


class PlaybackMixin:
    def _build_vlc(self) -> None:
        self.instance = vlc.Instance(
            "--quiet",
            "--no-video-title-show",
            "--avcodec-hw=none",
        )
        if not self.instance:
            raise Exception("self.instance failed to load.")
        self.player = self.instance.media_player_new()
        if self.video_path:
            if not self.video_path.exists():
                raise FileNotFoundError(
                    f"video path not found: {self.video_path}")
            self._set_player_media(
                self.video_path,
                self.audio_path,
                self.start_sec)

    def _set_player_media(
        self,
        video_path: Path,
        audio_path: Path | None,
        start_sec: float = 0.0
    ) -> None:
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
        self._bind_video_output(self.video_panel.winfo_id())
        self.player.play()
        self._startup_poll_count = 0
        self.root.after(
            350,
            lambda: self._post_media_load(
                start_sec,
                retry_without_audio=audio_path is not None
            ))

    def _bind_video_output(self, handle: int) -> None:
        if sys.platform.startswith("linux"):
            self.player.set_xwindow(handle)
            return
        if sys.platform == "win32":
            self.player.set_hwnd(handle)
            return
        if sys.platform == "darwin":
            self.player.set_nsobject(handle)

    def _post_media_load(
        self,
        start_sec: float,
        *,
        retry_without_audio: bool
    ) -> None:
        state = self.player.get_state()
        match state:
            case \
                    vlc.State.Opening | \
                    vlc.State.Buffering | \
                    vlc.State.NothingSpecial:
                if self._startup_poll_count < 8:
                    self._startup_poll_count += 1
                    self.root.after(
                        250,
                        lambda: self._post_media_load(
                            start_sec,
                            retry_without_audio=retry_without_audio
                        ))
                    return
            case vlc.State.Stopped:
                if self._startup_poll_count < 3:
                    self._startup_poll_count += 1
                    self.player.play()
                    self.root.after(
                        250,
                        lambda: self._post_media_load(
                            start_sec,
                            retry_without_audio=retry_without_audio
                        ))
                    return
            case vlc.State.Ended | vlc.State.Error | vlc.State.Stopped:
                if retry_without_audio and self.audio_path is not None:
                    self.status_var.set(
                        "Media failed with sidecar audio, trying video-only..."
                    )
                    self._set_player_media(
                        self.video_path,
                        None,
                        start_sec=start_sec
                    )
                    return
                alt = self._pick_alternate_video_path()
                if alt is not None:
                    self._load_fail_count += 1
                    self.status_var.set(
                        f"Media load failed ({
                            self.video_path.name
                        }, {
                            state
                        }); trying {
                            alt.name
                        }..."
                    )
                    self._set_player_media(
                        alt,
                        None,
                        start_sec=start_sec
                    )
                    return
                if not self._proxy_attempted:
                    proxy = self._generate_proxy_playback(
                        self.video_path,
                        self.audio_path
                    )
                    if proxy is not None and proxy.exists():
                        self._proxy_attempted = True
                        self.status_var.set(
                            f"Retrying with compatibility proxy: {proxy.name}"
                        )
                        self._set_player_media(
                            proxy,
                            None,
                            start_sec=start_sec
                        )
                        return
                _v = self.video_path
                _s = state
                self.status_var.set(f"Failed to load media: {_v} (state={_s})")
                return
        if start_sec > 0:
            self.player.set_time(int(start_sec * 1000.0))
        self.player.set_pause(0)
        self.status_var.set("Ready")
        self._load_fail_count = 0
        self._startup_poll_count = 0

    def _generate_proxy_playback(
        self,
        video_path: Path,
        audio_path: Path | None
    ) -> Path | None:
        if not self.current_video_id:
            return None
        proxy_path = self.ingester_config.media_dir \
            / f"{self.current_video_id}.proxy.mp4"
        cmd: list[str] = [
            self.ingester_config.ffmpeg_binary,
            "-y",
            "-i",
            str(video_path)
        ]
        if audio_path and audio_path.exists():
            cmd.extend([
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0"
            ])
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
        if proc.returncode != 0 or not proxy_path.exists():
            return None
        return proxy_path

    def _pick_alternate_video_path(self) -> Path | None:
        if not self.current_video_id or self._load_fail_count >= 2:
            return None
        candidates: list[Path] = []
        for path in sorted(
            self.ingester_config.media_dir.glob(
                f"{self.current_video_id}*")
        ):
            if not path.is_file() or path == self.video_path:
                continue
            if _media_has_video_stream(path) is True:
                candidates.append(path)
        if not candidates:
            return None
        ext_rank = {
            ".mkv": 5,
            ".mp4": 4,
            ".webm": 3,
            ".mov": 2,
            ".m4v": 2
        }
        candidates.sort(
            key=lambda path: (
                ext_rank.get(path.suffix.lower(), 0),
                path.stat().st_size
            ),
            reverse=True,
        )
        return candidates[0]

    def _find_audio_sidecar(
        self,
        video_id: str,
        video_path: Path
    ) -> Path | None:
        if _media_has_audio_stream(video_path) is True:
            return None
        audio_only: list[Path] = []
        for path in sorted(
            self.ingester_config.media_dir.glob(f"{video_id}*")
        ):
            if not path.is_file() or path == video_path:
                continue
            has_audio = _media_has_audio_stream(path)
            has_video = _media_has_video_stream(path)
            if has_audio is True and has_video is False:
                audio_only.append(path)
        if not audio_only:
            return None
        return sorted(
            audio_only,
            key=lambda path: path.stat().st_size,
            reverse=True
        )[0]

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
        self.segments = (
            self._load_segments(transcript_json)
            if transcript_json and transcript_json.exists()
            else []
        )
        self._segment_starts = [segment.start_sec for segment in self.segments]
        self.filtered_indexes = list(range(len(self.segments)))
        self.selected_filtered_pos = 0
        self._set_player_media(video_path, audio_path, start_sec=start_sec)
        try:
            self.player.set_rate(float(getattr(self, "_playback_rate", 1.0)))
        except Exception:
            pass
        self.filter_var.set(filter_text)
        if not filter_text:
            self._refresh_caption_view()
        self._refresh_transport_controls()
        self._refresh_details_pane()
        _t = _fmt_hms(start_sec)
        if self.segments:
            self.status_var.set(
                f"Loaded video at {_t}")
        else:
            self.status_var.set(
                f"Loaded video at {_t} (transcript not ready yet)")

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
        self._refresh_details_pane()
        self._refresh_transport_controls()
        try:
            self.player.stop()
        except Exception:
            pass
        self.status_var.set(message)

    def close(self) -> None:
        self._close_jobs_popup()
        for attr in (
            "_search_popup",
            "_video_picker_popup",
            "_ingest_popup",
            "_channel_popup",
            "_subscriptions_popup",
        ):
            popup = getattr(self, attr, None)
            if popup and popup.winfo_exists():
                popup.destroy()
        for preview_path in list(getattr(self, "_browse_temp_files", set())):
            try:
                preview_path.unlink(missing_ok=True)
            except Exception:
                pass
        if getattr(self, "_browse_thumb_dir", None) is not None:
            try:
                self._browse_thumb_dir.rmdir()
            except Exception:
                pass
        try:
            self.ingester.stop_background_workers()
            self.player.stop()
            if (
                self._ollama_started_by_app
                and self._ollama_proc
                and self._ollama_proc.poll() is None
            ):
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
        try:
            self.root.mainloop()
        finally:
            try:
                self.player.stop()
            except Exception:
                pass

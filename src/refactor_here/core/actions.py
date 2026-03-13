from __future__ import annotations
from dataclasses import replace
from .entities import (
    DownloadRequest,
    DownloadDefaults,
    TranscribeDefaults,
    TranscribeRequest,
)
from .ports import Downloader, Transcriber, MediaCatalog

# === Downloading ===

def resolve_download_options(
    defaults: DownloadDefaults,
    configured: DownloadDefaults | None,
    request: DownloadRequest
) -> DownloadDefaults:
    config = configured or defaults
    return replace(
        defaults,
        output_dir = \
            request.output_dir or \
            config.output_dir,
        format_selector = \
            request.format_selector or \
            config.format_selector,
        sort_selector = \
            request.sort_selector or \
            config.sort_selector,
        merge_output_format = \
            request.merge_output_format or \
            config.merge_output_format,
        write_info_json = (
            request.write_info_json
            if request.write_info_json is not None
            else config.write_info_json),
        retries = (
            request.retries
            if request.retries is not None
            else config.retries),
        yt_dlp_bin = config.yt_dlp_bin,
        ffmpeg_bin = config.ffmpeg_bin,
    )

class DownloadVideos:
    def __init__(
        self,
        downloader: Downloader,
        defaults: DownloadDefaults,
        configured: DownloadDefaults | None = None,
    ) -> None:
        self._downloader = downloader
        self._defaults = defaults
        self._configured = configured

    def execute(self, request: DownloadRequest):
        resolved = resolve_download_options(self._defaults, self._configured, request)
        return self._downloader.download(request, resolved)

# === Transcribing ===

def resolve_transcribe_options(
    defaults: TranscribeDefaults,
    configured: TranscribeDefaults | None,
    request: TranscribeRequest
) -> TranscribeDefaults:
    config = configured or defaults
    return replace(
        defaults,
        model = \
            request.model or \
            config.model,
        language = \
            request.language or \
            config.language,
        model_dir = (
            request.model_dir
            if request.model_dir is not None
            else config.model_dir),
        output_dir = \
            request.output_dir or \
            config.output_dir,
        whisper_bin = \
            config.whisper_bin,
        output_format = \
            config.output_format
    )

class TranscribeVideos:
    def __init__(
        self,
        transcriber: Transcriber,
        defaults: TranscribeDefaults,
        configured: TranscribeDefaults | None = None,
    ) -> None:
        self._transcriber = transcriber
        self._defaults = defaults
        self._configured = configured

    def execute(self, request: TranscribeRequest):
        resolved = resolve_transcribe_options(self._defaults, self._configured, request)
        return self._transcriber.transcribe(request, resolved)

# === Ingesting ===

class IngestFromUrls:
    def __init__(
        self,
        *,
        catalog: MediaCatalog,
        downloader: Downloader,
        transcriber: Transcriber | None,
        download_defaults: DownloadDefaults,
        transcribe_defaults: TranscribeDefaults | None = None,
    ) -> None:
        self._catalog = catalog
        self._download = DownloadVideos(downloader, download_defaults)
        self._transcribe = (
            TranscribeVideos(transcriber, transcribe_defaults)
            if transcriber is not None \
            and transcribe_defaults is not None
            else None
        )

    def execute(
        self,
        request: DownloadRequest,
        *,
        transcribe: bool = True
    ) -> dict[str, object]:
        record_by_url: dict[str, int] = {}
        for url in request.urls:
            record_id = self._catalog.create_from_url(
                video_url=url,
                queue_download=True,
                queue_transcribe=bool(transcribe),
            )
            record_by_url[url] = record_id

        download_result = self._download.execute(request)
        record_by_video_id: dict[str, int] = {}

        for item in download_result.items:
            record_id = record_by_url.get(item.url)
            if record_id is None:
                continue
            self._catalog.mark_download_done(
                record_id,
                video_path=str(item.media_path)
            )
            record_by_video_id[item.video_id] = record_id

        for url, reason in download_result.failed:
            record_id = record_by_url.get(url)
            if record_id is None:
                continue
            self._catalog.mark_download_failed(
                record_id,
                error_text=reason
            )
            if transcribe:
                self._catalog.mark_transcribe_skipped(
                    record_id,
                    reason="download_failed",
                )

        if not transcribe:
            for item in download_result.items:
                record_id = record_by_url.get(item.url)
                if record_id is None:
                    continue
                self._catalog.mark_transcribe_skipped(
                    record_id,
                    reason="transcription_disabled"
                )
            return {
                "download": download_result,
                "transcribe": None,
                "record_by_url": record_by_url,
            }

        if self._transcribe is None:
            for item in download_result.items:
                record_id = record_by_url.get(item.url)
                if record_id is None:
                    continue
                self._catalog.mark_transcribe_failed(
                    record_id,
                    error_text="transcriber_not_configured",
                )
            return {
                "download": download_result,
                "transcribe": None,
                "record_by_url": record_by_url,
            }

        transcribe_request = TranscribeRequest(media=download_result.items)
        transcribe_result = self._transcribe.execute(transcribe_request)

        for item in transcribe_result.items:
            record_id = record_by_video_id.get(item.video_id)
            if record_id is None:
                continue
            self._catalog.mark_transcribe_done(
                record_id,
                transcript_path=str(item.transcript_json_path),
            )
            self._catalog.replace_transcript_segments(
                record_id,
                str(item.transcript_json_path),
            )

        for video_id, reason in transcribe_result.failed:
            record_id = record_by_video_id.get(video_id)
            if record_id is None:
                continue
            self._catalog.mark_transcribe_failed(record_id, error_text=reason)

        return {
            "download": download_result,
            "transcribe": transcribe_result,
            "record_by_url": record_by_url,
        }

# === Querying ===

class QueryTranscript:
    def __init__(self, *, catalog: MediaCatalog) -> None:
        self._catalog = catalog

    def execute(
        self,
        query: str,
        *,
        limit: int = 100,
        record_id: int | None = None,
    ) -> list:
        return self._catalog.search_transcript_text(
            query=query,
            limit=limit,
            record_id=record_id,
        )

class GetMediaLocator:
    def __init__(self, *, catalog: MediaCatalog) -> None:
        self._catalog = catalog

    def by_id(self, record_id: int) -> dict[str, str | int | None] | None:
        return self._catalog.get_media_locator(record_id)

    def by_input(self, value: str) -> dict[str, str | int | None] | None:
        return self._catalog.get_media_locator_by_input(value)

class RankVideosByUsage:
    def __init__(self, *, catalog: MediaCatalog) -> None:
        self._catalog = catalog

    def execute(
        self, *,
        limit: int = 25,
        action: str | None = None,
        since_iso: str | None = None,
    ) -> list:
        return self._catalog.top_videos_by_usage(
            limit=limit,
            action=action,
            since_iso=since_iso,
        )


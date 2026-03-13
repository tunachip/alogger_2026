from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service import IngesterService


class BridgeRuntime:
    def __init__(
        self,
        service: IngesterService,
        *,
        processing_workers: int,
        default_allow_overwrite: bool,
        default_autoplay: bool,
    ) -> None:
        self.service = service
        self.default_allow_overwrite = default_allow_overwrite
        self.default_autoplay = default_autoplay
        self.executor = ThreadPoolExecutor(max_workers=max(1, processing_workers))
        self._shutdown_lock = threading.Lock()
        self._closed = False

    def submit_url(
        self,
        url: str,
        *,
        allow_overwrite: bool | None = None,
        autoplay: bool | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("bridge is shutting down")

        allow_overwrite_value = self.default_allow_overwrite if allow_overwrite is None else bool(allow_overwrite)
        autoplay_value = self.default_autoplay if autoplay is None else bool(autoplay)

        result = self.service.enqueue_with_dedupe([url], allow_overwrite=allow_overwrite_value)
        queued_ids = list(result.get("queued_ids") or [])
        if not queued_ids:
            return {
                "accepted": False,
                "reason": "duplicate_video_id",
                "conflicts": result.get("conflicts") or [],
            }

        job_id = int(queued_ids[0])
        self.executor.submit(self._process_job, job_id, autoplay_value)
        return {
            "accepted": True,
            "job_id": job_id,
            "autoplay": autoplay_value,
        }

    def _process_job(self, job_id: int, autoplay: bool) -> None:
        opened_player = False

        def _progress(stage: str, payload: dict[str, object]) -> None:
            nonlocal opened_player
            if stage != "download_done" or opened_player or not autoplay:
                return
            media_path_raw = str(payload.get("local_video_path") or "").strip()
            if not media_path_raw:
                return
            opened_player = True
            self._open_player(media_path_raw)

        try:
            self.service.process_job_id_with_progress(job_id, worker_id=777, progress_cb=_progress)
        except Exception as exc:
            print({"event": "bridge_job_failed", "job_id": job_id, "error": str(exc)}, flush=True)

    def _open_player(self, media_path: str) -> None:
        env = os.environ.copy()
        cmd = [sys.executable, "-m", "alogger_player", "--video-path", media_path]
        try:
            subprocess.Popen(cmd, env=env)
            print({"event": "bridge_open_player", "video_path": media_path}, flush=True)
        except Exception as exc:
            print({"event": "bridge_open_player_failed", "video_path": media_path, "error": str(exc)}, flush=True)

    def close(self) -> None:
        with self._shutdown_lock:
            if self._closed:
                return
            self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=False)


class BridgeHandler(BaseHTTPRequestHandler):
    runtime: BridgeRuntime | None = None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        raw_len = self.headers.get("Content-Length")
        if not raw_len:
            return {}
        length = int(raw_len)
        if length <= 0:
            return {}
        payload = self.rfile.read(length)
        if not payload:
            return {}
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "service": "alog-bridge"})
            return

        if parsed.path == "/api/open":
            query = parse_qs(parsed.query)
            url = str((query.get("url") or [""])[0]).strip()
            if not url:
                self._send_json(400, {"ok": False, "error": "missing_url"})
                return
            self._handle_submit({"url": url})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/open":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            payload = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": f"invalid_json: {exc}"})
            return
        self._handle_submit(payload)

    def _handle_submit(self, payload: dict[str, Any]) -> None:
        runtime = self.runtime
        if runtime is None:
            self._send_json(500, {"ok": False, "error": "bridge_not_ready"})
            return

        url = str(payload.get("url") or "").strip()
        if not url:
            self._send_json(400, {"ok": False, "error": "missing_url"})
            return

        allow_overwrite = payload.get("allow_overwrite")
        autoplay = payload.get("autoplay")

        try:
            result = runtime.submit_url(
                url,
                allow_overwrite=allow_overwrite if isinstance(allow_overwrite, bool) else None,
                autoplay=autoplay if isinstance(autoplay, bool) else None,
            )
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if not result.get("accepted"):
            self._send_json(409, {"ok": False, **result})
            return

        self._send_json(202, {"ok": True, **result})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_bridge_server(
    service: IngesterService,
    *,
    host: str = "127.0.0.1",
    port: int = 17373,
    processing_workers: int = 2,
    allow_overwrite: bool = False,
    autoplay: bool = True,
) -> None:
    service.init()
    runtime = BridgeRuntime(
        service,
        processing_workers=processing_workers,
        default_allow_overwrite=allow_overwrite,
        default_autoplay=autoplay,
    )
    BridgeHandler.runtime = runtime
    server = ThreadingHTTPServer((host, port), BridgeHandler)

    print(
        json.dumps(
            {
                "event": "bridge_started",
                "host": host,
                "port": port,
                "processing_workers": max(1, processing_workers),
                "allow_overwrite": allow_overwrite,
                "autoplay": autoplay,
                "endpoint": f"http://{host}:{port}/api/open",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        server.server_close()
        runtime.close()

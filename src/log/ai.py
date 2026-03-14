from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


AI_SETTINGS_DEFAULTS: dict[str, Any] = {
    "ai_provider": "ollama",
    "ollama_model": "llama3.2:3b",
    "ollama_base_url": "http://127.0.0.1:11434",
    "api_base_url": "https://api.openai.com",
    "api_key_env": "OPENAI_API_KEY",
    "api_model": "gpt-4o-mini",
    "auto_summary_default": False,
    "summary_segment_limit": 120,
    "summary_instructions_path": "",
}


def merged_ai_settings(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = dict(AI_SETTINGS_DEFAULTS)
    if overrides:
        settings.update(overrides)
    return settings


def ask_ai_text(
    prompt: str,
    settings: Mapping[str, Any] | None = None,
    *,
    timeout: float = 120.0,
    ensure_ollama_ready: Callable[[], None] | None = None,
) -> str:
    cfg = merged_ai_settings(settings)
    provider = str(cfg.get("ai_provider") or "ollama").strip().lower()
    if provider == "ollama":
        if ensure_ollama_ready is not None:
            ensure_ollama_ready()
        base = str(cfg.get("ollama_base_url") or AI_SETTINGS_DEFAULTS["ollama_base_url"]).rstrip("/")
        model = str(cfg.get("ollama_model") or AI_SETTINGS_DEFAULTS["ollama_model"]).strip()
        payload = json.dumps(
            {
                "model": model or AI_SETTINGS_DEFAULTS["ollama_model"],
                "prompt": prompt,
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/generate",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=payload,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return str(body.get("response") or "").strip() or "(empty response)"

    base = str(cfg.get("api_base_url") or AI_SETTINGS_DEFAULTS["api_base_url"]).rstrip("/")
    model = str(cfg.get("api_model") or AI_SETTINGS_DEFAULTS["api_model"]).strip()
    key_env = str(cfg.get("api_key_env") or AI_SETTINGS_DEFAULTS["api_key_env"]).strip()
    api_key = os.getenv(key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing API key env var: {key_env}")
    payload = json.dumps(
        {
            "model": model or AI_SETTINGS_DEFAULTS["api_model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=payload,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return "(no choices)"
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if isinstance(message, dict):
        return str(message.get("content") or "").strip() or "(empty response)"
    return "(invalid response)"


def load_summary_instructions(settings: Mapping[str, Any] | None = None) -> str:
    cfg = merged_ai_settings(settings)
    path_token = str(cfg.get("summary_instructions_path") or "").strip()
    if not path_token:
        return ""
    path = Path(path_token).expanduser()
    if not path.exists():
        raise RuntimeError(f"Summary instructions file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_summary_transcript_text(
    segments: list[dict[str, Any]],
    *,
    segment_limit: int,
) -> str:
    cleaned = [str(seg.get("text") or "").strip() for seg in segments]
    cleaned = [text for text in cleaned if text]
    if not cleaned:
        return ""
    if segment_limit <= 0 or len(cleaned) <= segment_limit:
        return "\n".join(cleaned)
    head = max(1, segment_limit // 2)
    tail = max(1, segment_limit - head)
    selected = cleaned[:head] + cleaned[-tail:]
    return "\n".join(selected)


def _extract_summary_payload(text: str) -> dict[str, Any]:
    src = str(text or "").strip()
    if not src:
        return {"summary": "", "genre": []}
    candidates: list[str] = [src]
    if "```" in src:
        for chunk in src.split("```"):
            payload = chunk.strip()
            if payload.startswith("json"):
                payload = payload[4:].strip()
            if payload:
                candidates.append(payload)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            summary = str(data.get("summary") or "").strip()
            genre_raw = data.get("genre") or data.get("genres") or []
            if isinstance(genre_raw, str):
                genres = [item.strip() for item in genre_raw.split(",") if item.strip()]
            elif isinstance(genre_raw, list):
                genres = [str(item).strip() for item in genre_raw if str(item).strip()]
            else:
                genres = []
            return {"summary": summary, "genre": genres}
    return {"summary": src, "genre": []}


def generate_transcript_summary(
    *,
    transcript_segments: list[dict[str, Any]],
    metadata: Mapping[str, Any],
    settings: Mapping[str, Any] | None = None,
    ensure_ollama_ready: Callable[[], None] | None = None,
) -> dict[str, Any]:
    cfg = merged_ai_settings(settings)
    segment_limit = max(0, int(cfg.get("summary_segment_limit") or 0))
    transcript_text = build_summary_transcript_text(
        transcript_segments,
        segment_limit=segment_limit,
    )
    if not transcript_text:
        return {"summary": "", "genre": [], "segment_limit": segment_limit}
    custom_instructions = load_summary_instructions(cfg)
    prompt_parts = [
        "Summarize this transcript for a local video library.",
        "Return ONLY JSON with keys `summary` and `genre`.",
        "`summary` should be a concise 2-5 sentence paragraph.",
        "`genre` should be a short array of category tags.",
        "",
        "Video metadata:",
        json.dumps(
            {
                "title": metadata.get("title"),
                "channel": metadata.get("channel") or metadata.get("uploader"),
                "uploader_id": metadata.get("uploader_id"),
                "duration": metadata.get("duration"),
                "description": metadata.get("description"),
                "categories": metadata.get("categories"),
                "tags": metadata.get("tags"),
            },
            ensure_ascii=False,
        ),
    ]
    if custom_instructions:
        prompt_parts.extend(["", "Custom instructions:", custom_instructions])
    prompt_parts.extend(["", "Transcript:", transcript_text])
    raw = ask_ai_text(
        "\n".join(prompt_parts),
        cfg,
        timeout=180.0,
        ensure_ollama_ready=ensure_ollama_ready,
    )
    payload = _extract_summary_payload(raw)
    payload["segment_limit"] = segment_limit
    payload["model"] = str(
        cfg.get("ollama_model")
        if str(cfg.get("ai_provider") or "ollama").strip().lower() == "ollama"
        else cfg.get("api_model")
    )
    return payload

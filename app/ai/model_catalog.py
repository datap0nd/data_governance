"""Bounded discovery of models from an OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.ai.protocol import (
    AIConfigurationError,
    AIProtocolError,
    AITransportError,
    AITransportTimeout,
    AIUpstreamError,
)
from app.ai.runtime_config import AIRuntimeSettings, normalize_endpoint


MAX_MODELS_RESPONSE_BYTES = 1024 * 1024
MAX_MODELS = 2000
MAX_MODEL_ID_BYTES = 300


def models_endpoint(chat_endpoint: str) -> str:
    """Return the sibling ``/models`` URL for a chat-completions URL."""
    normalized = normalize_endpoint(chat_endpoint)
    if not normalized:
        raise AIConfigurationError("Enter an AI endpoint before loading models.")
    parsed = urlsplit(normalized)
    path = parsed.path.rstrip("/")
    folded = path.casefold()
    for suffix in ("/chat/completions", "/completions"):
        if folded.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path.casefold().endswith("/models"):
        path = f"{path}/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _parse_model_ids(payload: Any, *, api_key: str = "") -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise AIProtocolError(
            "The AI models endpoint did not return an OpenAI-compatible model list."
        )
    rows = payload["data"]
    if len(rows) > MAX_MODELS:
        raise AIProtocolError("The AI models endpoint returned too many models.")

    model_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        model_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        try:
            encoded_id = model_id.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if (
            not model_id
            or len(encoded_id) > MAX_MODEL_ID_BYTES
            or any(ord(char) < 32 for char in model_id)
            or bool(api_key and api_key in model_id)
            or model_id in seen
        ):
            continue
        seen.add(model_id)
        model_ids.append(model_id)
    if not model_ids:
        raise AIProtocolError("The AI models endpoint returned no usable model IDs.")
    return sorted(model_ids, key=lambda value: (value.casefold(), value))


def list_available_models(
    settings: AIRuntimeSettings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Fetch a sanitized model-ID list without sending any Metronome data."""
    url = models_endpoint(settings.endpoint)
    headers = {
        "Accept": "application/json",
        # Bound the bytes we parse ourselves instead of allowing transparent
        # decompression to allocate beyond the response limit.
        "Accept-Encoding": "identity",
    }
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    timeout_seconds = min(30.0, settings.http_timeout_seconds)
    deadline_monotonic = time.monotonic() + timeout_seconds
    timeout = httpx.Timeout(
        connect=min(5.0, timeout_seconds),
        read=min(5.0, timeout_seconds),
        write=min(5.0, timeout_seconds),
        pool=min(5.0, timeout_seconds),
    )
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in {401, 403}:
                    raise AIConfigurationError(
                        "The AI models endpoint rejected the configured credentials."
                    )
                if 300 <= response.status_code < 400:
                    raise AIUpstreamError(
                        "The AI models endpoint redirected, which Metronome does not follow with credentials. Configure the provider to serve its model list directly, or use Custom model ID."
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    raise AIUpstreamError(
                        f"The AI models endpoint returned HTTP {response.status_code}."
                    )
                content_encoding = response.headers.get("content-encoding", "").strip().casefold()
                if content_encoding not in {"", "identity"}:
                    raise AIProtocolError(
                        "The AI models endpoint returned an encoded response despite the identity request."
                    )
                content = bytearray()
                # Encoded responses were rejected above, so iter_bytes cannot
                # transparently expand compressed content past our limit.
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline_monotonic:
                        raise AITransportTimeout(
                            "The AI models endpoint exceeded its total response deadline."
                        )
                    content.extend(chunk)
                    if len(content) > MAX_MODELS_RESPONSE_BYTES:
                        raise AIProtocolError(
                            "The AI models endpoint returned an oversized response."
                        )
    except (
        AIConfigurationError,
        AIProtocolError,
        AITransportTimeout,
        AIUpstreamError,
    ):
        raise
    except httpx.TimeoutException as exc:
        raise AITransportTimeout(
            "The AI models endpoint did not respond before the deadline."
        ) from exc
    except httpx.HTTPError as exc:
        raise AITransportError("Metronome could not reach the AI models endpoint.") from exc

    try:
        payload = json.loads(bytes(content))
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise AIProtocolError(
            "The AI models endpoint returned a non-JSON response."
        ) from exc
    return _parse_model_ids(payload, api_key=settings.api_key)

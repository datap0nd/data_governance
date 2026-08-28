"""Strict OpenAI-compatible transport for Qwen and compatible endpoints."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.ai.runtime_config import (
    AIRuntimeSettings,
    environment_settings,
    sanitize_ai_error,
)
from app.ai.protocol import (
    AIConfigurationError,
    AIProtocolError,
    AITransportError,
    AITransportTimeout,
    AIUpstreamError,
    AssistantTurn,
    ToolCall,
)

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_CONTENT_BYTES = 64 * 1024
MAX_REASONING_BYTES = 128 * 1024
RETRYABLE_STATUS = {429, 502, 503, 504}


def _reject_json_constant(_value: str):
    raise ValueError("Non-finite JSON number")


def _validate_json_shape(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if depth > 12:
        raise AIProtocolError("Tool arguments are nested too deeply.")
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if counter[0] > 2000:
        raise AIProtocolError("Tool arguments contain too many values.")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AIProtocolError("Tool argument names must be strings.")
            _validate_json_shape(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for item in value:
            _validate_json_shape(item, depth=depth + 1, counter=counter)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise AIProtocolError("Tool arguments contain an unsupported value.")


def _json_object(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        try:
            encoded = json.dumps(
                raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (RecursionError, TypeError, ValueError) as exc:
            raise AIProtocolError("The model returned invalid tool arguments.") from exc
        if len(encoded) > MAX_ARGUMENT_BYTES:
            raise AIProtocolError("The model returned oversized tool arguments.")
        _validate_json_shape(raw)
        return json.loads(encoded.decode("utf-8"))
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise AIProtocolError("The model returned invalid or oversized tool arguments.")

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AIProtocolError(f"The model repeated tool argument {key!r}.")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except AIProtocolError:
        raise
    except (RecursionError, TypeError, ValueError) as exc:
        raise AIProtocolError("The model returned malformed JSON tool arguments.") from exc
    if not isinstance(parsed, dict):
        raise AIProtocolError("Tool arguments must be a JSON object.")
    _validate_json_shape(parsed)
    return parsed


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if not isinstance(block, dict) or block.get("type") not in {"text", "output_text"}:
                raise AIProtocolError("The model returned an unsupported content block.")
            text = block.get("text")
            if not isinstance(text, str):
                raise AIProtocolError("The model returned a malformed text block.")
            parts.append(text)
        return "".join(parts)
    raise AIProtocolError("The model returned an unsupported content value.")


class OpenAIChatProvider:
    """Small provider with no application tool-dispatch authority."""

    def __init__(
        self,
        transport=None,
        *,
        settings: AIRuntimeSettings | None = None,
    ):
        self._transport = transport
        # One immutable snapshot is used for the complete request. A settings
        # save can therefore never switch endpoint/model midway through a run.
        self.settings = settings or environment_settings()

    def _payload(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "n": 1,
            "max_tokens": self.settings.max_output_tokens,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        profile = self.settings.provider_profile.casefold()
        if profile == "qwen_vllm" or (
            profile == "auto" and "qwen" in self.settings.model.casefold()
        ):
            payload["reasoning_effort"] = self.settings.reasoning_effort
            payload["chat_template_kwargs"] = {
                "enable_thinking": True,
                "preserve_thinking": True,
            }
        return payload

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        deadline_monotonic: float | None = None,
    ) -> AssistantTurn:
        if not self.settings.qwen_enabled or not self.settings.endpoint:
            raise AIConfigurationError("Qwen is not configured for this Metronome instance.")

        if deadline_monotonic is not None and deadline_monotonic <= time.monotonic():
            raise AITransportTimeout("The Qwen request deadline has already expired.")

        remaining = (
            max(0.1, deadline_monotonic - time.monotonic())
            if deadline_monotonic is not None
            else self.settings.http_timeout_seconds
        )
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        attempts = 2
        response = None
        started = time.monotonic()
        payload = self._payload(messages, tools)
        try:
            payload_size = len(
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
        except (RecursionError, TypeError, ValueError) as exc:
            raise AIProtocolError("The Qwen request transcript is not valid JSON.") from exc
        if payload_size > MAX_REQUEST_BYTES:
            raise AIProtocolError("The Qwen request transcript exceeded its safe context limit.")
        for attempt in range(attempts):
            if deadline_monotonic is not None:
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    raise AITransportTimeout("The Qwen request deadline has expired.")
            else:
                remaining = self.settings.http_timeout_seconds
            timeout = httpx.Timeout(
                connect=min(5.0, remaining),
                read=min(self.settings.http_timeout_seconds, remaining),
                write=min(10.0, remaining),
                pool=min(5.0, remaining),
            )
            try:
                with httpx.Client(transport=self._transport, timeout=timeout) as client:
                    response = client.post(
                        self.settings.endpoint,
                        json=payload,
                        headers=headers,
                    )
            except httpx.TimeoutException as exc:
                raise AITransportTimeout("The Qwen endpoint did not respond before the deadline.") from exc
            except httpx.HTTPError as exc:
                if attempt == 0 and time.monotonic() + 0.25 < (deadline_monotonic or float("inf")):
                    time.sleep(0.25)
                    continue
                raise AITransportError("Metronome could not reach the Qwen endpoint.") from exc

            if response.status_code not in RETRYABLE_STATUS or attempt == attempts - 1:
                break
            if deadline_monotonic is not None and time.monotonic() + 0.25 >= deadline_monotonic:
                break
            time.sleep(0.25)

        assert response is not None
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            logger.warning(
                "AI endpoint returned HTTP %s after %sms", response.status_code, elapsed_ms
            )
            if response.status_code in {401, 403}:
                raise AIConfigurationError("The Qwen endpoint rejected its configured credentials.")
            raise AIUpstreamError(
                f"The Qwen endpoint returned HTTP {response.status_code}."
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise AIProtocolError("The Qwen endpoint returned an oversized response.")
        try:
            data = response.json()
        except ValueError as exc:
            raise AIProtocolError("The Qwen endpoint returned a non-JSON response.") from exc
        if not isinstance(data, dict):
            raise AIProtocolError("The Qwen endpoint returned an invalid response object.")
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise AIProtocolError("The Qwen endpoint must return exactly one completion choice.")
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason in {"length", "content_filter"}:
            raise AIProtocolError("The Qwen response was truncated or filtered before completion.")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise AIProtocolError("The Qwen endpoint omitted the assistant message.")
        content = _content_text(message.get("content"))
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise AIProtocolError("The Qwen endpoint returned oversized assistant content.")
        folded_content = content.casefold()
        if "<tool_call>" in folded_content or "<think>" in folded_content:
            raise AIProtocolError(
                "The Qwen server returned raw reasoning/tool markup. Configure its native Qwen reasoning and tool-call parsers."
            )

        raw_calls = message.get("tool_calls", [])
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise AIProtocolError("The Qwen endpoint returned malformed tool calls.")
        calls = []
        seen_ids = set()
        for index, item in enumerate(raw_calls):
            if not isinstance(item, dict) or item.get("type", "function") != "function":
                raise AIProtocolError("The Qwen endpoint returned an unsupported tool call.")
            function = item.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            if (
                not isinstance(function, dict)
                or not isinstance(name, str)
                or not name
                or len(name) > 100
            ):
                raise AIProtocolError("The Qwen endpoint returned a malformed function call.")
            call_id = item.get("id")
            if (
                not isinstance(call_id, str)
                or not call_id
                or len(call_id) > 200
                or call_id in seen_ids
            ):
                raise AIProtocolError("The Qwen endpoint returned an invalid or duplicate tool-call ID.")
            seen_ids.add(call_id)
            calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=_json_object(function.get("arguments", {})),
                )
            )

        if not content and not calls:
            raise AIProtocolError("The Qwen endpoint returned neither text nor a tool call.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        safe_usage = None
        if usage:
            safe_usage = {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    safe_usage[key] = value
        reasoning = message.get("reasoning_content")
        if reasoning is None:
            reasoning = message.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            reasoning = None
        if reasoning is not None and len(reasoning.encode("utf-8")) > MAX_REASONING_BYTES:
            raise AIProtocolError("The Qwen endpoint returned oversized reasoning content.")
        response_id = (
            sanitize_ai_error(data.get("id"), self.settings.api_key, limit=200)
            if data.get("id")
            else None
        )
        response_model = (
            sanitize_ai_error(data.get("model"), self.settings.api_key, limit=200)
            if data.get("model")
            else None
        )
        logged_tool_names = [
            sanitize_ai_error(call.name, self.settings.api_key, limit=100)
            for call in calls
        ]
        logger.info(
            "AI completion response_id=%s model=%s duration_ms=%s tool_calls=%s",
            response_id,
            response_model,
            elapsed_ms,
            logged_tool_names,
        )
        return AssistantTurn(
            content=content,
            reasoning_content=reasoning,
            tool_calls=tuple(calls),
            finish_reason=finish_reason,
            usage=safe_usage,
            response_id=response_id,
            model=response_model,
        )

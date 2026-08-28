"""Live, typed configuration for every Metronome AI surface.

Non-secret settings are stored as one versioned JSON value in ``app_settings``.
The optional API key is kept in a separate row so it can never be included by
generic settings serialization.  The key is intentionally write-only through
the HTTP API: public projections report only whether it exists and its source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.database import get_db


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_runtime_settings_v1"
API_KEY_KEY = "ai_api_key_v1"
SETTINGS_VERSION = 1

Mode = Literal["disabled", "preview", "qwen"]
ProviderProfile = Literal["auto", "qwen_vllm", "openai_compatible"]
ReasoningEffort = Literal["low", "medium", "xhigh"]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "y", "on"}


def normalize_endpoint(value: str | None) -> str:
    """Validate and normalize an OpenAI-compatible chat-completions URL."""
    endpoint = str(value or "").strip().rstrip("/")
    if not endpoint:
        return ""
    if len(endpoint) > 2048 or any(ord(char) < 32 for char in endpoint):
        raise ValueError("The AI endpoint is invalid.")
    parsed = urlsplit(endpoint)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("The AI endpoint must be an http:// or https:// URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Put credentials in the API key field, not in the endpoint URL.")
    if parsed.query or parsed.fragment:
        raise ValueError("The AI endpoint cannot contain a query string or fragment.")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1/chat/completions"
    elif path.casefold().endswith("/v1"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc, path, "", ""))


class AISettingsUpdate(BaseModel):
    """Partial write/test payload. Omitted fields retain their current value."""

    model_config = ConfigDict(extra="forbid")

    mode: Mode | None = None
    endpoint: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=300)
    provider_profile: ProviderProfile | None = None
    reasoning_effort: ReasoningEffort | None = None
    max_tool_calls: int | None = Field(default=None, ge=1, le=12)
    max_model_turns: int | None = Field(default=None, ge=1, le=8)
    max_seconds: int | None = Field(default=None, ge=30, le=300)
    http_timeout_seconds: float | None = Field(default=None, ge=10, le=180)
    max_output_tokens: int | None = Field(default=None, ge=512, le=8192)
    temperature: float | None = Field(default=None, ge=0, le=1.5)
    top_p: float | None = Field(default=None, ge=0.1, le=1)
    operations_investigator_enabled: bool | None = None
    automatic_alert_review_enabled: bool | None = None
    alert_email_analysis_enabled: bool | None = None
    documentation_suggestions_enabled: bool | None = None
    # Parse this manually so FastAPI can never reflect a malformed candidate
    # key inside its automatic validation-error response.
    api_key: Any = Field(default=None, exclude=True)
    clear_api_key: bool = False

    def submitted_api_key(self) -> str:
        if self.api_key is None:
            return ""
        if not isinstance(self.api_key, str):
            raise ValueError("The API key must be a string.")
        return self.api_key.strip()


class _StoredAISettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Literal[1] = SETTINGS_VERSION
    mode: Mode
    endpoint: str
    model: str
    provider_profile: ProviderProfile
    reasoning_effort: ReasoningEffort
    max_tool_calls: int = Field(ge=1, le=12)
    max_model_turns: int = Field(ge=1, le=8)
    max_seconds: int = Field(ge=30, le=300)
    http_timeout_seconds: float = Field(ge=10, le=180)
    max_output_tokens: int = Field(ge=512, le=8192)
    temperature: float = Field(ge=0, le=1.5)
    top_p: float = Field(ge=0.1, le=1)
    operations_investigator_enabled: bool
    automatic_alert_review_enabled: bool
    alert_email_analysis_enabled: bool
    documentation_suggestions_enabled: bool


@dataclass(frozen=True)
class AIRuntimeSettings:
    mode: Mode
    endpoint: str
    model: str
    provider_profile: ProviderProfile
    reasoning_effort: ReasoningEffort
    max_tool_calls: int
    max_model_turns: int
    max_seconds: int
    http_timeout_seconds: float
    max_output_tokens: int
    temperature: float
    top_p: float
    operations_investigator_enabled: bool
    automatic_alert_review_enabled: bool
    alert_email_analysis_enabled: bool
    documentation_suggestions_enabled: bool
    api_key: str = ""
    api_key_source: str = "none"
    api_key_updated_at: str | None = None
    configuration_source: str = "environment"
    updated_at: str | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    @property
    def mock_mode(self) -> bool:
        return self.mode == "preview"

    @property
    def qwen_enabled(self) -> bool:
        return self.mode == "qwen"

    @property
    def provider_mode(self) -> str:
        if self.mode == "preview":
            return "mock"
        return "qwen" if self.mode == "qwen" else "disabled"

    @property
    def effective_state(self) -> str:
        if self.mode == "disabled":
            return "disabled"
        if self.mode == "preview":
            return "deterministic_preview"
        return "configured"

    @property
    def fingerprint(self) -> str:
        """Hash inference-affecting, non-secret configuration."""
        payload = {
            "version": SETTINGS_VERSION,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "model": self.model,
            "provider_profile": self.provider_profile,
            "reasoning_effort": self.reasoning_effort,
            "max_tool_calls": self.max_tool_calls,
            "max_model_turns": self.max_model_turns,
            "max_seconds": self.max_seconds,
            "http_timeout_seconds": self.http_timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            # Credential contents are never hashed or persisted in run rows.
            # The non-secret source/revision is enough to retry bounded failed
            # work after a saved credential is replaced or removed.
            "api_key_source": self.api_key_source,
            "api_key_updated_at": self.api_key_updated_at,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def feature_enabled(self, name: str) -> bool:
        if not self.enabled:
            return False
        flags = {
            "operations_investigator": self.operations_investigator_enabled,
            "automatic_alert_review": self.automatic_alert_review_enabled,
            "alert_email_analysis": self.alert_email_analysis_enabled,
            "documentation_suggestions": self.documentation_suggestions_enabled,
        }
        return bool(flags.get(name, False))

    def public_dict(self) -> dict:
        """Explicit public projection. Never add ``api_key`` here."""
        return {
            "version": SETTINGS_VERSION,
            "mode": self.mode,
            "enabled": self.enabled,
            "mock_mode": self.mock_mode,
            "endpoint": self.endpoint,
            "model": self.model,
            "provider_profile": self.provider_profile,
            "reasoning_effort": self.reasoning_effort,
            "max_tool_calls": self.max_tool_calls,
            "max_model_turns": self.max_model_turns,
            "max_seconds": self.max_seconds,
            "http_timeout_seconds": self.http_timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "operations_investigator_enabled": self.operations_investigator_enabled,
            "automatic_alert_review_enabled": self.automatic_alert_review_enabled,
            "alert_email_analysis_enabled": self.alert_email_analysis_enabled,
            "documentation_suggestions_enabled": self.documentation_suggestions_enabled,
            "api_key_configured": bool(self.api_key),
            "api_key_source": self.api_key_source,
            "api_key_updated_at": self.api_key_updated_at,
            "configuration_source": self.configuration_source,
            "updated_at": self.updated_at,
            "fingerprint": self.fingerprint,
            "effective_state": self.effective_state,
        }

    def stored_dict(self) -> dict:
        return {
            "version": SETTINGS_VERSION,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "model": self.model,
            "provider_profile": self.provider_profile,
            "reasoning_effort": self.reasoning_effort,
            "max_tool_calls": self.max_tool_calls,
            "max_model_turns": self.max_model_turns,
            "max_seconds": self.max_seconds,
            "http_timeout_seconds": self.http_timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "operations_investigator_enabled": self.operations_investigator_enabled,
            "automatic_alert_review_enabled": self.automatic_alert_review_enabled,
            "alert_email_analysis_enabled": self.alert_email_analysis_enabled,
            "documentation_suggestions_enabled": self.documentation_suggestions_enabled,
        }


def environment_settings() -> AIRuntimeSettings:
    """Build the pre-settings-page behavior from existing config/env values."""
    mode: Mode
    if not _bool_env("DG_AI_ENABLED", True):
        mode = "disabled"
    else:
        mode = "preview" if bool(config.AI_MOCK) else "qwen"
    profile = str(config.AI_PROVIDER_PROFILE or "auto").casefold()
    if profile not in {"auto", "qwen_vllm", "openai_compatible"}:
        profile = "auto"
    reasoning = str(config.AI_REASONING_EFFORT or "medium").casefold()
    if reasoning not in {"low", "medium", "xhigh"}:
        reasoning = "medium"
    try:
        endpoint = normalize_endpoint(config.AI_API_URL)
    except ValueError:
        # AI configuration must never prevent the rest of Metronome from
        # starting. The user can repair the value from System > AI.
        logger.error(
            "The environment AI endpoint is invalid; AI starts disabled until it is corrected in System > AI."
        )
        endpoint = ""
        mode = "disabled"
    return AIRuntimeSettings(
        mode=mode,
        endpoint=endpoint,
        model=str(config.AI_MODEL or "Qwen/Qwen3.8-27B").strip(),
        provider_profile=profile,  # type: ignore[arg-type]
        reasoning_effort=reasoning,  # type: ignore[arg-type]
        max_tool_calls=int(config.AI_AGENT_MAX_TOOL_CALLS),
        max_model_turns=int(config.AI_AGENT_MAX_MODEL_TURNS),
        max_seconds=int(config.AI_AGENT_MAX_SECONDS),
        http_timeout_seconds=float(config.AI_AGENT_HTTP_TIMEOUT_SECONDS),
        max_output_tokens=int(config.AI_AGENT_MAX_OUTPUT_TOKENS),
        temperature=float(config.AI_AGENT_TEMPERATURE),
        top_p=float(config.AI_AGENT_TOP_P),
        operations_investigator_enabled=_bool_env(
            "DG_AI_OPERATIONS_INVESTIGATOR_ENABLED", True
        ),
        automatic_alert_review_enabled=_bool_env(
            "DG_AI_AUTOMATIC_ALERT_REVIEW_ENABLED", True
        ),
        alert_email_analysis_enabled=_bool_env(
            "DG_AI_ALERT_EMAIL_ANALYSIS_ENABLED", True
        ),
        documentation_suggestions_enabled=_bool_env(
            "DG_AI_DOCUMENTATION_SUGGESTIONS_ENABLED", True
        ),
        api_key=str(config.AI_API_KEY or ""),
        api_key_source="environment" if config.AI_API_KEY else "none",
        configuration_source="environment",
    )


def _settings_from_rows(settings_row, key_row) -> AIRuntimeSettings:
    fallback = environment_settings()
    if settings_row is None:
        stored_settings = None
    else:
        try:
            stored_settings = _StoredAISettings.model_validate_json(settings_row["value"])
        except Exception:
            logger.exception("Stored AI settings are invalid; using environment defaults")
            stored_settings = None

    if stored_settings is None:
        resolved = fallback
    else:
        resolved = replace(
            fallback,
            **stored_settings.model_dump(exclude={"version"}),
            configuration_source="system",
            updated_at=settings_row["updated_at"],
        )

    if key_row is not None:
        # Row presence is authoritative. An explicit empty tombstone prevents
        # `clear_api_key` from silently resurrecting DG_AI_API_KEY on reload.
        stored_key = str(key_row["value"] or "")
        resolved = replace(
            resolved,
            api_key=stored_key,
            api_key_source="system" if stored_key else "none",
            api_key_updated_at=key_row["updated_at"],
        )
    return resolved


def load_runtime_settings(conn=None) -> AIRuntimeSettings:
    """Load one immutable settings snapshot from SQLite plus env fallback."""
    if conn is None:
        with get_db() as db:
            return load_runtime_settings(db)
    rows = conn.execute(
        "SELECT key, value, updated_at FROM app_settings WHERE key IN (?, ?)",
        (SETTINGS_KEY, API_KEY_KEY),
    ).fetchall()
    by_key = {row["key"]: row for row in rows}
    return _settings_from_rows(by_key.get(SETTINGS_KEY), by_key.get(API_KEY_KEY))


def _merge_settings(
    current: AIRuntimeSettings,
    update: AISettingsUpdate,
    *,
    for_test: bool = False,
) -> AIRuntimeSettings:
    values = update.model_dump(
        exclude_none=True,
        exclude={"api_key", "clear_api_key"},
    )
    if "endpoint" in values:
        values["endpoint"] = normalize_endpoint(values["endpoint"])
    if "model" in values:
        values["model"] = str(values["model"]).strip()
    merged = replace(current, **values)
    if merged.mode == "qwen" and not merged.endpoint:
        raise ValueError("A Qwen endpoint is required in Qwen mode.")
    if merged.mode == "qwen" and not merged.model:
        raise ValueError("A model name is required in Qwen mode.")

    submitted_key = update.submitted_api_key()
    if submitted_key and update.clear_api_key:
        raise ValueError("Supply a new API key or clear it, not both.")
    if submitted_key:
        if len(submitted_key) > 4096 or any(ord(char) < 32 for char in submitted_key):
            raise ValueError("The API key is invalid.")
        merged = replace(
            merged,
            api_key=submitted_key,
            api_key_source="candidate" if for_test else "system",
        )
    elif update.clear_api_key:
        merged = replace(merged, api_key="", api_key_source="none", api_key_updated_at=None)
    return merged


def candidate_runtime_settings(update: AISettingsUpdate) -> AIRuntimeSettings:
    """Resolve an unsaved connection-test candidate without mutating state."""
    return _merge_settings(load_runtime_settings(), update, for_test=True)


def save_runtime_settings(conn, update: AISettingsUpdate) -> AIRuntimeSettings:
    """Atomically persist validated settings and an optional write-only key."""
    current = load_runtime_settings(conn)
    merged = _merge_settings(current, update)
    encoded = json.dumps(
        merged.stored_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    conn.execute(
        """INSERT INTO app_settings(key, value, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET
               value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
        (SETTINGS_KEY, encoded),
    )
    submitted_key = update.submitted_api_key()
    if submitted_key:
        conn.execute(
            """INSERT INTO app_settings(key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (API_KEY_KEY, submitted_key),
        )
    elif update.clear_api_key:
        conn.execute(
            """INSERT INTO app_settings(key, value, updated_at)
               VALUES (?, '', CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value='', updated_at=CURRENT_TIMESTAMP""",
            (API_KEY_KEY,),
        )
    return load_runtime_settings(conn)


def initialize_runtime_settings() -> AIRuntimeSettings:
    """Validate the effective startup configuration after schema migration."""
    return load_runtime_settings()


def sanitize_ai_error(value: object, *secrets: str, limit: int = 500) -> str:
    """Return a bounded provider diagnostic without credentials or headers."""
    text = str(value or "AI connection failed.")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(
        r"(?i)(authorization|api[_ -]?key|token|password|secret)\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(r"(https?://[^:/\s]+:)[^@/\s]+@", r"\1[redacted]@", text)
    text = " ".join(text.split())
    return text[:limit] or "AI connection failed."

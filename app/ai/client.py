"""LLM client — calls LiteLLM (OpenAI-compatible) or returns mock responses."""

import logging

import httpx
from app.config import AI_BASE_URL, AI_API_KEY, AI_MODEL, AI_MOCK

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Send a chat completion request to the LiteLLM endpoint.

    Uses OpenAI-compatible POST /chat/completions format.
    Returns the assistant's response text, or a fallback message on error.
    """
    url = AI_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if AI_API_KEY:
        headers["Authorization"] = f"Bearer {AI_API_KEY}"

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # OpenAI-compatible format
            if "choices" in data and data["choices"]:
                return data["choices"][0]["message"]["content"]
            # Anthropic format fallback
            if "content" in data and data["content"]:
                return data["content"][0]["text"]
            return str(data)

    except httpx.ConnectError:
        logger.warning("LLM endpoint unreachable at %s", url)
        return "**AI service unavailable** — cannot connect to LLM endpoint. Check `endpoint_url.txt` or set `DG_AI_URL`."
    except httpx.TimeoutException:
        logger.warning("LLM request timed out after %ss to %s", TIMEOUT, url)
        return "**AI service timeout** — the LLM took too long to respond. Try again or check the endpoint."
    except httpx.HTTPStatusError as e:
        logger.warning("LLM returned HTTP %s: %s", e.response.status_code, e.response.text[:200])
        return f"**AI service error** — LLM returned HTTP {e.response.status_code}. Check the model name and endpoint configuration."
    except Exception as e:
        logger.exception("Unexpected LLM error")
        return f"**AI service error** — {e}"

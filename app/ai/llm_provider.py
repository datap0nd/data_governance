"""Interface for real LLM providers (to be connected later)."""

import httpx
from app.config import AI_API_URL, AI_API_KEY, AI_MODEL


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to the configured LLM endpoint and return the response text.

    This is a placeholder for real LLM integration. The endpoint should accept
    an OpenAI-compatible chat completions format.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
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

        resp = await client.post(AI_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # OpenAI-compatible format
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        # Anthropic format
        if "content" in data:
            return data["content"][0]["text"]
        return str(data)

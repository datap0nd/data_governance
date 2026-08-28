"""Compatibility shim for the existing single-turn AI features."""

from app.ai.openai_provider import OpenAIChatProvider


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Send a plain text request through the strict provider transport."""
    provider = OpenAIChatProvider()
    turn = provider.complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        [],
    )
    if turn.tool_calls or not turn.content:
        raise RuntimeError("The configured model did not return a plain text response.")
    return turn.content

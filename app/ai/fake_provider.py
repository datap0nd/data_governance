"""Deterministic provider used by the Operations Investigator test suite."""

from __future__ import annotations

from copy import deepcopy

from app.ai.protocol import AssistantTurn


class ScriptedProvider:
    def __init__(self, turns: list[AssistantTurn | Exception]):
        self._turns = list(turns)
        self.requests: list[dict] = []

    def complete(self, messages, tools, *, deadline_monotonic=None) -> AssistantTurn:
        self.requests.append({
            "messages": deepcopy(messages),
            "tools": deepcopy(tools),
            "deadline_monotonic": deadline_monotonic,
        })
        if not self._turns:
            raise AssertionError("ScriptedProvider received an unexpected request.")
        item = self._turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)

    def assert_exhausted(self) -> None:
        if self._turns:
            raise AssertionError(f"{len(self._turns)} scripted response(s) were not used.")

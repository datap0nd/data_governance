"""Typed protocol shared by the Metronome agent runner and AI provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AIError(RuntimeError):
    """Base class for safe, user-facing AI failures."""

    code = "ai_error"


class AIConfigurationError(AIError):
    code = "ai_not_configured"


class AITransportError(AIError):
    code = "ai_transport_error"


class AITransportTimeout(AITransportError):
    code = "ai_timeout"


class AIUpstreamError(AIError):
    code = "ai_upstream_error"


class AIProtocolError(AIError):
    code = "ai_protocol_error"


class AgentBudgetExceeded(AIError):
    code = "agent_budget_exceeded"


class AgentCancelled(AIError):
    code = "agent_cancelled"


class AgentEvidenceSuperseded(AIError):
    """The canonical alert changed while its bound investigation was running."""

    code = "agent_evidence_superseded"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantTurn:
    content: str = ""
    reasoning_content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    response_id: str | None = None
    model: str | None = None


class ChatProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        deadline_monotonic: float | None = None,
    ) -> AssistantTurn: ...


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=400)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        min_length=1, max_length=6
    )


class AgentRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal[
        "inspect",
        "wait",
        "resume",
        "retry_sql",
        "run_fresh",
        "contact_owner",
        "review_configuration",
        "none",
    ]
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=400)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        min_length=1, max_length=6
    )


class AgentResult(BaseModel):
    """Terminal, evidence-backed result accepted from the model."""

    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(
        min_length=1,
        max_length=600,
        description="A concise statement of what went wrong.",
    )
    impact: str = Field(
        min_length=1,
        max_length=400,
        description="The concrete downstream effect of the issue.",
    )
    conclusion_evidence_refs: list[
        Annotated[str, Field(min_length=1, max_length=120)]
    ] = Field(min_length=1, max_length=6)
    alert_assessment: Literal[
        "confirmed",
        "likely",
        "uncertain",
        "not_supported",
    ] = "uncertain"
    confidence: Literal["low", "medium", "high"]
    observed_facts: list[EvidenceClaim] = Field(min_length=1, max_length=4)
    inferences: list[EvidenceClaim] = Field(default_factory=list, max_length=2)
    recommendations: list[AgentRecommendation] = Field(min_length=1, max_length=1)
    unknowns: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def concise_operational_summary(self):
        """Keep the three user-facing sections at or below 100 words total."""
        recommendation = self.recommendations[0]
        words = " ".join((
            self.conclusion,
            self.impact,
            recommendation.title,
            recommendation.rationale,
        )).split()
        if len(words) > 100:
            raise ValueError(
                "What happened, impact, and suggested action must total 100 words or fewer."
            )
        return self


def terminal_tool_definition() -> dict[str, Any]:
    """OpenAI-compatible pseudo-tool used for strict final output."""
    return {
        "type": "function",
        "function": {
            "name": "submit_agent_result",
            "description": (
                "Finish the investigation with a structured result. Every evidence_refs "
                "value must exactly match a reference returned by a read tool. This tool "
                "does not execute any operational action."
            ),
            "parameters": AgentResult.model_json_schema(),
        },
    }

"""Deterministic, offline LLM provider.

It implements the same prompt/validation path as a live model, but derives the
JSON answer from the structured payload embedded in the prompt. This keeps the
demo reproducible and the test suite hermetic while the prompts remain the real
production artefact.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..care import derive_care
from ..features import extract_signals
from ..models import (
    ChatMessageIn,
    CustomerFeatures,
    OfferDefinition,
    RiskScore,
)
from ..offers import choose_offer
from ..scoring import narrative_for
from .client import LLMOutputError

WORKFLOW_RE = re.compile(r"Workflow:\s*([a-z_]+)")
JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def _blocks(user: str) -> list[Any]:
    parsed: list[Any] = []
    for match in JSON_BLOCK_RE.finditer(user):
        try:
            parsed.append(json.loads(match.group(1)))
        except json.JSONDecodeError as exc:
            raise LLMOutputError(f"Embedded payload is not valid JSON: {exc}") from exc
    return parsed


def _messages(block: Any) -> list[ChatMessageIn]:
    if not isinstance(block, list):
        raise LLMOutputError("Expected a JSON array of conversation messages")
    return [ChatMessageIn.model_validate(item) for item in block]


class DeterministicLLM:
    """Rule-based stand-in for a chat-completions model."""

    name = "deterministic"

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        workflow_match = WORKFLOW_RE.search(user)
        if workflow_match is None:
            raise LLMOutputError("Prompt does not declare a workflow")
        workflow = workflow_match.group(1)
        blocks = _blocks(user)

        if workflow == "chat_extraction":
            result = extract_signals(_messages(blocks[0])).model_dump()
        elif workflow == "care_triage":
            risk = RiskScore.model_validate(blocks[1])
            result = derive_care(_messages(blocks[0]), risk).model_dump()
        elif workflow == "churn_narrative":
            risk = RiskScore.model_validate(blocks[0])
            features = CustomerFeatures.model_validate(blocks[1])
            rationale, save_play = narrative_for(risk, features)
            result = {"rationale": rationale, "save_play": save_play}
        elif workflow == "offer_recommendation":
            context = blocks[0]
            if not isinstance(context, dict):
                raise LLMOutputError("Expected a JSON object of customer context")
            features = CustomerFeatures.model_validate(context["features"])
            risk = RiskScore.model_validate(context["churn"])
            offers = [OfferDefinition.model_validate(item) for item in blocks[1]]
            result = choose_offer(offers, risk.top_drivers, features).model_dump()
        else:
            raise LLMOutputError(f"Unsupported workflow: {workflow}")

        return json.dumps(result, ensure_ascii=False)

"""Typed workflow helpers grouping prompts, payloads and validation."""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..models import (
    CareAssessment,
    ChatRecord,
    ChatSignals,
    ChurnNarrative,
    CustomerFeatures,
    OfferChoice,
    OfferDefinition,
    RiskScore,
)
from .client import StructuredLLM


def _messages_json(messages: Sequence[ChatRecord]) -> str:
    payload = [
        {
            "role": message.role,
            "message": message.message,
            "channel": message.channel,
            "ts": message.ts,
        }
        for message in messages
    ]
    return json.dumps(payload, ensure_ascii=False)


def extract_chat_signals(
    runner: StructuredLLM, customer_id: str, messages: Sequence[ChatRecord]
) -> ChatSignals:
    return runner.run(
        "chat_extraction",
        {"customer_id": customer_id, "input_json": _messages_json(messages)},
        ChatSignals,
    )


def triage_care(
    runner: StructuredLLM,
    customer_id: str,
    messages: Sequence[ChatRecord],
    risk: RiskScore,
) -> CareAssessment:
    return runner.run(
        "care_triage",
        {
            "customer_id": customer_id,
            "input_json": _messages_json(messages),
            "risk_json": json.dumps(risk.model_dump()),
        },
        CareAssessment,
    )


def write_churn_narrative(
    runner: StructuredLLM,
    customer_id: str,
    risk: RiskScore,
    features: CustomerFeatures,
) -> ChurnNarrative:
    return runner.run(
        "churn_narrative",
        {
            "customer_id": customer_id,
            "assessment_json": json.dumps(risk.model_dump()),
            "features_json": json.dumps(features.model_dump()),
        },
        ChurnNarrative,
    )


def recommend_offer(
    runner: StructuredLLM,
    customer_id: str,
    risk: RiskScore,
    features: CustomerFeatures,
    offers: Sequence[OfferDefinition],
) -> OfferChoice:
    allowed = {offer.code for offer in offers}

    def validate_choice(choice: OfferChoice) -> None:
        if choice.code not in allowed:
            raise ValueError(
                f"Offer code {choice.code!r} is not in the eligible shortlist "
                f"{sorted(allowed)}"
            )

    return runner.run(
        "offer_recommendation",
        {
            "customer_id": customer_id,
            "context_json": json.dumps(
                {
                    "customer_id": customer_id,
                    "churn": risk.model_dump(),
                    "features": features.model_dump(),
                }
            ),
            "offers_json": json.dumps([offer.model_dump() for offer in offers]),
        },
        OfferChoice,
        validate_choice,
    )

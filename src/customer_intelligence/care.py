"""Customer-care triage rules.

Used by the deterministic provider and as the fallback when a live LLM call
fails, so a care queue is always produced.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .features import (
    CANCELLATION_RE,
    COMPLAINT_RE,
    ESCALATION_RE,
    UNRESOLVED_RE,
    ChatLike,
)
from .models import CareAssessment, CareCategory, RiskScore, Urgency

SLA_HOURS: dict[Urgency, int] = {
    "urgent": 2,
    "high": 8,
    "normal": 24,
    "low": 48,
}

ACTIONS: dict[CareCategory, str] = {
    "cancellation_request": (
        "Route to the retention desk and call within SLA with an approved save offer."
    ),
    "billing_dispute": (
        "Review the last three invoices, confirm the disputed charge and apply an "
        "approved correction if valid."
    ),
    "technical_issue": (
        "Assign a network engineer, confirm the outage scope and give the customer an ETA."
    ),
    "plan_change": (
        "Compare usage with the current plan and prepare the two best-fit options."
    ),
    "general_question": (
        "Answer the question, confirm no follow-up is needed and close the contact."
    ),
}

TECH_WORDS = (
    "outage",
    "down",
    "slow",
    "speed",
    "router",
    "connection",
    "disconnect",
    "wifi",
    "signal",
    "broken",
    "not working",
)
BILLING_WORDS = (
    "invoice",
    "billing",
    "charge",
    "charged",
    "refund",
    "payment",
    "bill",
)
PLAN_WORDS = ("upgrade", "downgrade", "plan", "add-on", "addon", "package", "contract")
DISPUTE_WORDS = ("refund", "dispute", "wrong", "twice", "double", "overage", "charged")


def _word_re(words: Sequence[str]) -> re.Pattern[str]:
    """Match whole words/phrases so 'downgrade' never matches 'down'."""
    return re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b")


TECH_RE = _word_re(TECH_WORDS)
BILLING_RE = _word_re(BILLING_WORDS)
PLAN_RE = _word_re(PLAN_WORDS)
DISPUTE_RE = _word_re(DISPUTE_WORDS)


def _text(messages: Sequence[ChatLike]) -> str:
    return " ".join(item.message for item in messages if item.role == "customer").lower()


def derive_care(messages: Sequence[ChatLike], risk: RiskScore) -> CareAssessment:
    """Classify the conversation and derive urgency, SLA and next best action."""
    text = _text(messages)
    cancellation = bool(CANCELLATION_RE.search(text))
    unresolved = bool(UNRESOLVED_RE.search(text))
    complaint = bool(COMPLAINT_RE.search(text))
    escalation = bool(ESCALATION_RE.search(text))

    billing_signal = bool(BILLING_RE.search(text))
    dispute_signal = bool(DISPUTE_RE.search(text))

    category: CareCategory
    if cancellation:
        category = "cancellation_request"
    elif billing_signal and (dispute_signal or complaint):
        category = "billing_dispute"
    elif TECH_RE.search(text):
        category = "technical_issue"
    elif PLAN_RE.search(text):
        category = "plan_change"
    else:
        category = "general_question"

    urgency: Urgency
    if cancellation:
        urgency = "urgent"
    elif risk.band == "critical" or (unresolved and risk.score >= 60.0):
        urgency = "urgent"
    elif risk.band == "high" or escalation or complaint:
        urgency = "high"
    elif risk.band == "medium" or risk.score >= 30.0:
        urgency = "normal"
    else:
        urgency = "low"

    return CareAssessment(
        category=category,
        urgency=urgency,
        next_best_action=ACTIONS[category],
        sla_hours=SLA_HOURS[urgency],
        escalate=urgency == "urgent" or cancellation,
    )

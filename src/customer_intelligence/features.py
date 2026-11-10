"""Deterministic feature extraction from chat logs and billing history.

The raw text statistics in this module are combined with the structured
`ChatSignals` returned by the LLM workflow. Keeping the arithmetic deterministic
means the same transcript always yields the same churn score, which is essential
for auditability and tests.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta

from .models import (
    BillingRecordIn,
    ChatMessageIn,
    ChatRecord,
    ChatSignals,
    CustomerFeatures,
    CustomerIn,
    CustomerOut,
)
from .timeutil import parse_iso, utcnow

ChatLike = ChatMessageIn | ChatRecord

CANCELLATION_WORDS = (
    "cancel",
    "terminate",
    "leaving",
    "leave you",
    "switch to",
    "end my contract",
    "not worth",
    "stop my service",
)
COMPETITORS = ("TelcoOne", "NovaNet", "SkyLink", "FibreFast", "OrbitTel")
COMPLAINT_WORDS = (
    "broken",
    "outage",
    "slow",
    "not working",
    "doesn't work",
    "does not work",
    "charged",
    "refund",
    "again",
    "still",
    "terrible",
    "unacceptable",
    "frustrat",
    "ridiculous",
    "waiting",
)
UNRESOLVED_WORDS = (
    "still",
    "no response",
    "waiting",
    "unresolved",
    "not fixed",
    "third time",
    "again",
)
ESCALATION_WORDS = ("manager", "supervisor", "escalate", "legal", "ombudsman")
POSITIVE_WORDS = ("thank", "great", "happy", "love", "appreciate", "fixed", "resolved")

INTENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("billing_question", ("invoice", "billing", "charge", "charged", "refund", "payment", "bill")),
    (
        "technical_issue",
        (
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
        ),
    ),
    ("cancellation", CANCELLATION_WORDS),
    ("plan_change", ("upgrade", "downgrade", "plan", "add-on", "addon", "package", "contract")),
    ("sales_interest", ("interested", "offer", "deal", "discount", "promotion")),
    ("complaint", COMPLAINT_WORDS),
    ("general_question", ("how", "what", "when", "question", "help")),
)

COMPLAINT_RE = re.compile("|".join(re.escape(word) for word in COMPLAINT_WORDS), re.IGNORECASE)
ESCALATION_RE = re.compile(
    "|".join(re.escape(word) for word in ESCALATION_WORDS), re.IGNORECASE
)
CANCELLATION_RE = re.compile(
    "|".join(re.escape(word) for word in CANCELLATION_WORDS), re.IGNORECASE
)
UNRESOLVED_RE = re.compile(
    "|".join(re.escape(word) for word in UNRESOLVED_WORDS), re.IGNORECASE
)
POSITIVE_RE = re.compile("|".join(re.escape(word) for word in POSITIVE_WORDS), re.IGNORECASE)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _customer_messages(messages: Sequence[ChatLike]) -> list[str]:
    return [item.message for item in messages if item.role == "customer"]


def _find_competitor(text: str) -> str | None:
    lowered = text.lower()
    for competitor in COMPETITORS:
        if competitor.lower() in lowered:
            return competitor
    return None


def extract_signals(messages: Sequence[ChatLike]) -> ChatSignals:
    """Rule-based stand-in for the LLM extraction workflow.

    The deterministic provider uses this so the project is fully runnable
    offline. Live LLM mode replaces it with semantic extraction.
    """
    customer = _customer_messages(messages)
    joined = " ".join(customer)
    lowered = joined.lower()

    complaint_messages = sum(1 for message in customer if COMPLAINT_RE.search(message))
    positive_hits = sum(1 for message in customer if POSITIVE_RE.search(message))
    escalation = bool(ESCALATION_RE.search(joined))
    unresolved = bool(UNRESOLVED_RE.search(joined))
    cancellation = bool(CANCELLATION_RE.search(joined))
    competitor = _find_competitor(joined)

    intents: list[str] = []
    for intent, keywords in INTENT_KEYWORDS:
        if any(keyword in lowered for keyword in keywords) and intent not in intents:
            intents.append(intent)
    if not intents:
        intents = ["general_question"]

    sentiment = 0.2 + 0.12 * positive_hits
    sentiment -= 0.12 * complaint_messages
    sentiment -= 0.25 if cancellation else 0.0
    sentiment -= 0.1 if unresolved else 0.0
    sentiment -= 0.15 if escalation else 0.0
    sentiment = round(_clamp(sentiment, -1.0, 1.0), 2)

    effort = 1
    effort += 1 if complaint_messages else 0
    effort += 1 if unresolved else 0
    effort += 1 if cancellation else 0
    effort += 1 if escalation else 0
    effort = int(_clamp(effort, 1, 5))

    if cancellation:
        summary = "Customer signals intent to cancel or switch provider."
    elif complaint_messages:
        summary = "Customer reports a problem with the service."
    else:
        summary = "Customer contacted support for information."
    if unresolved:
        summary += " The issue remains unresolved."
    if competitor:
        summary += f" Mentions {competitor}."

    return ChatSignals(
        sentiment=sentiment,
        intents=intents,
        cancellation_intent=cancellation,
        competitor_mentioned=competitor,
        unresolved_issue=unresolved,
        effort_score=effort,
        summary=summary[:160],
    )


def build_features(
    customer: CustomerIn | CustomerOut,
    billing: Sequence[BillingRecordIn],
    messages: Sequence[ChatLike],
    signals: ChatSignals,
    now: datetime | None = None,
) -> CustomerFeatures:
    """Combine billing history, raw text statistics and LLM signals."""
    current = now or utcnow()
    cycles = sorted(billing, key=lambda record: record.period)[-3:]
    last_cycle = cycles[-1] if cycles else None

    usage_change = 0.0
    if len(cycles) >= 2 and cycles[0].usage_gb > 0:
        usage_change = (cycles[-1].usage_gb - cycles[0].usage_gb) / cycles[0].usage_gb * 100.0

    window_start = current - timedelta(days=30)
    contacts = 0
    last_ts: datetime | None = None
    for message in messages:
        ts = parse_iso(message.ts) if isinstance(message, ChatRecord) else (message.ts or current)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=current.tzinfo)
        last_ts = ts if last_ts is None else max(last_ts, ts)
        if message.role == "customer" and ts >= window_start:
            contacts += 1

    customer_text = _customer_messages(messages)
    complaint_count = sum(1 for message in customer_text if COMPLAINT_RE.search(message))
    escalation_count = sum(1 for message in customer_text if ESCALATION_RE.search(message))
    days_since_last_chat = (
        max(0.0, (current - last_ts).total_seconds() / 86_400.0) if last_ts else 30.0
    )

    return CustomerFeatures(
        customer_id=customer.customer_id,
        tenure_months=customer.tenure_months,
        mrr=customer.mrr,
        payment_failures_90d=sum(record.payment_failures for record in cycles),
        late_payments_90d=sum(record.late_payments for record in cycles),
        overage_charges_90d=round(sum(record.overage_charges for record in cycles), 2),
        discount_active=bool(last_cycle and last_cycle.discount_amount > 0),
        usage_change_pct=round(usage_change, 2),
        support_contacts_30d=contacts,
        chat_messages=len(messages),
        complaint_count=complaint_count,
        escalation_count=escalation_count,
        sentiment_score=signals.sentiment,
        cancellation_intent=signals.cancellation_intent,
        competitor_mentioned=signals.competitor_mentioned,
        unresolved_issue=signals.unresolved_issue,
        effort_score=signals.effort_score,
        days_since_last_chat=round(days_since_last_chat, 2),
    )

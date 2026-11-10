"""Pipeline orchestration: ingestion, seeding and per-customer analysis."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from . import repository
from .care import derive_care
from .features import build_features, extract_signals
from .llm import (
    PROMPT_VERSION,
    LLMError,
    StructuredLLM,
    build_llm_client,
    extract_chat_signals,
    recommend_offer,
    triage_care,
    write_churn_narrative,
)
from .models import (
    BillingRecordIn,
    ChatMessageIn,
    ChatRecord,
    ChatSignals,
    CustomerFeatures,
    CustomerIn,
    CustomerProfile,
    RiskScore,
)
from .offers import CATALOG_BY_CODE, build_recommendation, choose_offer, eligible_offers
from .scoring import assess_risk, narrative_for, with_narrative
from .seed import build_seed_customers
from .timeutil import to_iso, utcnow


class CustomerNotFound(LookupError):
    """Raised when an operation targets an unknown customer."""


def build_runner() -> StructuredLLM:
    return StructuredLLM(build_llm_client())


def create_customer(conn: sqlite3.Connection, payload: CustomerIn) -> None:
    repository.upsert_customer(conn, payload)
    conn.commit()


def add_chat(
    conn: sqlite3.Connection, customer_id: str, message: ChatMessageIn
) -> tuple[int, int]:
    if repository.get_customer(conn, customer_id) is None:
        raise CustomerNotFound(customer_id)
    ts = to_iso(message.ts or utcnow())
    repository.insert_chat(conn, customer_id, message, ts)
    conn.commit()
    return 1, repository.count_chats(conn, customer_id)


def add_billing(
    conn: sqlite3.Connection, customer_id: str, record: BillingRecordIn
) -> tuple[int, int]:
    if repository.get_customer(conn, customer_id) is None:
        raise CustomerNotFound(customer_id)
    repository.upsert_billing(conn, customer_id, record)
    conn.commit()
    return 1, repository.count_billing(conn, customer_id)


def _safe_signals(
    runner: StructuredLLM,
    customer_id: str,
    messages: Sequence[ChatRecord],
) -> ChatSignals:
    try:
        return extract_chat_signals(runner, customer_id, messages)
    except LLMError:
        return extract_signals(messages)


def _safe_narrative(
    runner: StructuredLLM,
    customer_id: str,
    risk: RiskScore,
    features: CustomerFeatures,
) -> tuple[str, str]:
    try:
        narrative = write_churn_narrative(runner, customer_id, risk, features)
    except LLMError:
        return narrative_for(risk, features)
    return narrative.rationale, narrative.save_play


def analyze_customer(
    conn: sqlite3.Connection,
    customer_id: str,
    runner: StructuredLLM,
    now: datetime | None = None,
) -> CustomerProfile:
    """Run the full AI pipeline for one customer and persist the result."""
    customer = repository.get_customer(conn, customer_id)
    if customer is None:
        raise CustomerNotFound(customer_id)

    billing = repository.list_billing(conn, customer_id)
    messages = repository.list_chats(conn, customer_id)

    signals = _safe_signals(runner, customer_id, messages)
    features = build_features(customer, billing, messages, signals, now)
    risk = assess_risk(features)

    rationale, save_play = _safe_narrative(runner, customer_id, risk, features)
    churn = with_narrative(risk, features, rationale, save_play)

    offers = eligible_offers(features, risk)
    offer = None
    if offers:
        try:
            choice = recommend_offer(runner, customer_id, risk, features, offers)
        except LLMError:
            choice = choose_offer(offers, risk.top_drivers, features)
        selected = CATALOG_BY_CODE[choice.code]
        offer = build_recommendation(selected, choice, risk)

    try:
        care = triage_care(runner, customer_id, messages, risk)
    except LLMError:
        care = derive_care(messages, risk)

    profile = CustomerProfile(
        customer=customer,
        features=features,
        chat_signals=signals,
        churn=churn,
        offer=offer,
        care=care,
        analyzed_at=to_iso(now or utcnow()),
        prompt_version=PROMPT_VERSION,
    )
    repository.save_analysis(conn, profile)
    conn.commit()
    return profile


def analyze_all(
    conn: sqlite3.Connection,
    runner: StructuredLLM,
    limit: int = 100,
    now: datetime | None = None,
) -> list[CustomerProfile]:
    customers = repository.list_customers(conn)[:limit]
    return [
        analyze_customer(conn, customer.customer_id, runner, now=now)
        for customer in customers
    ]


def seed_demo_data(
    conn: sqlite3.Connection,
    runner: StructuredLLM,
    reset: bool = False,
    now: datetime | None = None,
) -> dict[str, int]:
    """Populate the deterministic demo cohort and analyze every customer."""
    if reset:
        repository.reset_database(conn)

    current = now or utcnow()
    cohort = build_seed_customers(current)
    for entry in cohort:
        repository.upsert_customer(conn, entry.customer)
        for message in entry.messages:
            ts = to_iso(message.ts or current)
            repository.insert_chat(conn, entry.customer.customer_id, message, ts)
        for record in entry.billing:
            repository.upsert_billing(conn, entry.customer.customer_id, record)
    conn.commit()

    profiles = analyze_all(conn, runner, limit=len(cohort), now=current)
    critical = sum(1 for profile in profiles if profile.churn.band == "critical")
    return {
        "customers": len(cohort),
        "chats": sum(len(entry.messages) for entry in cohort),
        "billing": sum(len(entry.billing) for entry in cohort),
        "analyses": len(profiles),
        "critical": critical,
    }


def seed_if_empty(conn: sqlite3.Connection, runner: StructuredLLM) -> bool:
    row = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
    if row is not None and int(row["c"]) > 0:
        return False
    seed_demo_data(conn, runner)
    return True

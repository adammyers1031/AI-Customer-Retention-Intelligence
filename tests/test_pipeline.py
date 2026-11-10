"""End-to-end pipeline: seeding, ingestion and analysis orchestration."""

from __future__ import annotations

import sqlite3

import pytest

from customer_intelligence import repository
from customer_intelligence.models import BillingRecordIn, ChatMessageIn
from customer_intelligence.scoring import SAVE_PLAYS
from customer_intelligence.services import (
    CustomerNotFound,
    add_billing,
    add_chat,
    analyze_all,
    analyze_customer,
    build_runner,
    seed_demo_data,
    seed_if_empty,
)


def test_seed_demo_data_counts(conn: sqlite3.Connection) -> None:
    counts = seed_demo_data(conn, build_runner(), reset=True)
    assert counts == {
        "customers": 12,
        "chats": 41,
        "billing": 36,
        "analyses": 12,
        "critical": 1,
    }


def test_seed_if_empty_is_idempotent(conn: sqlite3.Connection) -> None:
    runner = build_runner()
    assert seed_if_empty(conn, runner) is True
    assert seed_if_empty(conn, runner) is False
    assert len(repository.list_customers(conn)) == 12


def test_seeded_profiles_cover_the_risk_spectrum(seeded_conn: sqlite3.Connection) -> None:
    profiles = [
        repository.get_analysis(seeded_conn, customer.customer_id)
        for customer in repository.list_customers(seeded_conn)
    ]
    valid = [profile for profile in profiles if profile is not None]
    bands = {profile.churn.band for profile in valid}
    offer_types = {profile.offer.offer_type for profile in valid if profile.offer}

    assert bands == {"low", "medium", "high", "critical"}
    assert offer_types == {"retention", "care", "upsell"}
    assert any(profile.care.category == "billing_dispute" for profile in valid)


def test_every_profile_is_internally_consistent(seeded_conn: sqlite3.Connection) -> None:
    for customer in repository.list_customers(seeded_conn):
        profile = repository.get_analysis(seeded_conn, customer.customer_id)
        assert profile is not None
        assert profile.customer.customer_id == customer.customer_id
        assert profile.features.customer_id == customer.customer_id
        assert 0.0 <= profile.churn.score <= 100.0
        assert profile.churn.band in {"low", "medium", "high", "critical"}
        assert profile.churn.save_play == SAVE_PLAYS[profile.churn.band]
        assert 1 <= profile.care.sla_hours <= 168
        assert profile.prompt_version


def test_analysis_is_deterministic(seeded_conn: sqlite3.Connection) -> None:
    runner = build_runner()
    first = analyze_customer(seeded_conn, "CUST-0004", runner)
    second = analyze_customer(seeded_conn, "CUST-0004", runner)
    assert first.churn.score == second.churn.score
    assert first.churn.band == second.churn.band
    assert first.offer is not None and second.offer is not None
    assert first.offer.code == second.offer.code


def test_analyze_unknown_customer_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(CustomerNotFound):
        analyze_customer(conn, "CUST-404", build_runner())


def test_analyze_all_respects_limit(seeded_conn: sqlite3.Connection) -> None:
    profiles = analyze_all(seeded_conn, build_runner(), limit=3)
    assert len(profiles) == 3


def test_ingestion_requires_known_customer(conn: sqlite3.Connection) -> None:
    message = ChatMessageIn(role="customer", message="Hi")
    record = BillingRecordIn(period="2026-11", invoiced_amount=10.0, paid_amount=10.0)
    with pytest.raises(CustomerNotFound):
        add_chat(conn, "CUST-404", message)
    with pytest.raises(CustomerNotFound):
        add_billing(conn, "CUST-404", record)


def test_ingestion_updates_existing_customer(seeded_conn: sqlite3.Connection) -> None:
    before = repository.count_chats(seeded_conn, "CUST-0001")
    stored, total = add_chat(
        seeded_conn,
        "CUST-0001",
        ChatMessageIn(role="customer", message="One more question about autopay."),
    )
    assert stored == 1
    assert total == before + 1

    periods_before = repository.count_billing(seeded_conn, "CUST-0001")
    stored, total = add_billing(
        seeded_conn,
        "CUST-0001",
        BillingRecordIn(period="2026-11", invoiced_amount=89.0, paid_amount=89.0),
    )
    assert stored == 1
    assert total in {periods_before, periods_before + 1}

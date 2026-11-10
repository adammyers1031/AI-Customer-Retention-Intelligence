"""SQLite persistence, summaries and aggregate metrics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from customer_intelligence import repository
from customer_intelligence.models import BillingRecordIn, ChatMessageIn, CustomerIn


def _customer(**overrides: object) -> CustomerIn:
    payload: dict[str, object] = {
        "customer_id": "CUST-2000",
        "name": "Repo Tester",
        "email": "repo@example.com",
        "plan": "standard",
        "mrr": 49.0,
        "tenure_months": 10,
    }
    payload.update(overrides)
    return CustomerIn(**payload)


def test_init_db_creates_parent_directory(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "deeper" / "test.db"
    connection = repository.init_db(db)
    try:
        assert db.exists()
    finally:
        connection.close()


def test_customer_round_trip_and_upsert(conn: sqlite3.Connection) -> None:
    repository.upsert_customer(conn, _customer())
    conn.commit()

    stored = repository.get_customer(conn, "CUST-2000")
    assert stored is not None
    assert stored.name == "Repo Tester"
    assert stored.marketing_opt_in is True

    repository.upsert_customer(conn, _customer(name="Renamed", mrr=59.0))
    conn.commit()
    renamed = repository.get_customer(conn, "CUST-2000")
    assert renamed is not None
    assert renamed.name == "Renamed"
    assert renamed.mrr == 59.0

    assert [item.customer_id for item in repository.list_customers(conn)] == ["CUST-2000"]
    assert repository.get_customer(conn, "MISSING") is None


def test_chat_storage_and_order(conn: sqlite3.Connection) -> None:
    repository.upsert_customer(conn, _customer())
    first = ChatMessageIn(role="customer", message="First")
    second = ChatMessageIn(role="agent", message="Second")

    repository.insert_chat(conn, "CUST-2000", second, "2026-11-02T10:00:00Z")
    repository.insert_chat(conn, "CUST-2000", first, "2026-11-01T10:00:00Z")
    conn.commit()

    assert repository.count_chats(conn, "CUST-2000") == 2
    stored = repository.list_chats(conn, "CUST-2000")
    assert [item.message for item in stored] == ["First", "Second"]
    assert stored[0].role == "customer"


def test_billing_upsert_is_idempotent(conn: sqlite3.Connection) -> None:
    repository.upsert_customer(conn, _customer())
    record = BillingRecordIn(
        period="2026-10",
        invoiced_amount=49.0,
        paid_amount=49.0,
        usage_gb=100.0,
    )
    repository.upsert_billing(conn, "CUST-2000", record)
    conn.commit()

    updated = record.model_copy(
        update={"payment_failures": 2, "usage_gb": 80.0, "overage_charges": 5.0}
    )
    repository.upsert_billing(conn, "CUST-2000", updated)
    conn.commit()

    assert repository.count_billing(conn, "CUST-2000") == 1
    stored = repository.list_billing(conn, "CUST-2000")
    assert stored[0].payment_failures == 2
    assert stored[0].usage_gb == 80.0
    assert stored[0].overage_charges == 5.0


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repository.insert_chat(
            conn,
            "UNKNOWN",
            ChatMessageIn(role="customer", message="x"),
            "2026-11-01T10:00:00Z",
        )


def test_seeded_analysis_round_trip(seeded_conn: sqlite3.Connection) -> None:
    profile = repository.get_analysis(seeded_conn, "CUST-0004")
    assert profile is not None
    assert profile.churn.band == "critical"
    assert profile.customer.name == "Daniel Kim"
    assert profile.offer is not None
    assert profile.prompt_version

    assert repository.get_analysis(seeded_conn, "CUST-9999") is None


def test_summaries_are_ranked_and_filtered(seeded_conn: sqlite3.Connection) -> None:
    summaries = repository.list_summaries(seeded_conn)
    assert len(summaries) == 12
    assert summaries[0].customer_id == "CUST-0004"
    scores = [item.score for item in summaries]
    assert scores == sorted(scores, reverse=True)

    critical = repository.list_summaries(seeded_conn, band="critical")
    assert [item.customer_id for item in critical] == ["CUST-0004"]

    limited = repository.list_summaries(seeded_conn, limit=5)
    assert len(limited) == 5


def test_metrics_aggregate(seeded_conn: sqlite3.Connection) -> None:
    metrics = repository.summary_metrics(seeded_conn)
    assert metrics.total_customers == 12
    assert metrics.analyzed_customers == 12
    assert metrics.avg_risk_score == pytest.approx(29.0, abs=0.1)
    assert metrics.high_risk_customers == 3
    assert metrics.at_risk_mrr == pytest.approx(249.0 + 189.0 + 54.0)
    assert metrics.band_counts == {"critical": 1, "high": 2, "medium": 2, "low": 7}
    assert metrics.offers_by_type == {"retention": 4, "care": 1, "upsell": 3}
    assert sum(metrics.care_categories.values()) == 12


def test_metrics_on_empty_database(conn: sqlite3.Connection) -> None:
    metrics = repository.summary_metrics(conn)
    assert metrics.total_customers == 0
    assert metrics.analyzed_customers == 0
    assert metrics.avg_risk_score == 0.0
    assert metrics.at_risk_mrr == 0.0
    assert metrics.band_counts == {}


def test_reset_database_clears_all_tables(seeded_conn: sqlite3.Connection) -> None:
    repository.reset_database(seeded_conn)
    assert repository.list_customers(seeded_conn) == []
    assert repository.list_summaries(seeded_conn) == []
    metrics = repository.summary_metrics(seeded_conn)
    assert metrics.total_customers == 0

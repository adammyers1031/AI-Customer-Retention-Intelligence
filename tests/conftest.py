"""Shared fixtures for the test suite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from customer_intelligence import repository
from customer_intelligence.api import create_app
from customer_intelligence.models import CustomerFeatures
from customer_intelligence.services import build_runner, seed_demo_data


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = repository.init_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def runner():
    return build_runner()


@pytest.fixture()
def seeded_conn(conn: sqlite3.Connection, runner) -> sqlite3.Connection:
    seed_demo_data(conn, runner, reset=True)
    return conn


@pytest.fixture()
def client(db_path: Path, runner) -> Iterator[TestClient]:
    app = create_app(db_path=db_path, auto_seed=True, runner=runner)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def empty_client(db_path: Path, runner) -> Iterator[TestClient]:
    app = create_app(db_path=db_path, auto_seed=False, runner=runner)
    with TestClient(app) as test_client:
        yield test_client


def make_features(**overrides: object) -> CustomerFeatures:
    """Healthy-customer feature vector; override fields per test."""
    base: dict[str, object] = {
        "customer_id": "CUST-TEST",
        "tenure_months": 36,
        "mrr": 50.0,
        "payment_failures_90d": 0,
        "late_payments_90d": 0,
        "overage_charges_90d": 0.0,
        "discount_active": False,
        "usage_change_pct": 0.0,
        "support_contacts_30d": 0,
        "chat_messages": 2,
        "complaint_count": 0,
        "escalation_count": 0,
        "sentiment_score": 0.5,
        "cancellation_intent": False,
        "competitor_mentioned": None,
        "unresolved_issue": False,
        "effort_score": 1,
        "days_since_last_chat": 2.0,
    }
    base.update(overrides)
    return CustomerFeatures.model_validate(base)

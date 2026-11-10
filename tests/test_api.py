"""FastAPI endpoints: payloads, status codes and error handling."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _customer_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "customer_id": "CUST-3000",
        "name": "API Tester",
        "email": "api@example.com",
        "plan": "standard",
        "mrr": 49.0,
        "tenure_months": 8,
    }
    payload.update(overrides)
    return payload


def test_health_and_root(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "deterministic"
    assert body["version"]

    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["docs"] == "/docs"


def test_customer_list_is_ranked_and_seeded(client: TestClient) -> None:
    response = client.get("/api/v1/customers")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 12
    assert rows[0]["customer_id"] == "CUST-0004"
    assert rows[0]["band"] == "critical"


def test_customer_list_filters_and_limits(client: TestClient) -> None:
    filtered = client.get("/api/v1/customers", params={"risk_band": "critical"})
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    limited = client.get("/api/v1/customers", params={"limit": 3})
    assert len(limited.json()) == 3

    invalid = client.get("/api/v1/customers", params={"limit": 0})
    assert invalid.status_code == 422

    invalid_band = client.get("/api/v1/customers", params={"risk_band": "severe"})
    assert invalid_band.status_code == 422


def test_get_single_customer(client: TestClient) -> None:
    response = client.get("/api/v1/customers/CUST-0001")
    assert response.status_code == 200
    assert response.json()["name"] == "Olivia Chen"

    missing = client.get("/api/v1/customers/CUST-9999")
    assert missing.status_code == 404


def test_profile_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/customers/CUST-0004/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["churn"]["band"] == "critical"
    assert body["offer"]["code"] == "WINBACK_CALL"
    assert body["care"]["urgency"] == "urgent"

    missing = client.get("/api/v1/customers/CUST-9999/profile")
    assert missing.status_code == 404


def test_analyze_one_customer(client: TestClient) -> None:
    response = client.post("/api/v1/customers/CUST-0002/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["customer"]["customer_id"] == "CUST-0002"
    assert body["churn"]["score"] > 0
    assert body["prompt_version"]

    missing = client.post("/api/v1/customers/CUST-9999/analyze")
    assert missing.status_code == 404


def test_analyze_all_customers(client: TestClient) -> None:
    response = client.post("/api/v1/analyze")
    assert response.status_code == 200
    assert len(response.json()) == 12


def test_metrics_summary(client: TestClient) -> None:
    response = client.get("/api/v1/metrics/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_customers"] == 12
    assert body["analyzed_customers"] == 12
    assert body["high_risk_customers"] == 3
    assert body["at_risk_mrr"] == 492.0
    assert body["band_counts"]["critical"] == 1


def test_offer_catalogue_and_prompt_metadata(client: TestClient) -> None:
    offers = client.get("/api/v1/offers")
    assert offers.status_code == 200
    codes = {offer["code"] for offer in offers.json()}
    assert "WINBACK_CALL" in codes
    assert "BILL_CREDIT" in codes

    prompts = client.get("/api/v1/prompts")
    assert prompts.status_code == 200
    body = prompts.json()
    assert set(body["workflows"]) == {
        "chat_extraction",
        "churn_narrative",
        "care_triage",
        "offer_recommendation",
    }
    assert body["version"]


def test_create_customer_and_validation(empty_client: TestClient) -> None:
    created = empty_client.post("/api/v1/customers", json=_customer_payload())
    assert created.status_code == 201
    assert created.json() == {"customer_id": "CUST-3000", "status": "created"}

    fetched = empty_client.get("/api/v1/customers/CUST-3000")
    assert fetched.status_code == 200
    assert fetched.json()["mrr"] == 49.0

    duplicate = empty_client.post("/api/v1/customers", json=_customer_payload(name="Renamed"))
    assert duplicate.status_code == 201
    assert empty_client.get("/api/v1/customers/CUST-3000").json()["name"] == "Renamed"

    invalid = empty_client.post(
        "/api/v1/customers", json=_customer_payload(customer_id="bad id")
    )
    assert invalid.status_code == 422

    extra = empty_client.post(
        "/api/v1/customers", json=_customer_payload(unexpected="field")
    )
    assert extra.status_code == 422


def test_chat_ingestion(empty_client: TestClient) -> None:
    empty_client.post("/api/v1/customers", json=_customer_payload())

    missing = empty_client.post(
        "/api/v1/customers/CUST-9999/chats",
        json={"role": "customer", "message": "Hi"},
    )
    assert missing.status_code == 404

    accepted = empty_client.post(
        "/api/v1/customers/CUST-3000/chats",
        json={"role": "customer", "message": "My router keeps dropping."},
    )
    assert accepted.status_code == 201
    assert accepted.json()["total_messages"] == 1

    invalid = empty_client.post(
        "/api/v1/customers/CUST-3000/chats",
        json={"role": "customer", "message": ""},
    )
    assert invalid.status_code == 422

    messages = empty_client.get("/api/v1/customers/CUST-3000/chats")
    assert messages.status_code == 200
    assert messages.json()[0]["message"] == "My router keeps dropping."

    missing_chats = empty_client.get("/api/v1/customers/CUST-9999/chats")
    assert missing_chats.status_code == 404


def test_billing_ingestion(empty_client: TestClient) -> None:
    empty_client.post("/api/v1/customers", json=_customer_payload())
    payload = {
        "period": "2026-11",
        "invoiced_amount": 49.0,
        "paid_amount": 49.0,
        "payment_failures": 1,
        "usage_gb": 120.0,
    }

    missing = empty_client.post("/api/v1/customers/CUST-9999/billing", json=payload)
    assert missing.status_code == 404

    accepted = empty_client.post("/api/v1/customers/CUST-3000/billing", json=payload)
    assert accepted.status_code == 201
    assert accepted.json()["total_periods"] == 1

    invalid = empty_client.post(
        "/api/v1/customers/CUST-3000/billing",
        json={**payload, "period": "November"},
    )
    assert invalid.status_code == 422

    periods = empty_client.get("/api/v1/customers/CUST-3000/billing")
    assert periods.status_code == 200
    assert periods.json()[0]["period"] == "2026-11"

    missing_billing = empty_client.get("/api/v1/customers/CUST-9999/billing")
    assert missing_billing.status_code == 404


def test_empty_database_metrics(empty_client: TestClient) -> None:
    metrics = empty_client.get("/api/v1/metrics/summary")
    assert metrics.status_code == 200
    assert metrics.json()["total_customers"] == 0
    assert empty_client.get("/api/v1/customers").json() == []


def test_analyze_after_ingestion(empty_client: TestClient) -> None:
    empty_client.post("/api/v1/customers", json=_customer_payload())
    empty_client.post(
        "/api/v1/customers/CUST-3000/chats",
        json={"role": "customer", "message": "I want to cancel and switch to NovaNet."},
    )
    empty_client.post(
        "/api/v1/customers/CUST-3000/billing",
        json={
            "period": "2026-11",
            "invoiced_amount": 49.0,
            "paid_amount": 0.0,
            "payment_failures": 2,
            "usage_gb": 50.0,
        },
    )

    analyzed = empty_client.post("/api/v1/customers/CUST-3000/analyze")
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["chat_signals"]["cancellation_intent"] is True
    assert body["features"]["payment_failures_90d"] == 2
    assert body["care"]["category"] == "cancellation_request"

    profile = empty_client.get("/api/v1/customers/CUST-3000/profile")
    assert profile.status_code == 200

    all_analyzed = empty_client.post("/api/v1/analyze")
    assert all_analyzed.status_code == 200
    assert len(all_analyzed.json()) == 1

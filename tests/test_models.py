"""Contract validation tests for the Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from customer_intelligence.models import (
    BillingRecordIn,
    ChatMessageIn,
    ChatSignals,
    CustomerIn,
    CustomerProfile,
    OfferDefinition,
    RiskScore,
)


def _customer_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "customer_id": "CUST-1000",
        "name": "Test User",
        "plan": "standard",
        "mrr": 49.0,
        "tenure_months": 12,
    }
    payload.update(overrides)
    return payload


def test_customer_accepts_valid_payload() -> None:
    customer = CustomerIn(**_customer_payload())
    assert customer.customer_id == "CUST-1000"
    assert customer.region == "us-east"
    assert customer.marketing_opt_in is True


@pytest.mark.parametrize(
    "customer_id",
    ["ab", "with space", "semi;colon", "$dollar"],
)
def test_customer_rejects_bad_ids(customer_id: str) -> None:
    with pytest.raises(ValidationError):
        CustomerIn(**_customer_payload(customer_id=customer_id))


def test_customer_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CustomerIn(**_customer_payload(unknown="x"))


def test_customer_rejects_unknown_plan() -> None:
    with pytest.raises(ValidationError):
        CustomerIn(**_customer_payload(plan="platinum"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mrr", -1.0),
        ("mrr", 2_000_000.0),
        ("tenure_months", -1),
        ("tenure_months", 601),
        ("name", ""),
    ],
)
def test_customer_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CustomerIn(**_customer_payload(**{field: value}))


def test_chat_message_defaults_and_validation() -> None:
    message = ChatMessageIn(role="customer", message="Hello")
    assert message.channel == "chat"
    assert message.ts is None

    with pytest.raises(ValidationError):
        ChatMessageIn(role="robot", message="Hello")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ChatMessageIn(role="customer", message="")


def test_billing_period_pattern() -> None:
    record = BillingRecordIn(
        period="2026-11",
        invoiced_amount=49.0,
        paid_amount=49.0,
    )
    assert record.payment_failures == 0

    with pytest.raises(ValidationError):
        BillingRecordIn(
            period="2026-1",
            invoiced_amount=49.0,
            paid_amount=49.0,
        )


def test_chat_signals_bounds() -> None:
    payload = {
        "sentiment": -0.5,
        "intents": ["technical_issue"],
        "cancellation_intent": False,
        "competitor_mentioned": None,
        "unresolved_issue": True,
        "effort_score": 3,
        "summary": "Customer reports an outage.",
    }
    assert ChatSignals(**payload).effort_score == 3

    with pytest.raises(ValidationError):
        ChatSignals(**{**payload, "sentiment": -2.0})

    with pytest.raises(ValidationError):
        ChatSignals(**{**payload, "effort_score": 9})


def test_risk_score_bounds() -> None:
    with pytest.raises(ValidationError):
        RiskScore(score=101.0, band="critical", factors=[], top_drivers=[])

    with pytest.raises(ValidationError):
        RiskScore(score=10.0, band="severe", factors=[], top_drivers=[])  # type: ignore[arg-type]


def test_offer_definition_round_trip() -> None:
    offer = OfferDefinition(
        code="TEST",
        name="Test offer",
        offer_type="retention",
        discount_pct=10.0,
        duration_months=3,
        description="A test offer.",
        tags=["price_sensitivity"],
        eligible_bands=["high"],
        base_lift_pct=10.0,
    )
    assert OfferDefinition.model_validate(offer.model_dump()) == offer


def test_customer_profile_is_serialisable() -> None:
    assert "prompt_version" in CustomerProfile.model_fields

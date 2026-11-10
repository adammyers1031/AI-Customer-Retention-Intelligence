"""Feature extraction from transcripts and billing history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import make_features

from customer_intelligence.features import build_features, extract_signals
from customer_intelligence.models import (
    BillingRecordIn,
    ChatMessageIn,
    CustomerIn,
    CustomerOut,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _msg(
    role: str,
    message: str,
    days_ago: float = 1.0,
    channel: str = "chat",
) -> ChatMessageIn:
    return ChatMessageIn(
        role=role,  # type: ignore[arg-type]
        message=message,
        channel=channel,  # type: ignore[arg-type]
        ts=NOW - timedelta(days=days_ago),
    )


def _billing(
    period: str,
    usage: float,
    *,
    failures: int = 0,
    late: int = 0,
    overage: float = 0.0,
    discount: float = 0.0,
    invoice: float = 50.0,
) -> BillingRecordIn:
    return BillingRecordIn(
        period=period,
        invoiced_amount=invoice,
        paid_amount=invoice,
        payment_failures=failures,
        late_payments=late,
        overage_charges=overage,
        discount_amount=discount,
        usage_gb=usage,
    )


def _customer(**overrides: object) -> CustomerIn:
    payload: dict[str, object] = {
        "customer_id": "CUST-1000",
        "name": "Test User",
        "plan": "standard",
        "mrr": 49.0,
        "tenure_months": 12,
    }
    payload.update(overrides)
    return CustomerIn(**payload)


def test_extract_signals_detects_cancellation_and_competitor() -> None:
    messages = [
        _msg("customer", "I am going to cancel and switch to NovaNet."),
        _msg("agent", "Let me help you first."),
    ]
    signals = extract_signals(messages)
    assert signals.cancellation_intent is True
    assert signals.competitor_mentioned == "NovaNet"
    assert signals.sentiment < 0
    assert "cancellation" in signals.intents


def test_extract_signals_detects_unresolved_escalation() -> None:
    messages = [
        _msg("customer", "The service is still broken and I am waiting for a manager."),
    ]
    signals = extract_signals(messages)
    assert signals.unresolved_issue is True
    assert signals.effort_score >= 4
    assert "technical_issue" in signals.intents
    assert "complaint" in signals.intents


def test_extract_signals_positive_message() -> None:
    signals = extract_signals([_msg("customer", "Thank you, the new router is great!")])
    assert signals.sentiment > 0
    assert signals.cancellation_intent is False
    assert signals.effort_score == 1


def test_extract_signals_defaults_to_general_question() -> None:
    signals = extract_signals([_msg("customer", "Hello, I have a question.")])
    assert signals.intents == ["general_question"]


def test_extract_signals_summary_is_capped() -> None:
    long_message = "cancel " + "verylongword " * 40
    signals = extract_signals([_msg("customer", long_message)])
    assert len(signals.summary) <= 160


def test_build_features_aggregates_billing() -> None:
    customer = _customer()
    billing = [
        _billing("2026-09", 100.0, failures=1, late=1, overage=10.0),
        _billing("2026-10", 80.0, failures=1, late=0, overage=5.0),
        _billing("2026-11", 50.0, discount=10.0, overage=15.0),
    ]
    signals = extract_signals([_msg("customer", "The bill is wrong.", days_ago=2)])
    features = build_features(customer, billing, [_msg("customer", "Bill", 2)], signals, NOW)

    assert features.payment_failures_90d == 2
    assert features.late_payments_90d == 1
    assert features.overage_charges_90d == 30.0
    assert features.discount_active is True
    # usage 100 -> 50 is a 50% decline
    assert features.usage_change_pct == -50.0


def test_build_features_usage_growth_and_single_cycle() -> None:
    customer = _customer()
    signals = extract_signals([_msg("customer", "Thanks, all good.")])
    features = build_features(
        customer,
        [_billing("2026-11", 120.0)],
        [_msg("customer", "Thanks", 1)],
        signals,
        NOW,
    )
    assert features.usage_change_pct == 0.0


def test_build_features_contacts_window() -> None:
    customer = _customer()
    messages = [
        _msg("customer", "old question", days_ago=40),
        _msg("customer", "recent one", days_ago=2),
        _msg("customer", "recent two", days_ago=1),
        _msg("agent", "agent reply", days_ago=1),
    ]
    signals = extract_signals(messages)
    features = build_features(customer, [], messages, signals, NOW)
    assert features.support_contacts_30d == 2
    assert features.chat_messages == 4
    assert features.days_since_last_chat == 1.0


def test_build_features_no_chat_defaults_days() -> None:
    customer = _customer()
    signals = extract_signals([])
    features = build_features(customer, [], [], signals, NOW)
    assert features.days_since_last_chat == 30.0
    assert features.support_contacts_30d == 0


def test_build_features_accepts_customer_out() -> None:
    customer = CustomerOut(
        customer_id="CUST-1000",
        name="Test User",
        email=None,
        plan="standard",
        mrr=49.0,
        tenure_months=12,
        region="us-east",
        marketing_opt_in=True,
        created_at="2026-11-01T00:00:00Z",
    )
    signals = extract_signals([_msg("customer", "Thanks")])
    features = build_features(customer, [], [_msg("customer", "Thanks")], signals, NOW)
    assert features.customer_id == "CUST-1000"


def test_make_features_fixture_is_valid() -> None:
    features = make_features()
    assert features.sentiment_score == 0.5

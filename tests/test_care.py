"""Care triage categories, urgency and SLA policy."""

from __future__ import annotations

import pytest

from customer_intelligence.care import ACTIONS, SLA_HOURS, derive_care
from customer_intelligence.models import ChatMessageIn, RiskScore


def _risk(band: str = "low", score: float = 10.0) -> RiskScore:
    return RiskScore(
        score=score,
        band=band,  # type: ignore[arg-type]
        factors=[],
        top_drivers=[],
    )


def _messages(*texts: str) -> list[ChatMessageIn]:
    return [ChatMessageIn(role="customer", message=text) for text in texts]


def test_sla_table_is_consistent() -> None:
    assert SLA_HOURS == {"urgent": 2, "high": 8, "normal": 24, "low": 48}
    assert set(ACTIONS) == {
        "cancellation_request",
        "billing_dispute",
        "technical_issue",
        "plan_change",
        "general_question",
    }


def test_cancellation_request_is_urgent() -> None:
    care = derive_care(_messages("I want to cancel my contract right now."), _risk())
    assert care.category == "cancellation_request"
    assert care.urgency == "urgent"
    assert care.escalate is True
    assert care.sla_hours == 2


def test_billing_dispute_detection() -> None:
    care = derive_care(
        _messages("I was charged twice this month and I want a refund."),
        _risk(),
    )
    assert care.category == "billing_dispute"
    assert care.urgency == "high"
    assert care.sla_hours == 8


def test_routine_billing_question_is_not_a_dispute() -> None:
    care = derive_care(
        _messages("Can you explain how the autopay discount appears on my invoice?"),
        _risk(),
    )
    assert care.category == "general_question"


def test_technical_issue_detection() -> None:
    care = derive_care(
        _messages("The router connection keeps dropping and the speed is slow."),
        _risk(),
    )
    assert care.category == "technical_issue"
    assert care.urgency == "high"


def test_plan_change_detection() -> None:
    care = derive_care(
        _messages("I would like to downgrade to a cheaper plan."),
        _risk(),
    )
    assert care.category == "plan_change"
    assert care.urgency == "low"
    assert care.escalate is False
    assert care.sla_hours == 48


def test_general_question_is_low_effort() -> None:
    care = derive_care(_messages("How do I update my email address?"), _risk())
    assert care.category == "general_question"
    assert care.urgency == "low"
    assert care.sla_hours == 48


def test_cancellation_beats_billing_keywords() -> None:
    care = derive_care(
        _messages("I have been charged twice and I will cancel if this is not fixed."),
        _risk(),
    )
    assert care.category == "cancellation_request"
    assert care.urgency == "urgent"


def test_critical_band_forces_urgent() -> None:
    care = derive_care(_messages("How does the service work?"), _risk("critical", 70.0))
    assert care.urgency == "urgent"
    assert care.escalate is True


def test_high_band_forces_high() -> None:
    care = derive_care(_messages("How does the service work?"), _risk("high", 50.0))
    assert care.urgency == "high"
    assert care.sla_hours == 8


def test_medium_band_is_normal() -> None:
    care = derive_care(_messages("How does the service work?"), _risk("medium", 30.0))
    assert care.urgency == "normal"
    assert care.sla_hours == 24


def test_unresolved_high_score_promotes_to_urgent() -> None:
    care = derive_care(
        _messages("Nobody has responded and the problem is still open."),
        _risk("high", 65.0),
    )
    assert care.urgency == "urgent"


def test_next_best_action_is_specific() -> None:
    care = derive_care(_messages("The connection is down."), _risk())
    assert care.next_best_action == ACTIONS["technical_issue"]
    assert len(care.next_best_action) <= 200


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("I want to leave you and switch to SkyLink.", "cancellation_request"),
        ("There is a charge I did not approve and I need a refund.", "billing_dispute"),
        ("My wifi is not working again.", "technical_issue"),
        ("Can I add another package to my plan?", "plan_change"),
    ],
)
def test_category_matrix(text: str, category: str) -> None:
    care = derive_care(_messages(text), _risk())
    assert care.category == category

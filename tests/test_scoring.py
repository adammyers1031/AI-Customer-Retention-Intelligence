"""Churn scoring: factor contributions, bands and narratives."""

from __future__ import annotations

import pytest
from conftest import make_features
from pydantic import ValidationError

from customer_intelligence.models import RiskScore
from customer_intelligence.scoring import (
    BAND_THRESHOLDS,
    FACTOR_WEIGHTS,
    SAVE_PLAYS,
    assess_risk,
    band_for,
    narrative_for,
    with_narrative,
)


def test_weights_sum_to_one_hundred() -> None:
    assert sum(FACTOR_WEIGHTS.values()) == pytest.approx(100.0)
    assert len(FACTOR_WEIGHTS) == len(BAND_THRESHOLDS) + 3


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, "critical"),
        (60.0, "critical"),
        (59.9, "high"),
        (45.0, "high"),
        (44.9, "medium"),
        (25.0, "medium"),
        (24.9, "low"),
        (0.0, "low"),
    ],
)
def test_band_thresholds(score: float, expected: str) -> None:
    assert band_for(score) == expected


def test_assess_risk_contributions_reconcile() -> None:
    risk = assess_risk(make_features())
    assert len(risk.factors) == len(FACTOR_WEIGHTS)
    for factor in risk.factors:
        assert factor.contribution == pytest.approx(factor.weight * factor.value, abs=0.01)
    assert risk.score == pytest.approx(
        round(min(100.0, sum(factor.contribution for factor in risk.factors)), 1)
    )
    assert risk.band == band_for(risk.score)


def test_healthy_customer_is_low_risk() -> None:
    risk = assess_risk(make_features())
    assert risk.score < 25.0
    assert risk.band == "low"


def test_distressed_customer_is_critical() -> None:
    features = make_features(
        tenure_months=0,
        mrr=100.0,
        payment_failures_90d=3,
        late_payments_90d=4,
        overage_charges_90d=300.0,
        discount_active=True,
        usage_change_pct=-60.0,
        support_contacts_30d=9,
        complaint_count=5,
        escalation_count=2,
        sentiment_score=-1.0,
        cancellation_intent=True,
        competitor_mentioned="NovaNet",
        unresolved_issue=True,
        effort_score=5,
    )
    risk = assess_risk(features)
    assert risk.band == "critical"
    assert risk.score >= 60.0


def test_score_is_monotonic_in_support_friction() -> None:
    calm = assess_risk(make_features(support_contacts_30d=0, complaint_count=0))
    noisy = assess_risk(make_features(support_contacts_30d=6, complaint_count=4))
    assert noisy.score > calm.score


def test_top_drivers_are_ranked() -> None:
    risk = assess_risk(
        make_features(
            sentiment_score=-0.9,
            unresolved_issue=True,
            support_contacts_30d=8,
            complaint_count=4,
            effort_score=5,
        )
    )
    assert risk.top_drivers
    assert len(risk.top_drivers) <= 3
    contributions = {factor.name: factor.contribution for factor in risk.factors}
    assert contributions[risk.top_drivers[0]] >= contributions[risk.top_drivers[-1]]


def test_score_never_exceeds_one_hundred() -> None:
    features = make_features(
        payment_failures_90d=100,
        late_payments_90d=100,
        overage_charges_90d=1_000_000.0,
        discount_active=True,
        usage_change_pct=-100.0,
        support_contacts_30d=100,
        complaint_count=100,
        effort_score=5,
        sentiment_score=-1.0,
        cancellation_intent=True,
        competitor_mentioned="NovaNet",
        tenure_months=0,
    )
    risk = assess_risk(features)
    assert risk.score <= 100.0


def test_narrative_mentions_score_and_band() -> None:
    features = make_features(sentiment_score=-0.8, unresolved_issue=True)
    risk = assess_risk(features)
    rationale, save_play = narrative_for(risk, features)
    assert f"{risk.score:.1f}/100" in rationale
    assert risk.band in rationale
    assert save_play == SAVE_PLAYS[risk.band]


def test_narrative_flags_cancellation_language() -> None:
    features = make_features(cancellation_intent=True)
    risk = assess_risk(features)
    rationale, _ = narrative_for(risk, features)
    assert "cancel" in rationale.lower()


def test_with_narrative_preserves_governed_score() -> None:
    features = make_features()
    risk = assess_risk(features)
    assessment = with_narrative(risk, features, "Custom rationale.", "Custom play.")
    assert assessment.score == risk.score
    assert assessment.band == risk.band
    assert assessment.factors == risk.factors
    assert assessment.rationale == "Custom rationale."


def test_risk_score_requires_valid_band() -> None:
    with pytest.raises(ValidationError):
        RiskScore(score=10.0, band="unknown", factors=[], top_drivers=[])  # type: ignore[arg-type]

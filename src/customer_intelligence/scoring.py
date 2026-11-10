"""Multi-factor churn risk scoring.

The score is deliberately governance-friendly: every factor has a fixed weight,
a normalised value and an explicit contribution, and the final score is the sum
of those contributions. No LLM is allowed to change the number.
"""

from __future__ import annotations

from .models import ChurnAssessment, CustomerFeatures, RiskBand, RiskFactor, RiskScore

# Points available per factor. The weights sum to 100.
FACTOR_WEIGHTS: dict[str, float] = {
    "Payment reliability": 18.0,
    "Price sensitivity": 12.0,
    "Usage decline": 12.0,
    "Support friction": 22.0,
    "Sentiment": 22.0,
    "Churn language": 10.0,
    "Tenure stability": 4.0,
}

BAND_THRESHOLDS: tuple[tuple[float, RiskBand], ...] = (
    (60.0, "critical"),
    (45.0, "high"),
    (25.0, "medium"),
    (0.0, "low"),
)

SAVE_PLAYS: dict[str, str] = {
    "critical": (
        "Same-day human outreach from the retention desk; confirm the open issue "
        "and apply an approved save offer."
    ),
    "high": (
        "Proactive call within 48 hours; lead with the issue that caused the "
        "friction, then present one offer."
    ),
    "medium": (
        "Targeted nurture within five days; check in on the service and offer a "
        "right-sized plan."
    ),
    "low": (
        "No save intervention needed; open a growth conversation at the next "
        "natural touchpoint."
    ),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _payment_value(features: CustomerFeatures) -> float:
    failures = min(1.0, features.payment_failures_90d / 3.0)
    late = min(1.0, features.late_payments_90d / 4.0)
    return _clamp(0.7 * failures + 0.3 * late, 0.0, 1.0)


def _price_value(features: CustomerFeatures) -> float:
    baseline = max(features.mrr * 3.0, 1.0)
    overage = min(1.0, features.overage_charges_90d / baseline)
    discount = 0.4 if features.discount_active else 0.0
    return _clamp(0.6 * overage + discount, 0.0, 1.0)


def _usage_value(features: CustomerFeatures) -> float:
    return _clamp(-features.usage_change_pct / 50.0, 0.0, 1.0)


def _support_value(features: CustomerFeatures) -> float:
    contacts = min(1.0, features.support_contacts_30d / 8.0)
    complaints = min(1.0, features.complaint_count / 4.0)
    effort = (features.effort_score - 1) / 4.0
    return _clamp(0.4 * contacts + 0.35 * complaints + 0.25 * effort, 0.0, 1.0)


def _sentiment_value(features: CustomerFeatures) -> float:
    return _clamp((0.25 - features.sentiment_score) / 1.25, 0.0, 1.0)


def _churn_language_value(features: CustomerFeatures) -> float:
    value = 0.7 if features.cancellation_intent else 0.0
    value += 0.3 if features.competitor_mentioned else 0.0
    return _clamp(value, 0.0, 1.0)


def _tenure_value(features: CustomerFeatures) -> float:
    return _clamp(1.0 - features.tenure_months / 24.0, 0.0, 1.0)


VALUE_FUNCTIONS = {
    "Payment reliability": _payment_value,
    "Price sensitivity": _price_value,
    "Usage decline": _usage_value,
    "Support friction": _support_value,
    "Sentiment": _sentiment_value,
    "Churn language": _churn_language_value,
    "Tenure stability": _tenure_value,
}


def band_for(score: float) -> RiskBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return "low"


def assess_risk(features: CustomerFeatures) -> RiskScore:
    """Compute the weighted churn score and its factor breakdown."""
    factors: list[RiskFactor] = []
    for name, weight in FACTOR_WEIGHTS.items():
        value = round(VALUE_FUNCTIONS[name](features), 3)
        factors.append(
            RiskFactor(
                name=name,
                weight=weight,
                value=value,
                contribution=round(weight * value, 2),
            )
        )

    score = round(min(100.0, sum(factor.contribution for factor in factors)), 1)
    ranked = sorted(factors, key=lambda factor: factor.contribution, reverse=True)
    top_drivers = [factor.name for factor in ranked if factor.contribution >= 3.0][:3]
    if not top_drivers:
        top_drivers = [factor.name for factor in ranked[:2]]

    return RiskScore(
        score=score,
        band=band_for(score),
        factors=factors,
        top_drivers=top_drivers,
    )


def narrative_for(risk: RiskScore, features: CustomerFeatures) -> tuple[str, str]:
    """Fallback rationale and save play used when the LLM is unavailable."""
    ranked = sorted(risk.factors, key=lambda factor: factor.contribution, reverse=True)[:3]
    detail = ", ".join(
        f"{factor.name.lower()} ({factor.contribution:.1f} points)" for factor in ranked
    )
    rationale = (
        f"Risk score {risk.score:.1f}/100 places this customer in the {risk.band} band. "
        f"The strongest contributors are {detail}."
    )
    if features.cancellation_intent:
        rationale += " The customer explicitly signalled intent to cancel or switch."
    save_play = SAVE_PLAYS[risk.band]
    return rationale, save_play


def with_narrative(
    risk: RiskScore, features: CustomerFeatures, rationale: str, save_play: str
) -> ChurnAssessment:
    return ChurnAssessment(
        score=risk.score,
        band=risk.band,
        factors=risk.factors,
        top_drivers=risk.top_drivers,
        rationale=rationale,
        save_play=save_play,
    )

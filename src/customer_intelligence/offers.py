"""Promotional offer catalogue, eligibility policy and selection.

Margins and eligibility are enforced deterministically. The LLM may only pick a
code from the eligible shortlist produced here; if it returns anything else the
structured-output validator rejects the response and retries.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import (
    CustomerFeatures,
    OfferChoice,
    OfferDefinition,
    OfferRecommendation,
    RiskBand,
    RiskScore,
)


def _bands(*items: RiskBand) -> list[RiskBand]:
    return list(items)


CATALOG: tuple[OfferDefinition, ...] = (
    OfferDefinition(
        code="SAVE20",
        name="Retention credit",
        offer_type="retention",
        discount_pct=20.0,
        duration_months=3,
        description="20% off the next three bills for customers with payment friction.",
        tags=["price_sensitivity", "payment_reliability", "churn_language"],
        eligible_bands=_bands("high", "critical"),
        base_lift_pct=18.0,
    ),
    OfferDefinition(
        code="BILL_CREDIT",
        name="Billing correction credit",
        offer_type="retention",
        discount_pct=15.0,
        duration_months=1,
        description="A 15% credit while the disputed charge is reviewed by the billing team.",
        tags=["payment_reliability", "price_sensitivity"],
        eligible_bands=_bands("medium", "high", "critical"),
        base_lift_pct=14.0,
    ),
    OfferDefinition(
        code="WINBACK_CALL",
        name="Priority save call",
        offer_type="retention",
        discount_pct=0.0,
        duration_months=0,
        description="A same-day call from the retention desk before any discount is applied.",
        tags=["churn_language", "sentiment", "payment_reliability"],
        eligible_bands=_bands("critical"),
        base_lift_pct=22.0,
    ),
    OfferDefinition(
        code="PLAN_DOWNGRADE",
        name="Right-sized plan",
        offer_type="retention",
        discount_pct=0.0,
        duration_months=12,
        description="Move to a lower-cost plan that matches current usage.",
        tags=["price_sensitivity", "usage_decline"],
        eligible_bands=_bands("medium", "high"),
        base_lift_pct=12.0,
    ),
    OfferDefinition(
        code="FREE_TECH_CHECK",
        name="Priority technical review",
        offer_type="care",
        discount_pct=0.0,
        duration_months=0,
        description="An engineer-led review of the open fault, with a follow-up call.",
        tags=["support_friction", "sentiment", "unresolved"],
        eligible_bands=_bands("medium", "high", "critical"),
        base_lift_pct=10.0,
    ),
    OfferDefinition(
        code="ANNUAL_PREPAY",
        name="Annual prepay",
        offer_type="cross_sell",
        discount_pct=12.0,
        duration_months=12,
        description="12% off an annual prepaid contract for established customers.",
        tags=["price_sensitivity", "tenure_stability"],
        eligible_bands=_bands("medium", "high"),
        base_lift_pct=11.0,
    ),
    OfferDefinition(
        code="DATA_BOOST",
        name="Data boost add-on",
        offer_type="cross_sell",
        discount_pct=15.0,
        duration_months=3,
        description="A discounted data add-on for customers whose usage is growing.",
        tags=["engagement", "usage_decline"],
        eligible_bands=_bands("low", "medium"),
        base_lift_pct=8.0,
    ),
    OfferDefinition(
        code="LOYALTY_UPGRADE",
        name="Loyalty upgrade",
        offer_type="upsell",
        discount_pct=10.0,
        duration_months=6,
        description="A 10% upgrade credit for loyal customers ready for a richer plan.",
        tags=["tenure_stability", "engagement"],
        eligible_bands=_bands("low"),
        base_lift_pct=9.0,
    ),
)

CATALOG_BY_CODE: dict[str, OfferDefinition] = {offer.code: offer for offer in CATALOG}


def _has_support_friction(features: CustomerFeatures) -> bool:
    return (
        features.unresolved_issue
        or features.support_contacts_30d >= 3
        or features.effort_score >= 4
    )


ELIGIBILITY_RULES = {
    "SAVE20": lambda features, risk: features.mrr > 0,
    "BILL_CREDIT": lambda features, risk: (
        features.payment_failures_90d >= 1 or features.overage_charges_90d >= 15.0
    ),
    "WINBACK_CALL": lambda features, risk: features.cancellation_intent,
    "PLAN_DOWNGRADE": lambda features, risk: features.usage_change_pct <= -10.0,
    "FREE_TECH_CHECK": lambda features, risk: _has_support_friction(features),
    "ANNUAL_PREPAY": lambda features, risk: (
        features.tenure_months >= 12 and not features.cancellation_intent
    ),
    "DATA_BOOST": lambda features, risk: features.usage_change_pct >= 15.0,
    "LOYALTY_UPGRADE": lambda features, risk: (
        features.tenure_months >= 6 and features.usage_change_pct >= 5.0
    ),
}


def eligible_offers(
    features: CustomerFeatures, risk: RiskScore
) -> list[OfferDefinition]:
    """Return the policy-approved shortlist for one customer."""
    offers: list[OfferDefinition] = []
    for offer in CATALOG:
        if risk.band not in offer.eligible_bands:
            continue
        rule = ELIGIBILITY_RULES.get(offer.code)
        if rule is not None and not rule(features, risk):
            continue
        offers.append(offer)
    return offers


def _driver_tags(drivers: Sequence[str]) -> set[str]:
    return {driver.lower().replace(" ", "_") for driver in drivers}


def choose_offer(
    offers: Sequence[OfferDefinition],
    drivers: Sequence[str],
    features: CustomerFeatures,
) -> OfferChoice:
    """Rule-based choice used by the deterministic provider and as a fallback."""
    if not offers:
        raise ValueError("No eligible offers to choose from")

    wanted = _driver_tags(drivers)
    payment_friction = (
        features.payment_failures_90d >= 1 or features.overage_charges_90d >= 20.0
    )

    def rank(offer: OfferDefinition) -> tuple[int, float]:
        overlap = len(set(offer.tags) & wanted)
        # Billing friction should route to a retention play before a care play.
        retention_boost = (
            1
            if payment_friction and offer.offer_type == "retention"
            else 0
        )
        return overlap + retention_boost, offer.base_lift_pct

    best = max(offers, key=rank)
    matched = [tag for tag in best.tags if tag in wanted]

    if matched:
        rationale = (
            f"{best.name} addresses {', '.join(matched).replace('_', ' ')} "
            f"and fits the {features.mrr:.0f} MRR account."
        )
    else:
        rationale = (
            f"{best.name} is the strongest approved option for this risk profile "
            f"and account value."
        )

    if best.discount_pct > 0:
        message = (
            f"Thank you for {features.tenure_months} months with us. "
            f"Take {best.discount_pct:.0f}% off your next {best.duration_months} bills "
            f"with our {best.name.lower()} - we will apply it to your account."
        )
    else:
        message = f"We would like to help with our {best.name.lower()}: {best.description}"

    return OfferChoice(code=best.code, rationale=rationale[:240], message=message[:320])


def build_recommendation(
    offer: OfferDefinition, choice: OfferChoice, risk: RiskScore
) -> OfferRecommendation:
    expected_lift = round(min(40.0, offer.base_lift_pct + risk.score * 0.1), 1)
    return OfferRecommendation(
        code=offer.code,
        name=offer.name,
        offer_type=offer.offer_type,
        discount_pct=offer.discount_pct,
        duration_months=offer.duration_months,
        rationale=choice.rationale,
        message=choice.message,
        expected_lift_pct=expected_lift,
    )

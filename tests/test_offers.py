"""Offer catalogue, eligibility policy and selection."""

from __future__ import annotations

import pytest
from conftest import make_features

from customer_intelligence.models import OfferChoice, RiskScore
from customer_intelligence.offers import (
    CATALOG,
    CATALOG_BY_CODE,
    ELIGIBILITY_RULES,
    build_recommendation,
    choose_offer,
    eligible_offers,
)


def _risk(band: str, score: float = 50.0) -> RiskScore:
    return RiskScore(
        score=score,
        band=band,  # type: ignore[arg-type]
        factors=[],
        top_drivers=["Payment reliability", "Price sensitivity"],
    )


def test_catalogue_codes_are_unique() -> None:
    codes = [offer.code for offer in CATALOG]
    assert len(codes) == len(set(codes))
    assert set(codes) == set(CATALOG_BY_CODE)
    assert len(CATALOG) == 8


def test_every_eligibility_rule_has_a_catalogue_offer() -> None:
    assert set(ELIGIBILITY_RULES) == set(CATALOG_BY_CODE)


def test_save20_is_reserved_for_high_and_critical() -> None:
    features = make_features()
    medium_codes = {offer.code for offer in eligible_offers(features, _risk("medium"))}
    high_codes = {offer.code for offer in eligible_offers(features, _risk("high"))}
    assert "SAVE20" not in medium_codes
    assert "SAVE20" in high_codes


def test_bill_credit_requires_payment_friction() -> None:
    clean = make_features(payment_failures_90d=0, overage_charges_90d=0.0)
    dirty = make_features(payment_failures_90d=1)
    clean_codes = {offer.code for offer in eligible_offers(clean, _risk("medium"))}
    dirty_codes = {offer.code for offer in eligible_offers(dirty, _risk("medium"))}
    assert "BILL_CREDIT" not in clean_codes
    assert "BILL_CREDIT" in dirty_codes


def test_bill_credit_accepts_large_overage() -> None:
    features = make_features(overage_charges_90d=20.0)
    codes = {offer.code for offer in eligible_offers(features, _risk("medium"))}
    assert "BILL_CREDIT" in codes


def test_free_tech_check_requires_support_friction() -> None:
    calm = make_features(unresolved_issue=False, support_contacts_30d=0, effort_score=1)
    noisy = make_features(unresolved_issue=True)
    calm_codes = {offer.code for offer in eligible_offers(calm, _risk("medium"))}
    noisy_codes = {offer.code for offer in eligible_offers(noisy, _risk("medium"))}
    assert "FREE_TECH_CHECK" not in calm_codes
    assert "FREE_TECH_CHECK" in noisy_codes


def test_winback_call_needs_critical_cancellation() -> None:
    cancelling = make_features(cancellation_intent=True)
    not_cancelling = make_features(cancellation_intent=False)

    critical = {offer.code for offer in eligible_offers(cancelling, _risk("critical"))}
    high = {offer.code for offer in eligible_offers(cancelling, _risk("high"))}
    no_cancel = {offer.code for offer in eligible_offers(not_cancelling, _risk("critical"))}

    assert "WINBACK_CALL" in critical
    assert "WINBACK_CALL" not in high
    assert "WINBACK_CALL" not in no_cancel


def test_low_risk_offers_are_growth_only() -> None:
    features = make_features(usage_change_pct=20.0, tenure_months=24)
    offers = eligible_offers(features, _risk("low", score=10.0))
    assert {offer.code for offer in offers} == {"DATA_BOOST", "LOYALTY_UPGRADE"}
    assert all(offer.offer_type in {"upsell", "cross_sell"} for offer in offers)


def test_data_boost_requires_usage_growth() -> None:
    flat = make_features(usage_change_pct=0.0)
    growing = make_features(usage_change_pct=15.0)
    flat_codes = {offer.code for offer in eligible_offers(flat, _risk("low"))}
    growing_codes = {offer.code for offer in eligible_offers(growing, _risk("low"))}
    assert "DATA_BOOST" not in flat_codes
    assert "DATA_BOOST" in growing_codes


def test_plan_downgrade_requires_usage_decline() -> None:
    flat = make_features(usage_change_pct=0.0)
    declining = make_features(usage_change_pct=-15.0)
    assert "PLAN_DOWNGRADE" not in {
        offer.code for offer in eligible_offers(flat, _risk("medium"))
    }
    assert "PLAN_DOWNGRADE" in {
        offer.code for offer in eligible_offers(declining, _risk("medium"))
    }


def test_choose_offer_returns_eligible_code() -> None:
    features = make_features(payment_failures_90d=1)
    offers = eligible_offers(features, _risk("medium"))
    choice = choose_offer(offers, ["Payment reliability"], features)
    assert choice.code in {offer.code for offer in offers}
    assert len(choice.rationale) <= 240
    assert len(choice.message) <= 320


def test_choose_offer_prefers_retention_on_payment_friction() -> None:
    features = make_features(payment_failures_90d=1, unresolved_issue=True, effort_score=4)
    offers = eligible_offers(features, _risk("medium"))
    choice = choose_offer(offers, ["Payment reliability", "Price sensitivity"], features)
    assert choice.code == "BILL_CREDIT"


def test_choose_offer_rejects_empty_shortlist() -> None:
    with pytest.raises(ValueError):
        choose_offer([], [], make_features())


def test_choose_offer_falls_back_when_no_tag_matches() -> None:
    features = make_features()
    offers = eligible_offers(features, _risk("high"))
    choice = choose_offer(offers, ["Tenure stability"], features)
    assert choice.code in {offer.code for offer in offers}


def test_build_recommendation_carries_catalogue_values() -> None:
    offer = CATALOG_BY_CODE["SAVE20"]
    choice = OfferChoice(code="SAVE20", rationale="Price friction.", message="Take 20% off.")
    recommendation = build_recommendation(offer, choice, _risk("high", score=50.0))

    assert recommendation.code == "SAVE20"
    assert recommendation.discount_pct == 20.0
    assert recommendation.duration_months == 3
    assert recommendation.expected_lift_pct == pytest.approx(23.0)


def test_expected_lift_adds_score_component() -> None:
    offer = CATALOG_BY_CODE["WINBACK_CALL"]
    choice = OfferChoice(code="WINBACK_CALL", rationale="x", message="y")
    recommendation = build_recommendation(offer, choice, _risk("critical", score=100.0))
    assert recommendation.expected_lift_pct == pytest.approx(32.0)
    assert recommendation.expected_lift_pct <= 40.0

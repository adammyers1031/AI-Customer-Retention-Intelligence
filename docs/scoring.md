# Risk scoring and decision policy

## Why a deterministic score

Churn scores drive money, staffing and customer treatment. A score that changes
with model temperature is hard to defend in an audit, hard to regression-test and
impossible to reproduce after an incident. This project therefore computes the
score in Python, exposes every factor contribution, and lets the LLM write only
the narrative around it.

The score is the sum of seven weighted factors:

```text
score = Σ (weight_i × value_i)      weights sum to 100, score is capped at 100
```

`value_i` is normalised to `[0, 1]`. Every contribution is persisted in the
analysis payload, so an agent or auditor can see exactly which behaviour moved the
number.

## Factor definitions

| Factor | Weight | Value function | Intuition |
| --- | ---: | --- | --- |
| Payment reliability | 18 | `0.7 × min(1, failures/3) + 0.3 × min(1, late/4)` | Failed and late payments in 90 days |
| Price sensitivity | 12 | `0.6 × min(1, overage / max(mrr×3, 1)) + 0.4 × discount_active` | Overage pressure plus active discount dependency |
| Usage decline | 12 | `clamp(−usage_change_pct / 50, 0, 1)` | 50% usage drop saturates the factor |
| Support friction | 22 | `0.4 × min(1, contacts/8) + 0.35 × min(1, complaints/4) + 0.25 × (effort−1)/4` | Contact volume, complaints and effort score |
| Sentiment | 22 | `clamp((0.25 − sentiment) / 1.25, 0, 1)` | Neutral (0.25) scores 0; −1.0 scores 1 |
| Churn language | 10 | `0.7 × cancellation_intent + 0.3 × competitor_mentioned` | Explicit cancel/switch language |
| Tenure stability | 4 | `clamp(1 − tenure_months/24, 0, 1)` | Newer accounts carry more risk |

Notes:

- Usage change compares the oldest and newest of the last three billing cycles.
- Payment, late and overage values aggregate the last three cycles (~90 days).
- Support contacts count customer messages in the last 30 days.
- Sentiment, cancellation, competitor, unresolved and effort come from the
  extraction workflow, with a deterministic rule-engine fallback.
- A deliberate design choice: sentiment and support friction carry the largest
  weights because they represent live, actionable dissatisfaction; tenure is a weak
  stabiliser, not a primary driver.

## Bands

| Band | Threshold | Operational meaning |
| --- | --- | --- |
| `critical` | ≥ 60 | Same-day human outreach, save desk |
| `high` | ≥ 45 | Proactive contact within 48 hours |
| `medium` | ≥ 25 | Targeted nurture within five days |
| `low` | < 25 | Growth conversation at natural touchpoints |

Bands are inclusive at the lower bound. `band_for` is tested at every boundary
(60.0, 59.9, 45.0, 44.9, 25.0, 24.9).

## Worked example — `CUST-0004` Daniel Kim

Inputs: 3 payment failures, 4 late payments, $75 overage, active discount, usage
down 34.7%, 3 support contacts, 2 complaints, effort 4, sentiment −0.27,
cancellation intent, competitor mentioned, 18-month tenure.

| Factor | Weight | Value | Contribution |
| --- | ---: | ---: | ---: |
| Payment reliability | 18 | 1.000 | 18.00 |
| Price sensitivity | 12 | 0.460 | 5.52 |
| Usage decline | 12 | 0.694 | 8.33 |
| Support friction | 22 | 0.512 | 11.26 |
| Sentiment | 22 | 0.416 | 9.15 |
| Churn language | 10 | 1.000 | 10.00 |
| Tenure stability | 4 | 0.250 | 1.00 |
| **Total** | **100** | | **63.26 → 63.3** |

Result: `critical`, top drivers `Payment reliability`, `Support friction`,
`Churn language`, offer `WINBACK_CALL`, care `cancellation_request` with a 2-hour
SLA.

## Offer catalogue and eligibility policy

The catalogue is deterministic. The LLM receives only the eligible subset and may
pick one code from it.

| Code | Type | Discount | Duration | Eligible bands | Additional rule | Base lift |
| --- | --- | ---: | ---: | --- | --- | ---: |
| `SAVE20` | retention | 20% | 3 months | high, critical | MRR > 0 | 18.0 |
| `BILL_CREDIT` | retention | 15% | 1 month | medium, high, critical | payment failure ≥1 or overage ≥$15 | 14.0 |
| `WINBACK_CALL` | retention | — | — | critical | cancellation intent | 22.0 |
| `PLAN_DOWNGRADE` | retention | — | 12 months | medium, high | usage change ≤ −10% | 12.0 |
| `FREE_TECH_CHECK` | care | — | — | medium, high, critical | unresolved issue, ≥3 contacts, or effort ≥4 | 10.0 |
| `ANNUAL_PREPAY` | cross-sell | 12% | 12 months | medium, high | tenure ≥12 and no cancellation intent | 11.0 |
| `DATA_BOOST` | cross-sell | 15% | 3 months | low, medium | usage change ≥ +15% | 8.0 |
| `LOYALTY_UPGRADE` | upsell | 10% | 6 months | low | tenure ≥6 and usage change ≥ +5% | 9.0 |

### Selection

`choose_offer` (the deterministic chooser and fallback) ranks eligible offers by:

1. number of offer tags overlapping the top churn drivers (e.g. `Support friction`
   → `support_friction`);
2. a +1 retention boost when payment friction exists and the offer is a retention
   type, so a billing problem routes to a save play before a care play;
3. `base_lift_pct` as the tie-breaker.

The live model follows the same policy in natural language. An out-of-shortlist
code is rejected by the validator, triggering one repair retry and then the
deterministic choice.

### Expected lift

```text
expected_lift_pct = min(40.0, base_lift_pct + 0.1 × churn_score)
```

This is a transparent prioritisation heuristic, not a causal uplift estimate. In
production it would be replaced by a measured incrementality model.

## Care triage policy

Category priority (first match wins):

| Order | Category | Trigger |
| ---: | --- | --- |
| 1 | `cancellation_request` | explicit cancel/leave/switch language |
| 2 | `billing_dispute` | billing keyword **and** a dispute/complaint signal |
| 3 | `technical_issue` | outage/speed/connectivity/device terms |
| 4 | `plan_change` | upgrade/downgrade/add-on/package terms |
| 5 | `general_question` | everything else |

Whole-word matching matters: earlier naive substring matching classified
"downgrade" as a technical issue because it contains "down". The classifier now
uses word boundaries (and the regression test is part of the suite).

Urgency:

| Condition | Urgency | SLA |
| --- | --- | ---: |
| Cancellation request | urgent | 2h |
| Critical band, or unresolved with score ≥60 | urgent | 2h |
| High band, escalation, or complaint | high | 8h |
| Medium band, or score ≥30 | normal | 24h |
| Otherwise | low | 48h |

`escalate` is true for all urgent cases and for cancellation requests.
`next_best_action` is a concrete human-owned step, capped at 200 characters.

## Limitations

- Weights, thresholds and lift values are expert heuristics for the portfolio,
  not statistically calibrated parameters.
- The score assumes the last three billing cycles represent current behaviour.
- The extraction signals inherit any bias in the prompt or model; the fallback
  rule engine is intentionally conservative.
- Production work would include labelled-outcome calibration, fairness analysis
  across regions/plans, drift monitoring and backtesting before automated action.

# API reference

Base URL: `http://127.0.0.1:8000`

Interactive documentation is generated at `/docs` (Swagger UI) and `/redoc`.
The OpenAPI JSON is available at `/openapi.json`.

## Conventions

- JSON in, JSON out; UTF-8.
- Customer IDs match `^[A-Za-z0-9._-]+$`, 3–64 characters.
- Billing periods are `YYYY-MM`.
- Unknown customers return `404`; invalid bodies and query parameters return
  `422` with FastAPI's validation detail.
- Writes are idempotent: customer upserts and billing-period upserts are safe to
  retry.
- A fresh database is seeded automatically when `CI_AUTO_SEED=1` (default).

## System

### `GET /health`

```json
{
  "status": "ok",
  "service": "customer-intelligence",
  "version": "0.1.0",
  "llm_provider": "deterministic"
}
```

### `GET /`

```json
{
  "service": "customer-intelligence",
  "docs": "/docs",
  "health": "/health"
}
```

### `GET /api/v1/prompts`

```json
{
  "version": "2026.11.1",
  "workflows": [
    "chat_extraction",
    "churn_narrative",
    "care_triage",
    "offer_recommendation"
  ]
}
```

## Customers

### `POST /api/v1/customers` → `201`

Creates or updates a customer.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/customers \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-1001",
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "plan": "business",
    "mrr": 249.0,
    "tenure_months": 18,
    "region": "eu-west",
    "marketing_opt_in": true
  }'
```

```json
{"customer_id": "CUST-1001", "status": "created"}
```

Required fields: `customer_id`, `name`, `plan`, `mrr`, `tenure_months`.
`plan` is one of `starter | standard | premium | business`. Extra fields are
rejected with `422`.

### `GET /api/v1/customers` → `200`

Returns analysed customers, highest risk first.

| Query | Type | Default | Notes |
| --- | --- | --- | --- |
| `risk_band` | enum | none | `low`, `medium`, `high`, `critical` |
| `limit` | int | 50 | 1–500 |

```json
[
  {
    "customer_id": "CUST-0004",
    "name": "Daniel Kim",
    "plan": "business",
    "mrr": 249.0,
    "score": 63.3,
    "band": "critical",
    "offer_code": "WINBACK_CALL",
    "offer_type": "retention",
    "care_category": "cancellation_request",
    "care_urgency": "urgent",
    "analyzed_at": "2026-11-09T12:00:00Z"
  }
]
```

Only customers with a stored analysis appear here. Customers created but not yet
analysed are returned by `GET /customers/{id}`.

### `GET /api/v1/customers/{customer_id}` → `200`

```json
{
  "customer_id": "CUST-0001",
  "name": "Olivia Chen",
  "email": "olivia.chen@example.com",
  "plan": "premium",
  "mrr": 89.0,
  "tenure_months": 42,
  "region": "us-west",
  "marketing_opt_in": true,
  "created_at": "2026-11-09T12:00:00Z"
}
```

## Ingestion

### `POST /api/v1/customers/{customer_id}/chats` → `201`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/customers/CUST-1001/chats \
  -H "Content-Type: application/json" \
  -d '{"role": "customer", "message": "We are considering leaving for NovaNet."}'
```

```json
{"customer_id": "CUST-1001", "messages_stored": 1, "total_messages": 1}
```

Fields: `role` (`customer | agent`), `message` (1–4000 chars), optional
`channel` (`chat | email | phone_transcript`), optional `ts` (defaults to now).

### `GET /api/v1/customers/{customer_id}/chats` → `200`

```json
[
  {
    "role": "customer",
    "message": "We are considering leaving for NovaNet.",
    "channel": "chat",
    "ts": "2026-11-09T12:00:00Z"
  }
]
```

Ordered oldest first.

### `POST /api/v1/customers/{customer_id}/billing` → `201`

Upserts one monthly cycle.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/customers/CUST-1001/billing \
  -H "Content-Type: application/json" \
  -d '{
    "period": "2026-11",
    "invoiced_amount": 249.0,
    "paid_amount": 0.0,
    "payment_failures": 2,
    "late_payments": 1,
    "overage_charges": 45.0,
    "discount_amount": 0.0,
    "usage_gb": 640.0
  }'
```

```json
{"customer_id": "CUST-1001", "periods_stored": 1, "total_periods": 1}
```

All amount fields are non-negative. `period` must match `^\d{4}-\d{2}$`.

### `GET /api/v1/customers/{customer_id}/billing` → `200`

Returns the stored cycles ordered by `period`.

## Analysis

### `POST /api/v1/customers/{customer_id}/analyze` → `200`

Runs the complete pipeline and persists the result.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/customers/CUST-0004/analyze
```

Response (abridged):

```json
{
  "customer": {
    "customer_id": "CUST-0004",
    "name": "Daniel Kim",
    "plan": "business",
    "mrr": 249.0,
    "tenure_months": 18
  },
  "features": {
    "payment_failures_90d": 3,
    "late_payments_90d": 4,
    "overage_charges_90d": 75.0,
    "usage_change_pct": -34.69,
    "support_contacts_30d": 3,
    "sentiment_score": -0.27,
    "cancellation_intent": true,
    "competitor_mentioned": "NovaNet",
    "unresolved_issue": true,
    "effort_score": 4
  },
  "chat_signals": {
    "sentiment": -0.27,
    "intents": [
      "billing_question",
      "technical_issue",
      "cancellation",
      "plan_change",
      "complaint"
    ],
    "cancellation_intent": true,
    "competitor_mentioned": "NovaNet",
    "unresolved_issue": true,
    "effort_score": 4,
    "summary": "Customer signals intent to cancel or switch provider. The issue remains unresolved. Mentions NovaNet."
  },
  "churn": {
    "score": 63.3,
    "band": "critical",
    "factors": [
      {"name": "Payment reliability", "weight": 18.0, "value": 1.0, "contribution": 18.0}
    ],
    "top_drivers": ["Payment reliability", "Support friction", "Churn language"],
    "rationale": "Risk score 63.3/100 places this customer in the critical band...",
    "save_play": "Same-day human outreach from the retention desk..."
  },
  "offer": {
    "code": "WINBACK_CALL",
    "name": "Priority save call",
    "offer_type": "retention",
    "discount_pct": 0.0,
    "duration_months": 0,
    "rationale": "Priority save call addresses churn language, payment reliability...",
    "message": "We would like to help with our priority save call...",
    "expected_lift_pct": 28.3
  },
  "care": {
    "category": "cancellation_request",
    "urgency": "urgent",
    "next_best_action": "Route to the retention desk and call within SLA with an approved save offer.",
    "sla_hours": 2,
    "escalate": true
  },
  "analyzed_at": "2026-11-09T12:00:00Z",
  "prompt_version": "2026.11.1"
}
```

`offer` may be `null` when no catalogue offer is eligible for the customer's band
and usage profile (for example several low-risk accounts).

### `GET /api/v1/customers/{customer_id}/profile` → `200`

Returns the last stored `CustomerProfile`. `404` when the customer has never been
analysed.

### `POST /api/v1/analyze` → `200`

Runs the pipeline for every stored customer and returns the profiles. Intended for
demo/backfill; production would enqueue this work.

## Metrics

### `GET /api/v1/metrics/summary` → `200`

```json
{
  "total_customers": 12,
  "analyzed_customers": 12,
  "avg_risk_score": 29.0,
  "high_risk_customers": 3,
  "at_risk_mrr": 492.0,
  "band_counts": {"critical": 1, "high": 2, "medium": 2, "low": 7},
  "offers_by_type": {"retention": 4, "care": 1, "upsell": 3},
  "care_categories": {
    "cancellation_request": 3,
    "billing_dispute": 2,
    "technical_issue": 3,
    "plan_change": 3,
    "general_question": 1
  }
}
```

`high_risk_customers` counts `high` + `critical`. `at_risk_mrr` sums MRR for those
bands.

## Offers

### `GET /api/v1/offers` → `200`

Returns the full policy catalogue, not the per-customer shortlist.

```json
[
  {
    "code": "SAVE20",
    "name": "Retention credit",
    "offer_type": "retention",
    "discount_pct": 20.0,
    "duration_months": 3,
    "description": "20% off the next three bills for customers with payment friction.",
    "tags": ["price_sensitivity", "payment_reliability", "churn_language"],
    "eligible_bands": ["high", "critical"],
    "base_lift_pct": 18.0
  }
]
```

## Error examples

```json
{"detail": "Unknown customer: CUST-9999"}
```

```json
{"detail": "No analysis stored for customer: CUST-9999"}
```

Validation errors use FastAPI's standard shape:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "mrr"],
      "msg": "Value error, ...",
      "input": -1.0
    }
  ]
}
```

## Status codes

| Code | Meaning |
| --- | --- |
| `200` | Read or analysis succeeded |
| `201` | Resource created or upserted |
| `404` | Unknown customer or missing analysis |
| `422` | Invalid body or query parameter |
| `500` | Unexpected server/storage failure |

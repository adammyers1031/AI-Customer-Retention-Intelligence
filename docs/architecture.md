# Architecture

## Design goals

1. **LLMs where they add value.** Language tasks — extracting intents from messy
   transcripts, writing an explanation, choosing among approved offers, routing a
   care case — are delegated to prompts.
2. **Determinism where it matters.** Numeric risk, eligibility, SLA and discounts
   are computed by auditable Python rules. A model cannot silently change a score,
   invent a discount or bypass margin policy.
3. **Hermetic by default.** The full pipeline, API and test suite run without a
   network or API key through the deterministic provider.
4. **Boring production plumbing.** Typed Pydantic contracts, parameterised SQL,
   per-request SQLite connections, non-root container, CI matrix and Docker
   healthcheck.

## Component map

```text
             ┌──────────────────────────────────────────────┐
 ingestion   │ POST /customers/{id}/chats                   │
             │ POST /customers/{id}/billing                 │
             │ POST /customers                             │
             └──────────────────────┬───────────────────────┘
                                    │ validated by models.py
                                    ▼
             ┌──────────────────────────────────────────────┐
 storage     │ repository.py · SQLite · schema.sql          │
             │ customers · chats · billing · analyses       │
             └──────────────────────┬───────────────────────┘
                                    │
 analysis                           ▼
             ┌──────────────────────────────────────────────┐
             │ services.analyze_customer                    │
             │                                              │
             │  1. llm.workflows.extract_chat_signals       │
             │       prompt → JSON → ChatSignals            │
             │  2. features.build_features                  │
             │       billing aggregation + text statistics  │
             │  3. scoring.assess_risk                      │
             │       7 weighted factors → RiskScore         │
             │  4. llm.workflows.write_churn_narrative      │
             │       explanation only, score immutable      │
             │  5. offers.eligible_offers + recommend_offer │
             │       policy shortlist → validated code      │
             │  6. llm.workflows.triage_care                │
             │       category · urgency · SLA · action      │
             └──────────────────────┬───────────────────────┘
                                    ▼
             ┌──────────────────────────────────────────────┐
             │ analyses.payload (JSON)                      │
             │ CustomerProfile                              │
             └──────────────────────┬───────────────────────┘
                                    ▼
 delivery    FastAPI · OpenAPI · /customers · /profile · /metrics
```

## Module responsibilities

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `models.py` | Validate every boundary and persisted payload | Pydantic |
| `features.py` | Text statistics, billing aggregation, feature vector | models |
| `scoring.py` | Weighted factor contributions, bands, fallback narrative | models |
| `offers.py` | Catalogue, eligibility rules, selection, lift estimate | models |
| `care.py` | Category/urgency policy, SLA, next best action | features, models |
| `llm/prompts.py` | Load and version Markdown templates | package resources |
| `llm/client.py` | JSON parsing, structured retry, providers | httpx |
| `llm/workflows.py` | Typed prompt variables and validators | models, client |
| `llm/deterministic.py` | Offline provider using the same contract | features, offers, care, scoring |
| `repository.py` | Schema, CRUD, ranking, aggregates | models, sqlite3 |
| `services.py` | Orchestration + graceful LLM fallbacks | all of the above |
| `api.py` | HTTP surface and dependency injection | services, repository |
| `seed.py` | Deterministic demo cohort | models |
| `cli.py` | Operator workflows | services, repository |

## Request lifecycle

```text
Client
  │  POST /api/v1/customers/CUST-0004/analyze
  ▼
FastAPI dependency get_conn ──► new SQLite connection (WAL, foreign keys)
  ▼
services.analyze_customer
  ├─ repository.get_customer      ── 404 if missing
  ├─ repository.list_chats
  ├─ repository.list_billing
  ├─ StructuredLLM.run(chat_extraction)
  ├─ build_features
  ├─ assess_risk
  ├─ StructuredLLM.run(churn_narrative)
  ├─ eligible_offers + StructuredLLM.run(offer_recommendation)
  ├─ StructuredLLM.run(care_triage)
  ├─ repository.save_analysis
  └─ CustomerProfile ──► JSON response
  ▼
connection.close()
```

Each request owns its connection. WAL mode and a busy timeout keep concurrent
reads safe while analyses write. `check_same_thread=False` is safe because the
connection never leaves its request thread.

## Structured-output contract

Every workflow follows the same path:

```text
prompt.render(variables)
        │
        ▼
provider.complete(system, user, temperature)
        │
        ▼
parse_json_response  ── plain JSON, fenced JSON or chatty preamble
        │
        ▼
Model.model_validate(payload)
        │
        ├─ success ──► optional validator (e.g. offer code eligibility)
        │
        └─ failure ──► append validation error, retry once
```

If `StructuredLLM` still fails, `services.py` falls back to the deterministic
rule for that step. The pipeline therefore degrades gracefully: a provider outage
produces a lower-fidelity narrative or choice, never a missing analysis.

## Failure handling matrix

| Failure | Behaviour |
| --- | --- |
| Malformed JSON | Repair prompt, retry once, then fallback rule |
| Schema violation | Repair prompt with Pydantic error, retry once |
| Offer code outside shortlist | Validator rejects, repair prompt, retry once |
| Provider HTTP error / timeout | `LLMTransportError` → fallback rule |
| Unknown customer | `CustomerNotFound` → HTTP 404 |
| Invalid request body | Pydantic → HTTP 422 |
| Unknown risk band filter | Literal validation → HTTP 422 |
| Duplicate customer/period | Idempotent upsert |
| Foreign-key violation | `sqlite3.IntegrityError`, surfaced as 500 |
| Empty database | Optional auto-seed (`CI_AUTO_SEED=1`) |

## Storage schema

```sql
customers(customer_id PK, name, email, plan, mrr, tenure_months, region,
          marketing_opt_in, created_at)

chats(id PK, customer_id FK → customers, role, message, channel, ts)
  index (customer_id, ts)

billing(id PK, customer_id FK → customers, period,
        invoiced_amount, paid_amount, payment_failures, late_payments,
        overage_charges, discount_amount, usage_gb)
  unique (customer_id, period)

analyses(customer_id PK FK → customers, analyzed_at, score, band,
         offer_code, offer_type, care_category, care_urgency, payload)
  index score DESC, index band
```

The SQL file is shipped as package data (`customer_intelligence/sql/schema.sql`)
so the container needs no source checkout at runtime. `analyses.payload` stores
the canonical `CustomerProfile` JSON; the scalar columns exist purely for fast
ranking and aggregation.

## Deterministic provider

`DeterministicLLM` is a test double with the same interface as a live model. It:

1. reads the workflow marker from the rendered prompt;
2. parses the embedded JSON blocks;
3. delegates to the same feature, scoring, offer and care rules;
4. returns JSON text.

This is what makes `pytest` hermetic and the demo reproducible. It is explicitly
not a model and does not claim to be one; it exists so the prompts, contracts,
retry logic and API can be exercised in CI without secrets.

## Extension points

- **New workflow:** add a prompt Markdown file, register it in
  `llm/prompts.py`, add a typed helper in `llm/workflows.py`, and call it from
  `services.py`.
- **New offer:** append an `OfferDefinition` and an eligibility rule. The API and
  validation pick it up automatically.
- **New risk factor:** add a weight, value function and register it in
  `VALUE_FUNCTIONS`; the contribution reconciliation is tested.
- **New provider:** implement `LLMClient.complete` and add a branch in
  `build_llm_client`. Nothing else changes.
- **New storage:** the repository module is the only place that knows SQL.

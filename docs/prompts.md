# Prompt engineering

## Why prompts are files

Prompts are production artefacts, not string literals buried in code. Each
workflow lives in `src/customer_intelligence/prompts/<workflow>.md`, is shipped as
package data, and is loaded once at import time by `llm/prompts.py`. This gives:

- reviewable diffs and history for prompt changes;
- a single version constant (`PROMPT_VERSION`) stamped on every analysis;
- the same file used by the deterministic and live providers;
- no prompt drift between environments.

## Template structure

Every file has two sections:

````markdown
## SYSTEM
Role, constraints, tone and output rules.

## USER
Workflow: <workflow_name>
Customer: {{customer_id}}

```json
{{input_json}}
```

Task instructions, closed sets and an example output.
````

The loader splits on `## SYSTEM` and `## USER`, trims whitespace, and binds the
per-workflow temperature:

| Workflow | Temperature | Rationale |
| --- | --- | --- |
| `chat_extraction` | 0.0 | Extraction must be repeatable |
| `care_triage` | 0.0 | Queue routing must be repeatable |
| `churn_narrative` | 0.2 | Slight variation in prose is acceptable |
| `offer_recommendation` | 0.2 | Decision is validated; prose may vary |

Templates are frozen dataclasses. Rendering replaces `{{key}}` placeholders and
raises `PromptRenderError` if any `{{` remains — a missing variable fails loudly
instead of shipping a half-rendered prompt to a paid model.

## Workflow 1 — `chat_extraction`

**Inputs:** `customer_id`, `input_json` (JSON array of conversation messages).

**Output contract:** `ChatSignals`

| Field | Type | Rules |
| --- | --- | --- |
| `sentiment` | float | −1.0 (furious) to 1.0 (delighted) |
| `intents` | list[str] | 1–5 labels from a closed set |
| `cancellation_intent` | bool | Only explicit/strong implication |
| `competitor_mentioned` | str \| null | Never guessed |
| `unresolved_issue` | bool | Still open, repeated, no response |
| `effort_score` | int | 1 (easy) to 5 (repeated escalation) |
| `summary` | str | One factual sentence, ≤160 chars |

Closed intent set: `billing_question`, `technical_issue`, `cancellation`,
`plan_change`, `sales_interest`, `complaint`, `general_question`.

The prompt tells the model to weight recent customer messages, treat agent messages
as context only, and never invent competitor names. The Pydantic model enforces
the numeric bounds and list shape; the closed set is a prompt rule because a
future taxonomy may grow.

## Workflow 2 — `care_triage`

**Inputs:** `customer_id`, `input_json`, `risk_json` (deterministic `RiskScore`).

**Output contract:** `CareAssessment`

```json
{
  "category": "technical_issue",
  "urgency": "high",
  "next_best_action": "Assign a senior network engineer and call the customer within 8 hours with an outage ETA.",
  "sla_hours": 8,
  "escalate": false
}
```

Category priority is explicit: cancellation > billing dispute > technical > plan
change > general. Urgency maps to SLA in the prompt and in `care.py`
(`urgent 2 / high 8 / normal 24 / low 48`). The action must be owned by a human
agent, not the customer.

## Workflow 3 — `churn_narrative`

**Inputs:** `customer_id`, `assessment_json`, `features_json`.

**Output contract:** `ChurnNarrative` (`rationale`, `save_play`).

This prompt is intentionally constrained: the score and factor contributions are
computed before the call and the model is told not to recalculate or contradict
them. It may only cite supplied numbers. `save_play` must match the risk band
(critical = same day, high = 48 hours, medium = nurture, low = growth).

If the model fails, `scoring.narrative_for` supplies a template-based rationale
built from the same factor list.

## Workflow 4 — `offer_recommendation`

**Inputs:** `customer_id`, `context_json` (churn + features), `offers_json`
(the deterministic eligible shortlist).

**Output contract:** `OfferChoice` (`code`, `rationale`, `message`), then enriched
into `OfferRecommendation` with catalogue values.

Decision policy in the prompt:

1. Match offer tags to the top churn drivers.
2. Prefer a human-led save play for critical risk.
3. Prefer growth offers for low risk only when usage/tenure support them.
4. Never return a code outside the shortlist.

Rule 4 is enforced in code, not trusted to the prompt:

```python
allowed = {offer.code for offer in offers}

def validate_choice(choice: OfferChoice) -> None:
    if choice.code not in allowed:
        raise ValueError(
            f"Offer code {choice.code!r} is not in the eligible shortlist {sorted(allowed)}"
        )
```

An invalid code triggers the structured retry, then a deterministic fallback
choice from the same shortlist. The model cannot invent a discount, duration or
offer that the catalogue did not authorise.

## Structured output and repair

`StructuredLLM.run` wraps every workflow:

```text
render prompt
  └─ provider.complete(...)
       └─ parse_json_response(raw)          # plain, fenced or chatty JSON
            └─ Model.model_validate(payload)
                 ├─ ok → optional validator → return
                 └─ error → append:
                      "Your previous response was invalid: <error>.
                       Return ONLY the JSON object, with no markdown or commentary."
                    → retry once
```

After the final attempt it raises `LLMOutputError`. `services.py` catches
`LLMError` per step, so one bad workflow does not lose the whole analysis:

| Step | Fallback |
| --- | --- |
| Extraction | `features.extract_signals` rule engine |
| Narrative | `scoring.narrative_for` template |
| Offer | `offers.choose_offer` rule engine |
| Care | `care.derive_care` rule engine |

## Versioning

`PROMPT_VERSION` lives in `llm/prompts.py` and is persisted on every
`CustomerProfile`. Bump it whenever prompt text, output schema or decision policy
changes. That makes it possible to compare outcomes across prompt releases and to
explain historical decisions during an audit.

Suggested workflow:

1. Edit the Markdown file.
2. Bump `PROMPT_VERSION`.
3. Add or update contract tests in `tests/test_prompts.py`.
4. Run `pytest` and an evaluation cohort (`customer-intel seed --reset`).
5. Compare band/offer/metric deltas before merging.

## Using a live model

```bash
export CI_LLM_PROVIDER=openai
export CI_LLM_API_KEY=sk-...
export CI_LLM_MODEL=gpt-4o-mini
export CI_LLM_BASE_URL=https://api.openai.com/v1
customer-intel serve
```

`OpenAICompatibleClient` posts to `/chat/completions` with a 30-second timeout and
maps transport/HTTP failures to `LLMTransportError`. Any OpenAI-compatible
gateway (Azure OpenAI proxy, local vLLM, Ollama's OpenAI endpoint) works by
changing `CI_LLM_BASE_URL` and `CI_LLM_MODEL`.

## Governance checklist

- The numeric score is never produced by the model.
- Offer codes are validated against a deterministic shortlist.
- Discounts and durations come from the catalogue, not generated text.
- Every workflow has a deterministic fallback.
- Every analysis records `prompt_version` and `analyzed_at`.
- Prompts use synthetic/demo data; no secrets or real PII belong in them.
- Output length limits are stated in the prompt and enforced by model fields.

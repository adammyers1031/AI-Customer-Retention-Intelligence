"""LLM plumbing: JSON parsing, structured retries, providers and workflows."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import make_features

from customer_intelligence.care import derive_care
from customer_intelligence.llm import client as client_module
from customer_intelligence.llm.client import (
    LLMOutputError,
    LLMTransportError,
    OpenAICompatibleClient,
    StructuredLLM,
    build_llm_client,
    parse_json_response,
)
from customer_intelligence.llm.deterministic import DeterministicLLM
from customer_intelligence.models import (
    CareAssessment,
    ChatMessageIn,
    ChatSignals,
    OfferChoice,
    RiskScore,
)
from customer_intelligence.offers import CATALOG_BY_CODE

SIGNALS_PAYLOAD = {
    "sentiment": -0.4,
    "intents": ["technical_issue"],
    "cancellation_intent": False,
    "competitor_mentioned": None,
    "unresolved_issue": True,
    "effort_score": 3,
    "summary": "Customer reports an unresolved outage.",
}


class ScriptedClient:
    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "user": user, "temperature": str(temperature)})
        return self.responses.pop(0)


def _signals_json() -> str:
    return json.dumps(SIGNALS_PAYLOAD)


def test_parse_plain_json() -> None:
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_fenced_json() -> None:
    raw = 'Here is the result:\n```json\n{"a": 2}\n```\nDone.'
    assert parse_json_response(raw) == {"a": 2}


def test_parse_chatty_json() -> None:
    raw = 'Sure! {"a": 3, "b": [1, 2]} Let me know if you need more.'
    assert parse_json_response(raw) == {"a": 3, "b": [1, 2]}


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(LLMOutputError):
        parse_json_response("definitely not json")


def test_structured_run_accepts_first_response() -> None:
    client = ScriptedClient([_signals_json()])
    runner = StructuredLLM(client)
    result = runner.run(
        "chat_extraction",
        {"customer_id": "CUST-1", "input_json": "[]"},
        ChatSignals,
    )
    assert isinstance(result, ChatSignals)
    assert len(client.calls) == 1


def test_structured_run_repairs_invalid_output() -> None:
    client = ScriptedClient(["not json", _signals_json()])
    runner = StructuredLLM(client)
    result = runner.run(
        "chat_extraction",
        {"customer_id": "CUST-1", "input_json": "[]"},
        ChatSignals,
    )
    assert result.unresolved_issue is True
    assert len(client.calls) == 2
    assert "previous response was invalid" in client.calls[1]["user"]


def test_structured_run_retries_failed_validation() -> None:
    bad = json.dumps({"code": "NOT_ELIGIBLE", "rationale": "x", "message": "y"})
    good = json.dumps({"code": "SAVE20", "rationale": "x", "message": "y"})
    client = ScriptedClient([bad, good])
    runner = StructuredLLM(client)

    def validate(choice: OfferChoice) -> None:
        if choice.code != "SAVE20":
            raise ValueError("code not eligible")

    result = runner.run(
        "offer_recommendation",
        {"customer_id": "CUST-1", "context_json": "{}", "offers_json": "[]"},
        OfferChoice,
        validate,
    )
    assert result.code == "SAVE20"
    assert len(client.calls) == 2


def test_structured_run_exhausts_attempts() -> None:
    client = ScriptedClient(["nope", "still nope", "nope again"])
    runner = StructuredLLM(client, max_attempts=3)
    with pytest.raises(LLMOutputError):
        runner.run(
            "chat_extraction",
            {"customer_id": "CUST-1", "input_json": "[]"},
            ChatSignals,
        )
    assert len(client.calls) == 3


def test_structured_run_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError):
        StructuredLLM(ScriptedClient([]), max_attempts=0)


def test_openai_client_requires_key() -> None:
    with pytest.raises(LLMTransportError):
        OpenAICompatibleClient(base_url="https://example.test", api_key="", model="m")


def _fake_response(status_code: int, payload: object) -> object:
    class FakeResponse:
        text = "response body"

        def json(self) -> object:
            return payload

    FakeResponse.status_code = status_code
    return FakeResponse()


def test_openai_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return _fake_response(
            200, {"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    client = OpenAICompatibleClient(
        base_url="https://example.test/v1/", api_key="secret", model="test-model"
    )
    assert client.complete(system="sys", user="hi", temperature=0.2) == '{"ok": true}'
    assert captured["url"] == "https://example.test/v1/chat/completions"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "test-model"
    assert payload["temperature"] == 0.2


def test_openai_client_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *args, **kwargs: _fake_response(500, {}),
    )
    client = OpenAICompatibleClient(
        base_url="https://example.test/v1", api_key="secret", model="m"
    )
    with pytest.raises(LLMTransportError):
        client.complete(system="sys", user="hi")


def test_openai_client_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(client_module.httpx, "post", boom)
    client = OpenAICompatibleClient(
        base_url="https://example.test/v1", api_key="secret", model="m"
    )
    with pytest.raises(LLMTransportError):
        client.complete(system="sys", user="hi")


def test_openai_client_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module.httpx,
        "post",
        lambda *args, **kwargs: _fake_response(200, {"unexpected": True}),
    )
    client = OpenAICompatibleClient(
        base_url="https://example.test/v1", api_key="secret", model="m"
    )
    with pytest.raises(LLMTransportError):
        client.complete(system="sys", user="hi")


def test_build_client_defaults_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "LLM_PROVIDER", "deterministic")
    assert isinstance(build_llm_client(), DeterministicLLM)


def test_build_client_openai_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(client_module, "LLM_API_KEY", "secret")
    monkeypatch.setattr(client_module, "LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(client_module, "LLM_MODEL", "test-model")
    client = build_llm_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.name == "openai-compatible"


def test_build_client_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "LLM_PROVIDER", "telepathy")
    with pytest.raises(LLMTransportError):
        build_llm_client()


def _block(payload: object) -> str:
    return f"```json\n{json.dumps(payload)}\n```"


def _risk_payload() -> dict[str, object]:
    return {"score": 50.0, "band": "high", "factors": [], "top_drivers": ["Sentiment"]}


def test_deterministic_chat_extraction() -> None:
    messages = [{"role": "customer", "message": "The service is still broken."}]
    user = f"Workflow: chat_extraction\n{_block(messages)}"
    result = json.loads(DeterministicLLM().complete(system="", user=user))
    assert result["unresolved_issue"] is True
    assert ChatSignals.model_validate(result)


def test_deterministic_care_triage() -> None:
    messages = [{"role": "customer", "message": "I want to cancel my contract."}]
    user = f"Workflow: care_triage\n{_block(messages)}\n{_block(_risk_payload())}"
    result = CareAssessment.model_validate(
        json.loads(DeterministicLLM().complete(system="", user=user))
    )
    assert result.category == "cancellation_request"
    assert result.urgency == "urgent"


def test_deterministic_churn_narrative() -> None:
    features = make_features(sentiment_score=-0.7, unresolved_issue=True)
    risk = _risk_payload()
    user = (
        "Workflow: churn_narrative\n"
        + _block(risk)
        + "\n"
        + _block(features.model_dump())
    )
    result = json.loads(DeterministicLLM().complete(system="", user=user))
    assert "50.0/100" in result["rationale"]
    assert result["save_play"]


def test_deterministic_offer_recommendation() -> None:
    features = make_features(payment_failures_90d=1)
    context = {"churn": _risk_payload(), "features": features.model_dump()}
    offers = [
        CATALOG_BY_CODE["SAVE20"].model_dump(),
        CATALOG_BY_CODE["BILL_CREDIT"].model_dump(),
    ]
    user = (
        "Workflow: offer_recommendation\n"
        + _block(context)
        + "\n"
        + _block(offers)
    )
    result = OfferChoice.model_validate(
        json.loads(DeterministicLLM().complete(system="", user=user))
    )
    assert result.code in {"SAVE20", "BILL_CREDIT"}


def test_deterministic_rejects_unknown_workflow() -> None:
    user = "Workflow: crystal_ball\n```json\n[]\n```"
    with pytest.raises(LLMOutputError):
        DeterministicLLM().complete(system="", user=user)


def test_deterministic_requires_workflow_marker() -> None:
    with pytest.raises(LLMOutputError):
        DeterministicLLM().complete(system="", user="no marker here")


def test_deterministic_rejects_bad_embedded_json() -> None:
    user = "Workflow: chat_extraction\n```json\nnot json\n```"
    with pytest.raises(LLMOutputError):
        DeterministicLLM().complete(system="", user=user)


def test_deterministic_care_matches_rule_engine() -> None:
    messages = [
        {"role": "customer", "message": "The router is still down and nobody called back."}
    ]
    risk = RiskScore.model_validate(_risk_payload())
    user = f"Workflow: care_triage\n{_block(messages)}\n{_block(_risk_payload())}"
    result = CareAssessment.model_validate(
        json.loads(DeterministicLLM().complete(system="", user=user))
    )
    expected = derive_care(
        [ChatMessageIn(role="customer", message=messages[0]["message"])],
        risk,
    )
    assert result == expected

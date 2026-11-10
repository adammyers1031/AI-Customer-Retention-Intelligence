"""Prompt registry: versioning, sections and rendering."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from customer_intelligence.llm.prompts import (
    PROMPT_VERSION,
    PROMPTS,
    WORKFLOW_TEMPERATURES,
    PromptRenderError,
    PromptTemplate,
)

EXPECTED_WORKFLOWS = {
    "chat_extraction",
    "care_triage",
    "churn_narrative",
    "offer_recommendation",
}


def test_all_workflows_are_registered() -> None:
    assert set(PROMPTS) == EXPECTED_WORKFLOWS
    assert set(WORKFLOW_TEMPERATURES) == EXPECTED_WORKFLOWS


def test_prompt_version_is_calendar_style() -> None:
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d+", PROMPT_VERSION)


def test_every_prompt_has_sections_and_version() -> None:
    for workflow, template in PROMPTS.items():
        assert template.workflow == workflow
        assert template.version == PROMPT_VERSION
        assert template.system.strip()
        assert template.user.strip()
        assert "## USER" not in template.system


def test_prompts_contain_a_json_contract() -> None:
    for template in PROMPTS.values():
        assert "JSON" in template.system or "JSON" in template.user
        assert "```json" in template.user


def test_extraction_prompts_are_deterministic() -> None:
    assert WORKFLOW_TEMPERATURES["chat_extraction"] == 0.0
    assert WORKFLOW_TEMPERATURES["care_triage"] == 0.0


def test_render_replaces_placeholders() -> None:
    template = PROMPTS["chat_extraction"]
    rendered = template.render(
        {"customer_id": "CUST-0001", "input_json": "[]"}
    )
    assert "CUST-0001" in rendered
    assert "{{" not in rendered


def test_render_raises_on_missing_variable() -> None:
    template = PromptTemplate(
        workflow="test",
        version="1",
        temperature=0.0,
        system="sys",
        user="Hello {{name}}",
    )
    with pytest.raises(PromptRenderError):
        template.render({})


def test_render_allows_extra_variables() -> None:
    template = PromptTemplate(
        workflow="test",
        version="1",
        temperature=0.0,
        system="sys",
        user="Hello {{name}}",
    )
    assert template.render({"name": "Ada", "unused": "x"}) == "Hello Ada"


def test_prompt_template_is_immutable() -> None:
    template = PROMPTS["care_triage"]
    with pytest.raises(FrozenInstanceError):
        template.workflow = "changed"  # type: ignore[misc]

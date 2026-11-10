"""Versioned prompt templates loaded from package resources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files

PROMPT_VERSION = "2026.11.1"

WORKFLOW_TEMPERATURES: dict[str, float] = {
    "chat_extraction": 0.0,
    "care_triage": 0.0,
    "churn_narrative": 0.2,
    "offer_recommendation": 0.2,
}


class PromptRenderError(ValueError):
    """Raised when a template placeholder is missing from the variables."""


@dataclass(frozen=True)
class PromptTemplate:
    workflow: str
    version: str
    temperature: float
    system: str
    user: str

    def render(self, variables: Mapping[str, str]) -> str:
        text = self.user
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", value)
        if "{{" in text:
            raise PromptRenderError(
                f"Prompt {self.workflow!r} has unrendered placeholders; "
                "check the workflow variables."
            )
        return text


def _load(workflow: str, temperature: float) -> PromptTemplate:
    resource = (
        files("customer_intelligence")
        .joinpath("prompts")
        .joinpath(f"{workflow}.md")
    )
    text = resource.read_text(encoding="utf-8")
    for marker in ("## SYSTEM", "## USER"):
        if marker not in text:
            raise RuntimeError(f"Prompt {workflow!r} is missing the {marker!r} section")

    system = text.split("## SYSTEM", 1)[1].split("## USER", 1)[0].strip()
    user = text.split("## USER", 1)[1].strip()
    return PromptTemplate(
        workflow=workflow,
        version=PROMPT_VERSION,
        temperature=temperature,
        system=system,
        user=user,
    )


PROMPTS: dict[str, PromptTemplate] = {
    workflow: _load(workflow, temperature)
    for workflow, temperature in WORKFLOW_TEMPERATURES.items()
}

"""LLM client protocol, structured-output runner and OpenAI-compatible adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER
from .prompts import PROMPTS


class LLMError(RuntimeError):
    """Base class for LLM failures."""


class LLMOutputError(LLMError):
    """The model returned text that could not be validated as the contract."""


class LLMTransportError(LLMError):
    """The provider could not be reached or returned a non-2xx status."""


class LLMClient(Protocol):
    name: str

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        """Return the model's raw text response."""
        ...


ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_json_response(raw: str) -> Any:
    """Extract a JSON value from a raw model response.

    Handles plain JSON, fenced JSON and a chatty preamble around the object.
    """
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        object_match = re.search(r"\{.*\}", text, re.DOTALL)
        if object_match:
            try:
                return json.loads(object_match.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMOutputError(f"Response is not valid JSON: {first_error}") from first_error


class StructuredLLM:
    """Runs a prompt, validates the JSON contract and retries once on failure."""

    def __init__(self, client: LLMClient, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.client = client
        self.max_attempts = max_attempts

    def run(
        self,
        workflow: str,
        variables: Mapping[str, str],
        model: type[ModelT],
        validate: Callable[[ModelT], None] | None = None,
    ) -> ModelT:
        prompt = PROMPTS[workflow]
        base_user = prompt.render(variables)
        attempt_user = base_user
        last_error: Exception | None = None

        for _ in range(self.max_attempts):
            raw = self.client.complete(
                system=prompt.system,
                user=attempt_user,
                temperature=prompt.temperature,
            )
            try:
                payload = parse_json_response(raw)
                parsed = model.model_validate(payload)
                if validate is not None:
                    validate(parsed)
                return parsed
            except (LLMOutputError, ValidationError, ValueError) as exc:
                last_error = exc
                attempt_user = (
                    base_user
                    + "\n\nYour previous response was invalid: "
                    + str(exc)
                    + "\nReturn ONLY the JSON object, with no markdown or commentary."
                )

        raise LLMOutputError(
            f"{workflow} failed after {self.max_attempts} attempt(s): {last_error}"
        ) from last_error


class OpenAICompatibleClient:
    """Adapter for any OpenAI-compatible chat-completions endpoint."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise LLMTransportError(
                "A live LLM provider requires CI_LLM_API_KEY (or OPENAI_API_KEY)"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def complete(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"LLM request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMTransportError(
                f"LLM provider returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMTransportError("LLM provider returned an unexpected response body") from exc


def build_llm_client() -> LLMClient:
    """Select the configured provider. Deterministic is the safe default."""
    if LLM_PROVIDER in {"deterministic", "mock", "offline"}:
        from .deterministic import DeterministicLLM

        return DeterministicLLM()
    if LLM_PROVIDER in {"openai", "openai-compatible"}:
        return OpenAICompatibleClient(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
        )
    raise LLMTransportError(
        f"Unknown CI_LLM_PROVIDER={LLM_PROVIDER!r}; use 'deterministic' or 'openai'"
    )

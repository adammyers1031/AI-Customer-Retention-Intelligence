"""LLM provider, prompt registry and typed workflows."""

from .client import (
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMTransportError,
    OpenAICompatibleClient,
    StructuredLLM,
    build_llm_client,
    parse_json_response,
)
from .prompts import PROMPT_VERSION, PROMPTS, PromptRenderError, PromptTemplate
from .workflows import (
    extract_chat_signals,
    recommend_offer,
    triage_care,
    write_churn_narrative,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMOutputError",
    "LLMTransportError",
    "OpenAICompatibleClient",
    "PROMPTS",
    "PROMPT_VERSION",
    "PromptRenderError",
    "PromptTemplate",
    "StructuredLLM",
    "build_llm_client",
    "extract_chat_signals",
    "parse_json_response",
    "recommend_offer",
    "triage_care",
    "write_churn_narrative",
]

"""Pydantic contracts for ingestion, diagnostics, offers and API responses.

Input models forbid unknown fields, so a malformed producer fails fast with a
422 instead of writing partial records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskBand = Literal["low", "medium", "high", "critical"]
PlanName = Literal["starter", "standard", "premium", "business"]
ChatRole = Literal["customer", "agent"]
ChatChannel = Literal["chat", "email", "phone_transcript"]
OfferType = Literal["retention", "upsell", "cross_sell", "care"]
CareCategory = Literal[
    "billing_dispute",
    "technical_issue",
    "cancellation_request",
    "plan_change",
    "general_question",
]
Urgency = Literal["low", "normal", "high", "urgent"]

CUSTOMER_ID_PATTERN = r"^[A-Za-z0-9._-]+$"


class CustomerIn(BaseModel):
    """A subscriber account."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=3, max_length=64, pattern=CUSTOMER_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    plan: PlanName
    mrr: float = Field(ge=0, le=1_000_000)
    tenure_months: int = Field(ge=0, le=600)
    region: str = Field(default="us-east", max_length=32)
    marketing_opt_in: bool = True


class ChatMessageIn(BaseModel):
    """One message in a customer conversation."""

    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    message: str = Field(min_length=1, max_length=4000)
    channel: ChatChannel = "chat"
    ts: datetime | None = Field(default=None, description="Defaults to ingest time (UTC)")


class BillingRecordIn(BaseModel):
    """One monthly billing cycle."""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    invoiced_amount: float = Field(ge=0, le=1_000_000)
    paid_amount: float = Field(ge=0, le=1_000_000)
    payment_failures: int = Field(default=0, ge=0, le=100)
    late_payments: int = Field(default=0, ge=0, le=100)
    overage_charges: float = Field(default=0, ge=0, le=1_000_000)
    discount_amount: float = Field(default=0, ge=0, le=1_000_000)
    usage_gb: float = Field(default=0, ge=0, le=10_000_000)


class CustomerOut(BaseModel):
    customer_id: str
    name: str
    email: str | None
    plan: PlanName
    mrr: float
    tenure_months: int
    region: str
    marketing_opt_in: bool
    created_at: str


class ChatSignals(BaseModel):
    """Structured signals extracted from unstructured conversation text."""

    sentiment: float = Field(ge=-1.0, le=1.0)
    intents: list[str]
    cancellation_intent: bool
    competitor_mentioned: str | None
    unresolved_issue: bool
    effort_score: int = Field(ge=1, le=5)
    summary: str


class CustomerFeatures(BaseModel):
    """Feature vector used by the deterministic risk and offer engines."""

    customer_id: str
    tenure_months: int
    mrr: float
    payment_failures_90d: int
    late_payments_90d: int
    overage_charges_90d: float
    discount_active: bool
    usage_change_pct: float
    support_contacts_30d: int
    chat_messages: int
    complaint_count: int
    escalation_count: int
    sentiment_score: float
    cancellation_intent: bool
    competitor_mentioned: str | None
    unresolved_issue: bool
    effort_score: int
    days_since_last_chat: float


class ChatRecord(BaseModel):
    role: ChatRole
    message: str
    channel: ChatChannel
    ts: str


class RiskFactor(BaseModel):
    name: str
    weight: float
    value: float = Field(ge=0.0, le=1.0)
    contribution: float


class RiskScore(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    band: RiskBand
    factors: list[RiskFactor]
    top_drivers: list[str]


class ChurnAssessment(RiskScore):
    rationale: str
    save_play: str


class OfferDefinition(BaseModel):
    code: str
    name: str
    offer_type: OfferType
    discount_pct: float = Field(ge=0.0, le=100.0)
    duration_months: int = Field(ge=0)
    description: str
    tags: list[str]
    eligible_bands: list[RiskBand]
    base_lift_pct: float


class ChurnNarrative(BaseModel):
    rationale: str
    save_play: str


class OfferChoice(BaseModel):
    """Raw LLM decision, validated against the eligible catalogue."""

    code: str
    rationale: str
    message: str


class OfferRecommendation(BaseModel):
    code: str
    name: str
    offer_type: OfferType
    discount_pct: float
    duration_months: int
    rationale: str
    message: str
    expected_lift_pct: float


class CareAssessment(BaseModel):
    category: CareCategory
    urgency: Urgency
    next_best_action: str
    sla_hours: int = Field(ge=1, le=168)
    escalate: bool


class CustomerProfile(BaseModel):
    customer: CustomerOut
    features: CustomerFeatures
    chat_signals: ChatSignals
    churn: ChurnAssessment
    offer: OfferRecommendation | None
    care: CareAssessment
    analyzed_at: str
    prompt_version: str


class CustomerSummary(BaseModel):
    customer_id: str
    name: str
    plan: PlanName
    mrr: float
    score: float
    band: RiskBand
    offer_code: str | None
    offer_type: OfferType | None
    care_category: CareCategory
    care_urgency: Urgency
    analyzed_at: str


class MetricsSummary(BaseModel):
    total_customers: int
    analyzed_customers: int
    avg_risk_score: float
    high_risk_customers: int
    at_risk_mrr: float
    band_counts: dict[str, int]
    offers_by_type: dict[str, int]
    care_categories: dict[str, int]


class CustomerCreated(BaseModel):
    customer_id: str
    status: str = "created"


class ChatAccepted(BaseModel):
    customer_id: str
    messages_stored: int
    total_messages: int


class BillingAccepted(BaseModel):
    customer_id: str
    periods_stored: int
    total_periods: int


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    llm_provider: str

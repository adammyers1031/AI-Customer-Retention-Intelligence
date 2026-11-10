"""FastAPI surface for ingestion, analysis, offers and metrics."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from . import __version__, repository
from .config import AUTO_SEED, DEFAULT_DB_PATH, DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from .llm import PROMPT_VERSION, StructuredLLM
from .models import (
    BillingAccepted,
    BillingRecordIn,
    ChatAccepted,
    ChatMessageIn,
    ChatRecord,
    CustomerCreated,
    CustomerIn,
    CustomerOut,
    CustomerProfile,
    CustomerSummary,
    HealthOut,
    MetricsSummary,
    OfferDefinition,
    RiskBand,
)
from .offers import CATALOG
from .services import (
    CustomerNotFound,
    add_billing,
    add_chat,
    analyze_all,
    analyze_customer,
    build_runner,
    create_customer,
    seed_if_empty,
)


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Yield one SQLite connection per request."""
    connection = repository.connect(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


ConnectionDep = Annotated[sqlite3.Connection, Depends(get_conn)]


def create_app(
    db_path: str | Path | None = None,
    auto_seed: bool | None = None,
    runner: StructuredLLM | None = None,
) -> FastAPI:
    """Build the application. Tests inject a temp database and a runner."""
    resolved_db = Path(db_path or DEFAULT_DB_PATH)
    active_runner = runner or build_runner()
    should_seed = AUTO_SEED if auto_seed is None else auto_seed

    conn = repository.init_db(resolved_db)
    if should_seed:
        seed_if_empty(conn, active_runner)
    conn.close()

    app = FastAPI(
        title="AI Customer Retention Intelligence API",
        version=__version__,
        description=(
            "LLM-assisted extraction of churn signals from chat logs and billing "
            "history, a deterministic multi-factor risk score, policy-governed "
            "offer decisioning and customer-care triage."
        ),
    )
    app.state.db_path = resolved_db
    app.state.runner = active_runner

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "service": "customer-intelligence",
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health", response_model=HealthOut, tags=["system"])
    def health(request: Request) -> HealthOut:
        return HealthOut(
            status="ok",
            service="customer-intelligence",
            version=__version__,
            llm_provider=request.app.state.runner.client.name,
        )

    @app.post(
        "/api/v1/customers",
        response_model=CustomerCreated,
        status_code=201,
        tags=["customers"],
        summary="Create or update a customer",
    )
    def post_customer(payload: CustomerIn, conn: ConnectionDep) -> CustomerCreated:
        create_customer(conn, payload)
        return CustomerCreated(customer_id=payload.customer_id)

    @app.get(
        "/api/v1/customers",
        response_model=list[CustomerSummary],
        tags=["customers"],
        summary="Analyzed customers ranked by churn risk",
    )
    def get_customers(
        conn: ConnectionDep,
        risk_band: Annotated[RiskBand | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    ) -> list[CustomerSummary]:
        return repository.list_summaries(conn, band=risk_band, limit=limit)

    @app.get(
        "/api/v1/customers/{customer_id}",
        response_model=CustomerOut,
        tags=["customers"],
    )
    def get_customer(customer_id: str, conn: ConnectionDep) -> CustomerOut:
        customer = repository.get_customer(conn, customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail=f"Unknown customer: {customer_id}")
        return customer

    @app.post(
        "/api/v1/customers/{customer_id}/chats",
        response_model=ChatAccepted,
        status_code=201,
        tags=["ingestion"],
        summary="Append one chat message",
    )
    def post_chat(
        customer_id: str, payload: ChatMessageIn, conn: ConnectionDep
    ) -> ChatAccepted:
        try:
            stored, total = add_chat(conn, customer_id, payload)
        except CustomerNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown customer: {customer_id}"
            ) from exc
        return ChatAccepted(customer_id=customer_id, messages_stored=stored, total_messages=total)

    @app.get(
        "/api/v1/customers/{customer_id}/chats",
        response_model=list[ChatRecord],
        tags=["ingestion"],
    )
    def get_chats(customer_id: str, conn: ConnectionDep) -> list[ChatRecord]:
        if repository.get_customer(conn, customer_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown customer: {customer_id}")
        return repository.list_chats(conn, customer_id)

    @app.post(
        "/api/v1/customers/{customer_id}/billing",
        response_model=BillingAccepted,
        status_code=201,
        tags=["ingestion"],
        summary="Upsert one monthly billing cycle",
    )
    def post_billing(
        customer_id: str, payload: BillingRecordIn, conn: ConnectionDep
    ) -> BillingAccepted:
        try:
            stored, total = add_billing(conn, customer_id, payload)
        except CustomerNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown customer: {customer_id}"
            ) from exc
        return BillingAccepted(customer_id=customer_id, periods_stored=stored, total_periods=total)

    @app.get(
        "/api/v1/customers/{customer_id}/billing",
        response_model=list[BillingRecordIn],
        tags=["ingestion"],
    )
    def get_billing(customer_id: str, conn: ConnectionDep) -> list[BillingRecordIn]:
        if repository.get_customer(conn, customer_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown customer: {customer_id}")
        return repository.list_billing(conn, customer_id)

    @app.post(
        "/api/v1/customers/{customer_id}/analyze",
        response_model=CustomerProfile,
        tags=["analysis"],
        summary="Run extraction, scoring, offer and care workflows",
    )
    def post_analyze(
        customer_id: str, conn: ConnectionDep, request: Request
    ) -> CustomerProfile:
        try:
            return analyze_customer(conn, customer_id, request.app.state.runner)
        except CustomerNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown customer: {customer_id}"
            ) from exc

    @app.get(
        "/api/v1/customers/{customer_id}/profile",
        response_model=CustomerProfile,
        tags=["analysis"],
    )
    def get_profile(customer_id: str, conn: ConnectionDep) -> CustomerProfile:
        profile = repository.get_analysis(conn, customer_id)
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"No analysis stored for customer: {customer_id}",
            )
        return profile

    @app.post(
        "/api/v1/analyze",
        response_model=list[CustomerProfile],
        tags=["analysis"],
        summary="Run the pipeline for every stored customer",
    )
    def post_analyze_all(request: Request, conn: ConnectionDep) -> list[CustomerProfile]:
        return analyze_all(conn, request.app.state.runner, limit=MAX_LIST_LIMIT)

    @app.get(
        "/api/v1/metrics/summary",
        response_model=MetricsSummary,
        tags=["metrics"],
        summary="Fleet risk, revenue-at-risk and queue mix",
    )
    def get_summary(conn: ConnectionDep) -> MetricsSummary:
        return repository.summary_metrics(conn)

    @app.get(
        "/api/v1/offers",
        response_model=list[OfferDefinition],
        tags=["offers"],
        summary="Policy-approved offer catalogue",
    )
    def get_offers() -> list[OfferDefinition]:
        return list(CATALOG)

    @app.get("/api/v1/prompts", tags=["analysis"], summary="Prompt version metadata")
    def get_prompts() -> dict[str, object]:
        return {
            "version": PROMPT_VERSION,
            "workflows": [
                "chat_extraction",
                "churn_narrative",
                "care_triage",
                "offer_recommendation",
            ],
        }

    return app

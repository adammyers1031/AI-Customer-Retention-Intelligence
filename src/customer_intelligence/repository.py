"""SQLite persistence and aggregate queries.

All SQL is parameterised. The schema ships inside the package so the Docker
image needs no repository checkout at runtime.
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

from .models import (
    BillingRecordIn,
    ChatMessageIn,
    ChatRecord,
    CustomerIn,
    CustomerOut,
    CustomerProfile,
    CustomerSummary,
    MetricsSummary,
    RiskBand,
)
from .timeutil import to_iso, utcnow


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection. Each request owns its connection, so cross-thread use
    is safe even though FastAPI runs sync endpoints in a worker thread."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    schema = (
        files("customer_intelligence")
        .joinpath("sql")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )
    conn.executescript(schema)
    conn.commit()
    return conn


def reset_database(conn: sqlite3.Connection) -> None:
    for table in ("analyses", "billing", "chats", "customers"):
        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
    conn.commit()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def upsert_customer(
    conn: sqlite3.Connection, payload: CustomerIn, created_at: str | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO customers
            (customer_id, name, email, plan, mrr, tenure_months, region,
             marketing_opt_in, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET
            name = excluded.name,
            email = excluded.email,
            plan = excluded.plan,
            mrr = excluded.mrr,
            tenure_months = excluded.tenure_months,
            region = excluded.region,
            marketing_opt_in = excluded.marketing_opt_in
        """,
        (
            payload.customer_id,
            payload.name,
            payload.email,
            payload.plan,
            payload.mrr,
            payload.tenure_months,
            payload.region,
            int(payload.marketing_opt_in),
            created_at or to_iso(utcnow()),
        ),
    )


def insert_chat(
    conn: sqlite3.Connection, customer_id: str, message: ChatMessageIn, ts: str
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO chats (customer_id, role, message, channel, ts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (customer_id, message.role, message.message, message.channel, ts),
    )
    return int(cursor.lastrowid or 0)


def upsert_billing(
    conn: sqlite3.Connection, customer_id: str, record: BillingRecordIn
) -> None:
    conn.execute(
        """
        INSERT INTO billing
            (customer_id, period, invoiced_amount, paid_amount, payment_failures,
             late_payments, overage_charges, discount_amount, usage_gb)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id, period) DO UPDATE SET
            invoiced_amount = excluded.invoiced_amount,
            paid_amount = excluded.paid_amount,
            payment_failures = excluded.payment_failures,
            late_payments = excluded.late_payments,
            overage_charges = excluded.overage_charges,
            discount_amount = excluded.discount_amount,
            usage_gb = excluded.usage_gb
        """,
        (
            customer_id,
            record.period,
            record.invoiced_amount,
            record.paid_amount,
            record.payment_failures,
            record.late_payments,
            record.overage_charges,
            record.discount_amount,
            record.usage_gb,
        ),
    )


def save_analysis(conn: sqlite3.Connection, profile: CustomerProfile) -> None:
    conn.execute(
        """
        INSERT INTO analyses
            (customer_id, analyzed_at, score, band, offer_code, offer_type,
             care_category, care_urgency, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET
            analyzed_at = excluded.analyzed_at,
            score = excluded.score,
            band = excluded.band,
            offer_code = excluded.offer_code,
            offer_type = excluded.offer_type,
            care_category = excluded.care_category,
            care_urgency = excluded.care_urgency,
            payload = excluded.payload
        """,
        (
            profile.customer.customer_id,
            profile.analyzed_at,
            profile.churn.score,
            profile.churn.band,
            profile.offer.code if profile.offer else None,
            profile.offer.offer_type if profile.offer else None,
            profile.care.category,
            profile.care.urgency,
            profile.model_dump_json(),
        ),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _customer_from_row(row: sqlite3.Row) -> CustomerOut:
    return CustomerOut(
        customer_id=row["customer_id"],
        name=row["name"],
        email=row["email"],
        plan=row["plan"],
        mrr=float(row["mrr"]),
        tenure_months=int(row["tenure_months"]),
        region=row["region"],
        marketing_opt_in=bool(row["marketing_opt_in"]),
        created_at=row["created_at"],
    )


def get_customer(conn: sqlite3.Connection, customer_id: str) -> CustomerOut | None:
    row = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return _customer_from_row(row) if row else None


def list_customers(conn: sqlite3.Connection) -> list[CustomerOut]:
    rows = conn.execute("SELECT * FROM customers ORDER BY customer_id").fetchall()
    return [_customer_from_row(row) for row in rows]


def list_chats(conn: sqlite3.Connection, customer_id: str) -> list[ChatRecord]:
    rows = conn.execute(
        """
        SELECT role, message, channel, ts
        FROM chats
        WHERE customer_id = ?
        ORDER BY ts, id
        """,
        (customer_id,),
    ).fetchall()
    return [
        ChatRecord(
            role=row["role"],
            message=row["message"],
            channel=row["channel"],
            ts=row["ts"],
        )
        for row in rows
    ]


def count_chats(conn: sqlite3.Connection, customer_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM chats WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return int(row["c"]) if row else 0


def list_billing(conn: sqlite3.Connection, customer_id: str) -> list[BillingRecordIn]:
    rows = conn.execute(
        """
        SELECT period, invoiced_amount, paid_amount, payment_failures,
               late_payments, overage_charges, discount_amount, usage_gb
        FROM billing
        WHERE customer_id = ?
        ORDER BY period
        """,
        (customer_id,),
    ).fetchall()
    return [
        BillingRecordIn(
            period=row["period"],
            invoiced_amount=float(row["invoiced_amount"]),
            paid_amount=float(row["paid_amount"]),
            payment_failures=int(row["payment_failures"]),
            late_payments=int(row["late_payments"]),
            overage_charges=float(row["overage_charges"]),
            discount_amount=float(row["discount_amount"]),
            usage_gb=float(row["usage_gb"]),
        )
        for row in rows
    ]


def count_billing(conn: sqlite3.Connection, customer_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM billing WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    return int(row["c"]) if row else 0


def get_analysis(conn: sqlite3.Connection, customer_id: str) -> CustomerProfile | None:
    row = conn.execute(
        "SELECT payload FROM analyses WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        return None
    return CustomerProfile.model_validate_json(row["payload"])


def list_summaries(
    conn: sqlite3.Connection, band: RiskBand | None = None, limit: int = 50
) -> list[CustomerSummary]:
    query = """
        SELECT c.customer_id, c.name, c.plan, c.mrr,
               a.score, a.band, a.offer_code, a.offer_type,
               a.care_category, a.care_urgency, a.analyzed_at
        FROM analyses a
        JOIN customers c ON c.customer_id = a.customer_id
    """
    params: list[object] = []
    if band is not None:
        query += " WHERE a.band = ?"
        params.append(band)
    query += " ORDER BY a.score DESC, c.customer_id LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [
        CustomerSummary(
            customer_id=row["customer_id"],
            name=row["name"],
            plan=row["plan"],
            mrr=float(row["mrr"]),
            score=float(row["score"]),
            band=row["band"],
            offer_code=row["offer_code"],
            offer_type=row["offer_type"],
            care_category=row["care_category"],
            care_urgency=row["care_urgency"],
            analyzed_at=row["analyzed_at"],
        )
        for row in rows
    ]


def _group_counts(conn: sqlite3.Connection, query: str, column: str) -> dict[str, int]:
    rows = conn.execute(query).fetchall()
    return {str(row[column]): int(row["count"]) for row in rows}


def summary_metrics(conn: sqlite3.Connection) -> MetricsSummary:
    total = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
    analyzed = conn.execute("SELECT COUNT(*) AS c FROM analyses").fetchone()
    aggregate = conn.execute(
        """
        SELECT
            COALESCE(AVG(a.score), 0) AS avg_score,
            COALESCE(SUM(CASE WHEN a.band IN ('high', 'critical') THEN 1 ELSE 0 END), 0)
                AS high_risk,
            COALESCE(SUM(CASE WHEN a.band IN ('high', 'critical') THEN c.mrr ELSE 0 END), 0)
                AS at_risk_mrr
        FROM analyses a
        JOIN customers c ON c.customer_id = a.customer_id
        """
    ).fetchone()

    band_counts = _group_counts(
        conn,
        "SELECT band, COUNT(*) AS count FROM analyses GROUP BY band",
        "band",
    )
    offers_by_type = _group_counts(
        conn,
        """
        SELECT offer_type, COUNT(*) AS count
        FROM analyses
        WHERE offer_type IS NOT NULL
        GROUP BY offer_type
        """,
        "offer_type",
    )
    care_categories = _group_counts(
        conn,
        "SELECT care_category, COUNT(*) AS count FROM analyses GROUP BY care_category",
        "care_category",
    )

    return MetricsSummary(
        total_customers=int(total["c"]) if total else 0,
        analyzed_customers=int(analyzed["c"]) if analyzed else 0,
        avg_risk_score=round(float(aggregate["avg_score"]), 1) if aggregate else 0.0,
        high_risk_customers=int(aggregate["high_risk"]) if aggregate else 0,
        at_risk_mrr=round(float(aggregate["at_risk_mrr"]), 2) if aggregate else 0.0,
        band_counts=band_counts,
        offers_by_type=offers_by_type,
        care_categories=care_categories,
    )

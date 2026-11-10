PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    email            TEXT,
    plan             TEXT NOT NULL CHECK (plan IN ('starter', 'standard', 'premium', 'business')),
    mrr              REAL NOT NULL,
    tenure_months    INTEGER NOT NULL,
    region           TEXT NOT NULL,
    marketing_opt_in INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('customer', 'agent')),
    message     TEXT NOT NULL,
    channel     TEXT NOT NULL CHECK (channel IN ('chat', 'email', 'phone_transcript')),
    ts          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chats_customer_ts ON chats(customer_id, ts);

CREATE TABLE IF NOT EXISTS billing (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    period           TEXT NOT NULL,
    invoiced_amount  REAL NOT NULL,
    paid_amount      REAL NOT NULL,
    payment_failures INTEGER NOT NULL,
    late_payments    INTEGER NOT NULL,
    overage_charges  REAL NOT NULL,
    discount_amount  REAL NOT NULL,
    usage_gb         REAL NOT NULL,
    UNIQUE(customer_id, period)
);

CREATE INDEX IF NOT EXISTS idx_billing_customer_period ON billing(customer_id, period);

CREATE TABLE IF NOT EXISTS analyses (
    customer_id   TEXT PRIMARY KEY REFERENCES customers(customer_id) ON DELETE CASCADE,
    analyzed_at   TEXT NOT NULL,
    score         REAL NOT NULL,
    band          TEXT NOT NULL,
    offer_code    TEXT,
    offer_type    TEXT,
    care_category TEXT NOT NULL,
    care_urgency  TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_score ON analyses(score DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_band ON analyses(band);

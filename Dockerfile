# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage: resolve and install the application into an isolated prefix.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --prefix=/install .

# ---------------------------------------------------------------------------
# Runtime stage: no compilers, no build tooling, non-root user.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CI_DB_PATH=/data/retention.db \
    CI_AUTO_SEED=1 \
    CI_LLM_PROVIDER=deterministic

WORKDIR /app

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "customer_intelligence.asgi:app", "--host", "0.0.0.0", "--port", "8000"]

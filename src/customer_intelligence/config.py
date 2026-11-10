"""Runtime paths and settings.

Every setting can be overridden with an environment variable, which keeps the
same code path working for local runs, tests and the Docker image.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_DB_PATH = Path(os.environ.get("CI_DB_PATH", str(DATA_DIR / "retention.db")))
AUTO_SEED = os.environ.get("CI_AUTO_SEED", "1") == "1"

LLM_PROVIDER = os.environ.get("CI_LLM_PROVIDER", "deterministic").strip().lower()
LLM_BASE_URL = os.environ.get("CI_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("CI_LLM_MODEL", "gpt-4o-mini")
LLM_API_KEY = os.environ.get("CI_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

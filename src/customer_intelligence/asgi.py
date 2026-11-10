"""ASGI entrypoint for container and production servers."""

from __future__ import annotations

from .api import create_app

app = create_app()

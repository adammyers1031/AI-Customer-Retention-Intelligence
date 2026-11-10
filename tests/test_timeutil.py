"""UTC datetime helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from customer_intelligence.timeutil import minutes_ago, parse_iso, to_iso, utcnow


def test_utcnow_is_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_to_iso_formats_utc() -> None:
    value = datetime(2026, 11, 9, 12, 30, 5, tzinfo=timezone.utc)
    assert to_iso(value) == "2026-11-09T12:30:05Z"


def test_to_iso_treats_naive_as_utc() -> None:
    value = datetime(2026, 11, 9, 12, 30, 5)
    assert to_iso(value) == "2026-11-09T12:30:05Z"


def test_to_iso_accepts_string() -> None:
    assert to_iso("2026-11-09T12:30:05Z") == "2026-11-09T12:30:05Z"


def test_parse_iso_accepts_z_suffix() -> None:
    parsed = parse_iso("2026-11-09T12:30:05Z")
    assert parsed.tzinfo is not None
    assert parsed.year == 2026
    assert parsed.hour == 12


def test_minutes_ago_is_monotonic() -> None:
    recent = parse_iso(minutes_ago(1))
    older = parse_iso(minutes_ago(60))
    assert recent > older

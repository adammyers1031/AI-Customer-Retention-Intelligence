"""CLI commands run against a temporary database."""

from __future__ import annotations

import json
from pathlib import Path

from customer_intelligence.cli import main


def test_seed_and_report_commands(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"

    assert main(["--db", str(db), "seed", "--reset"]) == 0
    seeded = capsys.readouterr().out
    assert "Seeded 12 customers" in seeded
    assert "1 critical" in seeded

    assert main(["--db", str(db), "report", "--limit", "5"]) == 0
    report = capsys.readouterr().out
    assert "CUST-0004" in report
    assert "WINBACK_CALL" in report
    assert "Customers: 12" in report
    assert "revenue at risk: $492" in report


def test_analyze_one_customer(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    main(["--db", str(db), "seed", "--reset"])
    capsys.readouterr()

    assert main(["--db", str(db), "analyze", "--customer", "CUST-0002"]) == 0
    output = capsys.readouterr().out
    assert "CUST-0002" in output
    assert "prompt version" in output


def test_analyze_unknown_customer_fails(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    main(["--db", str(db), "seed", "--reset"])
    capsys.readouterr()

    assert main(["--db", str(db), "analyze", "--customer", "CUST-9999"]) == 1
    assert "Unknown customer" in capsys.readouterr().out


def test_show_command_outputs_json(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    main(["--db", str(db), "seed", "--reset"])
    capsys.readouterr()

    assert main(["--db", str(db), "show", "CUST-0004"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["customer"]["customer_id"] == "CUST-0004"
    assert payload["churn"]["band"] == "critical"


def test_show_unknown_customer_fails(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    main(["--db", str(db), "seed", "--reset"])
    capsys.readouterr()

    assert main(["--db", str(db), "show", "CUST-9999"]) == 1
    assert "No analysis stored" in capsys.readouterr().out

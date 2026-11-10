"""Command-line interface.

    customer-intel seed --reset
    customer-intel analyze
    customer-intel report
    customer-intel show CUST-0004
    customer-intel serve --port 8000
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import repository
from .config import DEFAULT_DB_PATH
from .models import CustomerProfile
from .services import (
    CustomerNotFound,
    analyze_all,
    analyze_customer,
    build_runner,
    seed_demo_data,
)


def _resolve_db(args: argparse.Namespace) -> Path:
    return Path(args.db or DEFAULT_DB_PATH)


def cmd_seed(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        counts = seed_demo_data(conn, build_runner(), reset=args.reset)
    finally:
        conn.close()
    print(
        "Seeded {customers} customers, {chats} chats, {billing} billing rows, "
        "{analyses} analyses ({critical} critical).".format(**counts)
    )
    return 0


def _print_profile(profile: CustomerProfile) -> None:
    churn = profile.churn
    print(
        f"{profile.customer.customer_id} {profile.customer.name}: "
        f"{churn.score:.1f}/100 {churn.band.upper()} -> "
        f"{profile.offer.code if profile.offer else 'no offer'} | "
        f"care={profile.care.category}/{profile.care.urgency}"
    )
    print(f"  rationale: {churn.rationale}")
    print(f"  save play: {churn.save_play}")


def cmd_analyze(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        runner = build_runner()
        if args.customer:
            profiles = [analyze_customer(conn, args.customer, runner)]
        else:
            profiles = analyze_all(conn, runner)
    except CustomerNotFound:
        print(f"Unknown customer: {args.customer}")
        return 1
    finally:
        conn.close()

    for profile in profiles:
        _print_profile(profile)
    version = profiles[0].prompt_version if profiles else "-"
    print(f"Analyzed {len(profiles)} customer(s) with prompt version {version}.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        summaries = repository.list_summaries(conn, limit=args.limit)
        metrics = repository.summary_metrics(conn)
    finally:
        conn.close()

    header = (
        f"{'ID':<10} {'NAME':<18} {'PLAN':<9} {'MRR':>7} "
        f"{'SCORE':>6} {'BAND':<9} {'OFFER':<14} CARE"
    )
    print(header)
    print("-" * len(header))
    for item in summaries:
        print(
            f"{item.customer_id:<10} {item.name[:18]:<18} {item.plan:<9} "
            f"{item.mrr:>7.0f} {item.score:>6.1f} {item.band:<9} "
            f"{(item.offer_code or '-'):<14} {item.care_category}"
        )

    print()
    print(
        f"Customers: {metrics.total_customers} | analyzed: {metrics.analyzed_customers} | "
        f"avg risk: {metrics.avg_risk_score} | high/critical: {metrics.high_risk_customers} | "
        f"revenue at risk: ${metrics.at_risk_mrr:,.0f}"
    )
    print(f"Bands: {metrics.band_counts}")
    print(f"Offers: {metrics.offers_by_type}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        profile = repository.get_analysis(conn, args.customer)
    finally:
        conn.close()
    if profile is None:
        print(f"No analysis stored for customer: {args.customer}")
        return 1
    print(profile.model_dump_json(indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    app = create_app(db_path=_resolve_db(args), auto_seed=not args.no_seed)
    print(f"Serving API on http://{args.host}:{args.port} (docs at /docs)")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customer-intel",
        description="AI customer retention intelligence pipeline.",
    )
    parser.add_argument("--db", default=None, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Populate the deterministic demo cohort")
    seed.add_argument("--reset", action="store_true", help="Delete existing rows first")
    seed.set_defaults(func=cmd_seed)

    analyze = sub.add_parser("analyze", help="Run the AI pipeline")
    analyze.add_argument("--customer", default=None, help="Analyze one customer only")
    analyze.set_defaults(func=cmd_analyze)

    report = sub.add_parser("report", help="Print the risk and offer report")
    report.add_argument("--limit", type=int, default=50)
    report.set_defaults(func=cmd_report)

    show = sub.add_parser("show", help="Print one stored profile as JSON")
    show.add_argument("customer")
    show.set_defaults(func=cmd_show)

    serve = sub.add_parser("serve", help="Run the FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-seed", action="store_true", help="Do not auto-seed an empty DB")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

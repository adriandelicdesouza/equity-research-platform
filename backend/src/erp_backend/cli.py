"""Administrative CLI.

    python -m erp_backend.cli create-user --username admin --display-name "Admin" \
        --role admin --password-file /run/secrets/initial_admin_password
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from erp_backend import audit
from erp_backend.api.admin import create_user_record
from erp_backend.auth.rbac import Role
from erp_backend.db import get_db
from erp_backend.errors import ApiError


def _create_user(args: argparse.Namespace) -> int:
    if args.password_file:
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    else:
        password = sys.stdin.readline().rstrip("\n")
    db = next(get_db())
    try:
        user = create_user_record(
            db,
            username=args.username,
            display_name=args.display_name,
            password=password,
            roles=args.role,
        )
        audit.record(
            db,
            action="user_created",
            entity_type="user",
            entity_id=str(user.id),
            after={"username": user.username, "roles": sorted(user.role_codes)},
            reason="created via CLI",
        )
        db.commit()
    except ApiError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"created user {user.username} ({', '.join(sorted(user.role_codes))})")
    return 0


def _enqueue_ping(message: str) -> int:
    from erp_backend import tasks
    from erp_backend.jobs import defer, sync_app

    with sync_app().open() as queue:
        job_id = defer(queue, tasks.ping.name, message=message)
    print(f"queued system.ping job {job_id}")
    return 0


def _sec_check_config() -> int:
    from erp_backend.config import get_settings
    from erp_backend.ingestion.http import ProviderNotConfiguredError
    from erp_backend.ingestion.sec import validate_user_agent

    try:
        validate_user_agent(get_settings().sec_user_agent)
    except ProviderNotConfiguredError as exc:
        print(f"SEC User-Agent: NOT configured — {exc}", file=sys.stderr)
        return 1
    print("SEC User-Agent: configured and valid (value not shown)")
    return 0


def _sec_ingest(tickers: list[str], ciks: list[int]) -> int:
    from erp_backend import tasks
    from erp_backend.jobs import defer, sync_app

    if not tickers and not ciks:
        print("error: give at least one --ticker or --cik", file=sys.stderr)
        return 2
    with sync_app().open() as queue:
        for t in tickers:
            job = defer(queue, tasks.sec_ingest_company.name, ticker=t.upper())
            print(f"queued sec.ingest_company ticker={t.upper()} job {job}")
        for c in ciks:
            job = defer(queue, tasks.sec_ingest_company.name, cik=c)
            print(f"queued sec.ingest_company cik={c} job {job}")
    return 0


def _sec_rebuild() -> int:
    from sqlalchemy import select

    from erp_backend.config import get_settings
    from erp_backend.db import new_session
    from erp_backend.ingestion.sec_ingest import rebuild_company_from_archive
    from erp_backend.models import Company, CompanyIdentifier

    with new_session() as db:
        companies = db.scalars(
            select(Company)
            .join(CompanyIdentifier, CompanyIdentifier.company_id == Company.id)
            .where(CompanyIdentifier.scheme == "SEC_CIK")
            .order_by(Company.legal_name)
        ).all()
        for company in companies:
            level = rebuild_company_from_archive(db, get_settings().raw_root, company)
            print(
                f"{company.legal_name}: country={company.country} "
                f"currency={company.reporting_currency} coverage={level}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erp_backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user", help="Create a user (password from file or stdin).")
    cu.add_argument("--username", required=True)
    cu.add_argument("--display-name", required=True)
    cu.add_argument("--role", action="append", required=True, choices=[r.value for r in Role])
    cu.add_argument("--password-file")
    ping = sub.add_parser("enqueue-ping", help="Queue a system.ping job (queue health check).")
    ping.add_argument("--message", default="ping")
    sub.add_parser(
        "sec-check-config", help="Validate the SEC User-Agent configuration (makes no request)."
    )
    ingest = sub.add_parser("sec-ingest", help="Queue SEC ingestion jobs for tickers or CIKs.")
    ingest.add_argument("--ticker", action="append", default=[])
    ingest.add_argument("--cik", action="append", type=int, default=[])
    sub.add_parser(
        "sec-rebuild-from-archive",
        help="Re-derive SEC company profiles and inferences from the raw archive "
        "and stored facts (no network).",
    )
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return _create_user(args)
    if args.command == "enqueue-ping":
        return _enqueue_ping(args.message)
    if args.command == "sec-check-config":
        return _sec_check_config()
    if args.command == "sec-ingest":
        return _sec_ingest(args.ticker, args.cik)
    if args.command == "sec-rebuild-from-archive":
        return _sec_rebuild()
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

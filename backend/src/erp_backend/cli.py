"""Administrative CLI.

    python -m erp_backend.cli create-user --username admin --display-name "Admin" \
        --role admin --password-file /run/secrets/initial_admin_password
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from erp_backend import audit
from erp_backend.api.admin import create_user_record
from erp_backend.auth.rbac import Role
from erp_backend.db import get_db
from erp_backend.errors import ApiError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from erp_backend.models import Company


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


KEY_ITEMS = (
    "IS.REVENUE", "IS.EBIT", "IS.NET_INCOME", "IS.EPS_DILUTED", "BS.CASH", "BS.TOTAL_ASSETS",
    "BS.TOTAL_LIABILITIES", "BS.TOTAL_EQUITY", "CF.CFO", "CF.CAPEX", "CF.DIVIDENDS",
)  # fmt: skip


def _sec_standardize_report(
    ticker: str, years: int, policy_name: str, as_of: str | None, show_all: bool
) -> int:
    """Read-only coverage report: stored SEC facts → mapped standard line items (no writes)."""
    from datetime import date

    from sqlalchemy import select

    from erp_backend.config import get_settings
    from erp_backend.db import new_session
    from erp_backend.ingestion.sec_ingest import stored_facts
    from erp_backend.ingestion.selection import Policy
    from erp_backend.ingestion.selection import select as select_facts
    from erp_backend.models import Company, Security
    from erp_backend.standardization import mapping as mapping_mod
    from erp_backend.standardization.resolve import (
        ValueStatus,
        annual_coverage,
        resolve,
        taxonomy_for_standard,
    )

    settings = get_settings()
    mapping = mapping_mod.load(settings.mappings_root, settings.mapping_version)
    policy = Policy(policy_name)
    with new_session() as db:
        company = db.scalars(
            select(Company)
            .join(Security, Security.company_id == Company.id)
            .where(Security.ticker == ticker.upper())
        ).first()
        if company is None:
            print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
            return 1
        taxonomy = taxonomy_for_standard(company.accounting_standard)
        if taxonomy is None or company.reporting_currency is None:
            print("error: accounting standard or reporting currency unknown", file=sys.stderr)
            return 1
        facts = stored_facts(db, company.id)
    selections = select_facts(facts, date.fromisoformat(as_of) if as_of else None)
    values = resolve(
        selections, mapping, taxonomy=taxonomy, currency=company.reporting_currency, policy=policy
    )
    cov = annual_coverage(values, mapping, taxonomy, years=years)
    print(
        f"{company.legal_name} ({ticker.upper()}): {taxonomy}, {company.reporting_currency}, "
        f"mapping {mapping.version}, policy {policy.value}, facts as of {as_of or 'all'}"
    )
    print(f"mapped line items for {taxonomy}: {len(cov.mapped)}")
    annual = {
        (v.line_item, v.period_end): v
        for v in values
        if v.period_end in cov.year_ends
        and (mapping.line_items[v.line_item].period == "instant" or (v.duration_days or 0) > 349)
    }
    for end in cov.year_ends:
        missing = cov.missing(end)
        print(f"\nFY ending {end}: {len(cov.mapped) - len(missing)}/{len(cov.mapped)} mapped items")
        codes = [c for c in mapping.line_items if c in cov.mapped] if show_all else KEY_ITEMS
        for code in codes:
            v = annual.get((code, end))
            if v is None:
                continue
            shown = "AMBIGUOUS" if v.status is ValueStatus.AMBIGUOUS else f"{v.value:,}"
            fallback = f" (fallback #{v.priority})" if v.priority > 1 else ""
            print(f"  {code:<34} {shown:>22}  {v.concept}{fallback}")
        if missing:
            print(f"  missing: {', '.join(missing)}")
    return 0


def _company_by_ticker(db: Session, ticker: str) -> Company | None:
    from sqlalchemy import select

    from erp_backend.models import Company, Security

    return db.scalars(
        select(Company)
        .join(Security, Security.company_id == Company.id)
        .where(Security.ticker == ticker.upper())
    ).first()


def _dataset_build(ticker: str, policy_name: str, as_of: str | None) -> int:
    import json
    from datetime import date

    from erp_backend.config import get_settings
    from erp_backend.datasets import DatasetError, build_dataset
    from erp_backend.db import new_session
    from erp_backend.ingestion.selection import Policy

    settings = get_settings()
    with new_session() as db:
        company = _company_by_ticker(db, ticker)
        if company is None:
            print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
            return 1
        try:
            result = build_dataset(
                db,
                company,
                mappings_root=settings.mappings_root,
                mapping_version=settings.mapping_version,
                policy=Policy(policy_name),
                facts_as_of=date.fromisoformat(as_of) if as_of else None,
            )
        except DatasetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        db.commit()
        d = result.dataset
        state = "created" if result.created else "unchanged (identical to current version)"
        print(f"{company.legal_name}: dataset v{d.version_no} {d.status} — {state}")
        print(f"  id {d.id}\n  content_sha256 {d.content_sha256}\n  values {d.value_count}")
        print(f"  summary {json.dumps(d.source_summary, sort_keys=True)}")
    return 0


def _dataset_list(ticker: str) -> int:
    from sqlalchemy import select

    from erp_backend.db import new_session
    from erp_backend.models import FinancialDataset

    with new_session() as db:
        company = _company_by_ticker(db, ticker)
        if company is None:
            print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
            return 1
        for d in db.scalars(
            select(FinancialDataset)
            .where(FinancialDataset.company_id == company.id)
            .order_by(FinancialDataset.version_no)
        ):
            print(
                f"v{d.version_no} {d.status:<9} {d.id} {d.selection_policy} as of "
                f"{d.facts_as_of} mapping {d.mapping_version} values {d.value_count} "
                f"sha {d.content_sha256[:16]}"
            )
    return 0


def _dataset_action(action: str, dataset_id: str, reason: str | None) -> int:
    import uuid

    from erp_backend.config import get_settings
    from erp_backend.datasets import (
        DatasetError,
        discard_dataset,
        lock_dataset,
        verify_dataset,
    )
    from erp_backend.db import new_session
    from erp_backend.models import FinancialDataset

    root = get_settings().mappings_root
    with new_session() as db:
        try:
            if action == "verify":
                dataset = db.get(FinancialDataset, uuid.UUID(dataset_id))
                if dataset is None:
                    raise DatasetError(f"No dataset {dataset_id}")
                check = verify_dataset(db, dataset, mappings_root=root)
                print(f"{'OK' if check.ok else 'FAILED'}: {check}")
                return 0 if check.ok else 1
            if not reason:
                raise DatasetError("--reason is required")
            if action == "lock":
                d = lock_dataset(db, uuid.UUID(dataset_id), mappings_root=root, reason=reason)
            else:
                d = discard_dataset(db, uuid.UUID(dataset_id), reason=reason)
            db.commit()
        except DatasetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"dataset {d.id} v{d.version_no}: {d.status}")
    return 0


def _conflict_list(dataset_id: str, show_all: bool) -> int:
    import uuid

    from sqlalchemy import select

    from erp_backend.db import new_session
    from erp_backend.models import DataConflict, FiscalPeriodRow

    with new_session() as db:
        rows = db.execute(
            select(DataConflict, FiscalPeriodRow)
            .join(FiscalPeriodRow, FiscalPeriodRow.id == DataConflict.period_id)
            .where(DataConflict.dataset_id == uuid.UUID(dataset_id))
            .order_by(FiscalPeriodRow.end_date, DataConflict.line_item_code)
        ).all()
        for c, p in rows:
            if not show_all and c.status == "auto_resolved_immaterial":
                continue
            cands = "; ".join(
                f"fact {x['fact_id']} {x['role']} {x['value']} ({x['form']} {x['accession']} "
                f"filed {x['filed']})"
                for x in c.candidates
            )
            print(
                f"{c.conflict_key} {c.status:<24} {c.kind:<13} {c.line_item_code:<22} "
                f"{p.period_type} {p.end_date} material={c.is_material} "
                f"diff={c.absolute_difference} ({c.relative_difference}) threshold={c.threshold}"
                f"\n    candidates: {cands}"
            )
        print(f"{len(rows)} conflict(s) in dataset {dataset_id}")
    return 0


def _conflict_resolve(args: argparse.Namespace) -> int:
    import uuid
    from decimal import Decimal, InvalidOperation

    from erp_backend.config import get_settings
    from erp_backend.datasets import DatasetError, rebuild_draft, resolve_conflict
    from erp_backend.db import new_session
    from erp_backend.models import Company, FinancialDataset

    settings = get_settings()
    action = (
        "select_candidate" if args.select is not None
        else "enter_value" if args.value is not None
        else "override" if args.override
        else "revoke"
    )  # fmt: skip
    try:
        value = Decimal(args.value) if args.value is not None else None
    except InvalidOperation:
        print(f"error: invalid value {args.value!r}", file=sys.stderr)
        return 2
    with new_session() as db:
        try:
            dataset = db.get(FinancialDataset, uuid.UUID(args.id))
            if dataset is None:
                raise DatasetError(f"No dataset {args.id}")
            resolution = resolve_conflict(
                db, dataset.id, args.key, action, reason=args.reason,
                candidate_fact_id=args.select, value=value, source_reference=args.source,
            )  # fmt: skip
            print(f"recorded resolution {resolution.id}: {action} on {args.key}")
            if not args.no_rebuild:
                company = db.get(Company, dataset.company_id)
                assert company is not None
                result = rebuild_draft(
                    db, company, mappings_root=settings.mappings_root,
                    mapping_version=dataset.mapping_version,
                    reason=f"superseded: conflict resolution {resolution.id} applied",
                )  # fmt: skip
                d = result.dataset
                print(
                    f"new draft v{d.version_no} {d.id}: conflicts {d.source_summary['conflicts']}"
                )
            db.commit()
        except DatasetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


def _dataset_rebuild(ticker: str, reason: str) -> int:
    from erp_backend.config import get_settings
    from erp_backend.datasets import DatasetError, rebuild_draft
    from erp_backend.db import new_session

    settings = get_settings()
    with new_session() as db:
        company = _company_by_ticker(db, ticker)
        if company is None:
            print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
            return 1
        try:
            result = rebuild_draft(
                db, company, mappings_root=settings.mappings_root,
                mapping_version=settings.mapping_version, reason=reason,
            )  # fmt: skip
        except DatasetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        db.commit()
        d = result.dataset
        print(f"{company.legal_name}: draft v{d.version_no} {d.id}")
    return 0


def _classify(tickers: list[str]) -> int:
    import json

    from erp_backend.classifications import effective_classification, suggest_classification
    from erp_backend.db import new_session

    with new_session() as db:
        for ticker in tickers:
            company = _company_by_ticker(db, ticker)
            if company is None:
                print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
                return 1
            result = suggest_classification(db, company)
            s = result.suggestion
            print(
                f"{company.legal_name}: suggested {s.classification} — {s.rationale} "
                f"({'stored' if result.created else 'unchanged'}); "
                f"effective {effective_classification(company)}"
            )
            for e in s.evidence:
                print(f"    {json.dumps(e.as_dict(), sort_keys=True)}")
            for note in s.notes:
                print(f"    note: {note}")
        db.commit()
    return 0


def _classification_confirm(ticker: str, classification: str, reason: str) -> int:
    from erp_backend.classifications import ClassificationError, confirm_classification
    from erp_backend.db import new_session

    with new_session() as db:
        company = _company_by_ticker(db, ticker)
        if company is None:
            print(f"error: no company with ticker {ticker.upper()}", file=sys.stderr)
            return 1
        try:
            row = confirm_classification(db, company, classification, reason=reason)
        except ClassificationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        db.commit()
        print(f"{company.legal_name}: confirmed {row.classification} (record {row.id})")
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
    std = sub.add_parser(
        "sec-standardize-report",
        help="Report how stored SEC facts map onto standard line items (read-only).",
    )
    std.add_argument("--ticker", required=True)
    std.add_argument("--years", type=int, default=5)
    std.add_argument(
        "--policy", choices=["latest_reported", "first_reported"], default="latest_reported"
    )
    std.add_argument("--as-of", help="Only facts filed on or before this date (YYYY-MM-DD).")
    std.add_argument("--all", action="store_true", help="Show every mapped line item.")
    dsb = sub.add_parser(
        "dataset-build", help="Build a draft standardized dataset version from stored facts."
    )
    dsb.add_argument("--ticker", required=True)
    dsb.add_argument(
        "--policy", choices=["latest_reported", "first_reported"], default="latest_reported"
    )
    dsb.add_argument("--as-of", help="Facts cut-off (YYYY-MM-DD); default: latest filing.")
    dsl = sub.add_parser("dataset-list", help="List dataset versions of a company.")
    dsl.add_argument("--ticker", required=True)
    for name, text in (
        ("dataset-verify", "Re-serialize and rebuild a dataset; check byte-identity."),
        ("dataset-lock", "Lock a verified draft dataset (immutable afterwards)."),
        ("dataset-discard", "Discard a draft dataset (its values are removed)."),
    ):
        act = sub.add_parser(name, help=text)
        act.add_argument("--id", required=True)
        if name != "dataset-verify":
            act.add_argument("--reason", required=True)
    cl = sub.add_parser("conflict-list", help="List data conflicts of a dataset.")
    cl.add_argument("--id", required=True, help="dataset id")
    cl.add_argument("--all", action="store_true", help="Include auto-resolved immaterial ones.")
    cr = sub.add_parser(
        "conflict-resolve",
        help="Record an analyst decision on a conflict and rebuild the company's draft.",
    )
    cr.add_argument("--id", required=True, help="dataset id where the conflict was detected")
    cr.add_argument("--key", required=True, help="conflict key (see conflict-list)")
    how = cr.add_mutually_exclusive_group(required=True)
    how.add_argument("--select", type=int, help="candidate fact id to use")
    how.add_argument("--value", help="analyst value (requires --source)")
    how.add_argument("--override", action="store_true", help="keep the policy value")
    how.add_argument("--revoke", action="store_true", help="revoke the active resolution")
    cr.add_argument("--source", help="source reference for --value")
    cr.add_argument("--reason", required=True)
    cr.add_argument("--no-rebuild", action="store_true")
    rb = sub.add_parser("dataset-rebuild", help="Discard the open draft and build a new one.")
    rb.add_argument("--ticker", required=True)
    rb.add_argument("--reason", required=True)
    cls = sub.add_parser(
        "classify", help="Suggest a financial-business classification (SIC + XBRL evidence)."
    )
    cls.add_argument("--ticker", action="append", required=True)
    cc = sub.add_parser("classification-confirm", help="Confirm a company's classification.")
    cc.add_argument("--ticker", required=True)
    cc.add_argument(
        "--class",
        dest="classification",
        required=True,
        choices=["bank", "insurer", "specialty_lender", "reit", "asset_manager_broker",
                 "investment_holding", "non_financial"],
    )  # fmt: skip
    cc.add_argument("--reason", required=True)
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
    if args.command == "dataset-build":
        return _dataset_build(args.ticker, args.policy, args.as_of)
    if args.command == "dataset-list":
        return _dataset_list(args.ticker)
    if args.command in ("dataset-verify", "dataset-lock", "dataset-discard"):
        action = args.command.split("-", 1)[1]
        return _dataset_action(action, args.id, getattr(args, "reason", None))
    if args.command == "conflict-list":
        return _conflict_list(args.id, args.all)
    if args.command == "conflict-resolve":
        return _conflict_resolve(args)
    if args.command == "dataset-rebuild":
        return _dataset_rebuild(args.ticker, args.reason)
    if args.command == "classify":
        return _classify(args.ticker)
    if args.command == "classification-confirm":
        return _classification_confirm(args.ticker, args.classification, args.reason)
    if args.command == "sec-standardize-report":
        return _sec_standardize_report(args.ticker, args.years, args.policy, args.as_of, args.all)
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

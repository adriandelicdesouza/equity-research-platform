"""CLI commands for rates/FX and reference datasets (D58); registered by ``erp_backend.cli``.

Rates/FX: ``rates-parse-file`` (offline preview, no database writes), ``rates-fetch``
(gated: refuses while the provider is disabled), ``rates-ingest-archived`` (re-parse an
archived fetch, no network), ``rate-series-list``, ``rates-show``, ``fx-show``.

Reference datasets: ``reference-import-validate`` (reads only), ``reference-import``
(explicit, hash-confirmed, versioned), ``reference-dataset-list``, ``reference-dataset-show``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

RATE_PROVIDERS = ("us_treasury", "bank_of_canada")
COMMANDS = (
    "rates-parse-file",
    "rates-fetch",
    "rates-ingest-archived",
    "rate-series-list",
    "rates-show",
    "fx-show",
    "reference-import-validate",
    "reference-import",
    "reference-dataset-list",
    "reference-dataset-show",
)


def _err(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _read(path: str, filename: str | None) -> tuple[str, bytes] | None:
    if path == "-":
        if not filename:
            _err("--filename is required with --file -")
            return None
        return filename, sys.stdin.buffer.read()
    file = Path(path)
    if not file.is_file():
        _err(f"no such file {path}")
        return None
    return filename or file.name, file.read_bytes()


def _iso(text: str | None, name: str) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be YYYY-MM-DD") from exc


def _adapter(code: str) -> Any:
    from erp_backend.rates.bank_of_canada import BankOfCanada
    from erp_backend.rates.treasury import UsTreasury

    return UsTreasury() if code == "us_treasury" else BankOfCanada()


# --------------------------------------------------------------------------- rates / FX


def _rates_parse_file(args: argparse.Namespace) -> int:
    from erp_backend.rates.model import ParseError

    source = _read(args.file, args.filename)
    if source is None:
        return 1
    try:
        parsed = _adapter(args.provider).parse(source[1])
    except ParseError as exc:
        return _err(f"payload rejected: {exc}")
    if args.json:
        print(
            json.dumps(
                {
                    "summary": parsed.summary(),
                    "issues": [i.as_dict() for i in parsed.issues],
                    "rates": [
                        {
                            "series": o.series_code,
                            "date": o.obs_date.isoformat(),
                            "value": str(o.value),
                        }
                        for o in parsed.rates
                    ],
                    "fx": [
                        {
                            "base": o.base,
                            "quote": o.quote,
                            "date": o.obs_date.isoformat(),
                            "rate": str(o.rate),
                            "series": o.provider_series_id,
                        }
                        for o in parsed.fx
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        s = parsed.summary()
        print(
            f"{s['provider']}: {s['rate_observations']} rate and {s['fx_observations']} FX "
            f"observation(s); missing {s['missing']}; errors {s['errors']}; "
            f"warnings {s['warnings']}; series {', '.join(s['series'])}"
        )
        for issue in parsed.issues[:50]:
            day = issue.trade_date.isoformat() if issue.trade_date else "-"
            print(f"  {issue.level.value}:{issue.code} {day} {issue.message}")
        print("preview only: nothing was written")
    return 0


def _rates_fetch(args: argparse.Namespace) -> int:
    from erp_backend.config import get_settings
    from erp_backend.db import new_session
    from erp_backend.ingestion.http import ProviderDisabledError
    from erp_backend.rates import bank_of_canada, treasury
    from erp_backend.rates.store import RatesError, fetch_rates

    with new_session() as db:
        try:
            if args.provider == "us_treasury":
                if args.year is None:
                    return _err("--year is required for us_treasury")
                url, params = treasury.year_url(args.year)
                client = treasury.treasury_client(db)
            else:
                start, end = _iso(args.start, "--start"), _iso(args.end, "--end")
                if start is None or end is None:
                    return _err("--start and --end are required for bank_of_canada")
                series = args.series or list(bank_of_canada.registered_series())
                url, params = bank_of_canada.observations_url(series, start, end)
                client = bank_of_canada.boc_client(db)
        except ProviderDisabledError as exc:
            return _err(f"refused (no request made): {exc}")
        except (ValueError, argparse.ArgumentTypeError) as exc:
            return _err(str(exc))
        try:
            result = fetch_rates(
                db, _adapter(args.provider), client, url, params, get_settings().raw_root
            )
        except RatesError as exc:
            db.commit()  # keep the archived fetch record as evidence
            return _err(str(exc))
        finally:
            client.close()
        db.commit()
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


def _rates_ingest_archived(args: argparse.Namespace) -> int:
    from erp_backend.config import get_settings
    from erp_backend.db import new_session
    from erp_backend.ingestion.archive import ArchiveIntegrityError
    from erp_backend.rates.model import ParseError
    from erp_backend.rates.store import RatesError, ingest_archived

    try:
        fetch_id = uuid.UUID(args.fetch_id)
    except ValueError:
        return _err(f"invalid fetch id {args.fetch_id!r}")
    with new_session() as db:
        try:
            result = ingest_archived(db, _adapter(args.provider), fetch_id, get_settings().raw_root)
        except (RatesError, ParseError, ArchiveIntegrityError) as exc:
            db.rollback()
            return _err(f"refused (nothing written): {exc}")
        db.commit()
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


def _rate_series_list() -> int:
    from sqlalchemy import func, select

    from erp_backend.db import new_session
    from erp_backend.models import RateObservationRow, RateSeries

    with new_session() as db:
        counts = dict(
            db.execute(
                select(RateObservationRow.series_code, func.count()).group_by(
                    RateObservationRow.series_code
                )
            ).all()
        )
        for s in db.scalars(
            select(RateSeries).order_by(RateSeries.provider_code, RateSeries.tenor_months)
        ):
            print(
                f"{s.code:<14} {s.provider_code:<15} {s.provider_series_id:<20} {s.currency} "
                f"{s.rate_type:<21} {s.tenor:<5} {s.unit}  observations {counts.get(s.code, 0)}"
            )
    return 0


def _rates_show(args: argparse.Namespace) -> int:
    from erp_backend.db import new_session
    from erp_backend.models import ProviderFetch, RateSeries
    from erp_backend.rates.store import current_rates

    try:
        start, end = _iso(args.start, "--start"), _iso(args.end, "--end")
    except argparse.ArgumentTypeError as exc:
        return _err(str(exc))
    with new_session() as db:
        series = db.get(RateSeries, args.series)
        if series is None:
            return _err(f"unknown series {args.series!r} (see rate-series-list)")
        print(f"{series.code}: {series.description}; {series.quote_convention}")
        rows = current_rates(db, series.code, start, end)[-args.limit :]
        for r in rows:
            fetch = db.get(ProviderFetch, r.fetch_id)
            sha = fetch.content_sha256[:12] if fetch and fetch.content_sha256 else "-"
            print(f"  {r.obs_date}  {r.value} {series.unit}  fetch {r.fetch_id} sha256 {sha}…")
        if not rows:
            print("  no observations")
    return 0


def _fx_show(args: argparse.Namespace) -> int:
    from erp_backend.db import new_session
    from erp_backend.rates.store import current_fx

    try:
        start, end = _iso(args.start, "--start"), _iso(args.end, "--end")
    except argparse.ArgumentTypeError as exc:
        return _err(str(exc))
    base, quote = args.base.upper(), args.quote.upper()
    with new_session() as db:
        rows = current_fx(db, base, quote, start, end)[-args.limit :]
        print(f"{quote} per 1 {base} (stored direction only; never inverted)")
        for r in rows:
            print(
                f"  {r.obs_date}  {r.rate}  {r.provider_code}:{r.provider_series_id}  "
                f"fetch {r.fetch_id}"
            )
        if not rows:
            print("  no observations in this direction")
    return 0


# --------------------------------------------------------------------------- reference data


def _print_reference_report(report: Any, show_all: bool) -> None:
    from erp_backend.reference.damodaran import Status

    print(f"template  {report.template}")
    print(f"lineage   {report.lineage.key}")
    print(f"file      {report.filename} ({report.bytes} bytes)")
    print(f"sha256    {report.file_sha256}")
    for error in report.file_errors:
        print(f"FILE ERROR  {error}")
    s = report.summary
    print(
        f"records   {s['records']}: accepted {s['accepted']}, duplicate {s['duplicate']}, "
        f"conflicting {s['conflicting']}, unmapped {s['unmapped']}, rejected {s['rejected']}; "
        f"storable {s['storable']}; warnings {s['warnings']}"
    )
    current = report.current_version
    print(
        "current   "
        + (
            f"version {current['version_no']} ({current['record_count']} records)"
            if current
            else "none"
        )
    )
    if report.removed_keys:
        print(f"removed   {len(report.removed_keys)}: {', '.join(report.removed_keys[:20])}")
    print(f"outcome   {report.outcome}")
    shown, limit = 0, None if show_all else 50
    for r in report.records:
        if not r.issues and r.status is Status.ACCEPTED:
            continue
        for i in r.issues:
            if limit is not None and shown >= limit:
                print("... more issues; use --all or --json")
                return
            print(
                f"  {r.locator:>10}  {r.status.value:<11} {r.record_key or '-':<24} "
                f"{i.level}:{i.code}  {i.message}"
            )
            shown += 1


def _reference_common(args: argparse.Namespace) -> dict[str, Any] | None:
    source = _read(args.file, args.filename)
    if source is None:
        return None
    try:
        edition = _iso(args.edition_date, "--edition-date")
    except argparse.ArgumentTypeError as exc:
        _err(str(exc))
        return None
    return {
        "filename": source[0],
        "data": source[1],
        "template": args.template,
        "edition_date": edition,
        "region": args.region,
        "variant": args.variant,
    }


def _reference_validate(args: argparse.Namespace) -> int:
    from erp_backend.db import new_session
    from erp_backend.reference.damodaran import ImportRefusedError, validate

    common = _reference_common(args)
    if common is None:
        return 1
    with new_session() as db:
        try:
            report = validate(db, **common)
        except ImportRefusedError as exc:
            return _err(str(exc))
        finally:
            db.rollback()  # validation never writes; make that explicit
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        _print_reference_report(report, args.all)
        if report.outcome in ("new_dataset", "new_version_requires_supersede"):
            extra = " --supersede --reason ..." if report.current_version else ""
            print(f"to ingest: reference-import ... --expect-sha256 {report.file_sha256}{extra}")
    return 1 if report.file_errors else 0


def _reference_import(args: argparse.Namespace) -> int:
    from erp_backend.config import get_settings
    from erp_backend.db import new_session
    from erp_backend.reference.damodaran import ImportRefusedError, ingest

    common = _reference_common(args)
    if common is None:
        return 1
    with new_session() as db:
        try:
            result = ingest(
                db,
                **common,
                source_name=args.source,
                source_reference=args.source_reference,
                expected_sha256=args.expect_sha256,
                raw_root=get_settings().raw_root,
                allow_partial=args.allow_partial,
                supersede=args.supersede,
                reason=args.reason,
            )
        except ImportRefusedError as exc:
            db.rollback()
            print(f"refused (nothing written): {exc}", file=sys.stderr)
            return 1
        db.commit()
    if result.status == "identical_to_current":
        print(f"nothing stored: identical to version {result.version_no} ({result.dataset_id})")
        return 0
    print(
        f"dataset {result.dataset_id} version {result.version_no} ({result.status}): "
        f"{result.stored_records} record(s); import {result.import_id}"
    )
    return 0


def _reference_list(kind: str | None) -> int:
    from erp_backend.db import new_session
    from erp_backend.reference.damodaran import list_datasets

    with new_session() as db:
        rows = list_datasets(db, kind)
        for d in rows:
            print(
                f"{d.id}  {d.lineage_key:<44} v{d.version_no:<3} {d.record_count:>4} records  "
                f"{d.created_at:%Y-%m-%d %H:%M}"
                + (f"  supersedes {d.supersedes_id}" if d.supersedes_id else "")
            )
        if not rows:
            print("no reference datasets")
    return 0


def _reference_show(dataset_id: str, show_values: bool) -> int:
    from sqlalchemy import select

    from erp_backend.db import new_session
    from erp_backend.models import ReferenceDataset, ReferenceValue, SourceImport

    try:
        ident = uuid.UUID(dataset_id)
    except ValueError:
        return _err(f"invalid dataset id {dataset_id!r}")
    with new_session() as db:
        d = db.get(ReferenceDataset, ident)
        if d is None:
            return _err(f"no reference dataset {dataset_id}")
        imp = db.get(SourceImport, d.import_id)
        assert imp is not None
        print(f"dataset   {d.id} ({d.template_version})")
        print(f"lineage   {d.lineage_key}  version {d.version_no}")
        if d.supersedes_id:
            print(f"supersedes {d.supersedes_id}: {d.reason}")
        print(f"records   {d.record_count}  content sha256 {d.content_sha256}")
        print(f"file      {imp.original_filename} ({imp.bytes} bytes) sha256 {imp.file_sha256}")
        print(f"archive   {imp.stored_path}")
        print(
            f"source    {imp.source_name}"
            + (f" — {imp.source_reference}" if imp.source_reference else "")
        )
        print(f"imported  {imp.imported_at:%Y-%m-%d %H:%M:%S %Z} ({imp.status})")
        for item in imp.skipped:
            issues = "; ".join(f"{i['code']}: {i['message']}" for i in item["issues"])
            print(f"  skipped {item['locator']} {item['status']}: {issues}")
        if show_values:
            for v in db.scalars(
                select(ReferenceValue)
                .where(ReferenceValue.dataset_id == d.id)
                .order_by(ReferenceValue.record_key, ReferenceValue.field)
            ):
                shown = v.value if v.value is not None else v.value_text
                print(f"  {v.record_key:<32} {v.field:<30} {shown} {v.unit}  [{v.locator}]")
    return 0


# --------------------------------------------------------------------------- registration


def register(sub: Any) -> None:
    p = sub.add_parser("rates-parse-file", help="Parse a rate/FX payload file (no writes).")
    p.add_argument("--provider", required=True, choices=RATE_PROVIDERS)
    p.add_argument("--file", required=True, help="path, or - to read stdin")
    p.add_argument("--filename")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("rates-fetch", help="Fetch rates/FX (refused while the provider is off).")
    p.add_argument("--provider", required=True, choices=RATE_PROVIDERS)
    p.add_argument("--year", type=int, help="us_treasury: calendar year")
    p.add_argument("--start", help="bank_of_canada: YYYY-MM-DD")
    p.add_argument("--end", help="bank_of_canada: YYYY-MM-DD")
    p.add_argument("--series", nargs="*", help="bank_of_canada series (default: registered)")
    p = sub.add_parser("rates-ingest-archived", help="Re-parse an archived rate/FX fetch.")
    p.add_argument("--provider", required=True, choices=RATE_PROVIDERS)
    p.add_argument("--fetch-id", required=True)
    sub.add_parser("rate-series-list", help="List registered rate series.")
    p = sub.add_parser("rates-show", help="Show stored observations of one rate series.")
    p.add_argument("--series", required=True)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("fx-show", help="Show stored FX rates (quote per 1 base).")
    p.add_argument("--base", required=True)
    p.add_argument("--quote", required=True)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int, default=30)

    from erp_backend.reference.damodaran import REGIONS, TEMPLATES, VARIANTS

    for name, text in (
        ("reference-import-validate", "Validate a reference-data file (no writes)."),
        ("reference-import", "Ingest a validated reference-data file as a dataset version."),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--template", required=True, choices=sorted(TEMPLATES))
        p.add_argument("--file", required=True, help="path, or - to read stdin")
        p.add_argument("--filename", help="original file name (required with --file -)")
        p.add_argument("--edition-date", required=True, help="Damodaran edition, YYYY-MM-DD")
        p.add_argument("--region", choices=REGIONS, help="industry betas only")
        p.add_argument("--variant", choices=VARIANTS, help="rating spreads only")
        if name == "reference-import-validate":
            p.add_argument("--json", action="store_true")
            p.add_argument("--all", action="store_true")
        else:
            p.add_argument("--source", required=True, help="where the data came from")
            p.add_argument("--source-reference", help="URL, file name or retrieval note")
            p.add_argument("--expect-sha256", required=True)
            p.add_argument("--allow-partial", action="store_true")
            p.add_argument(
                "--supersede",
                action="store_true",
                help="create a new version of an existing lineage",
            )
            p.add_argument("--reason", help="required with --supersede (>= 10 characters)")
    p = sub.add_parser("reference-dataset-list", help="List reference dataset versions.")
    p.add_argument(
        "--kind", choices=["implied_erp", "country_risk", "industry_betas", "rating_spreads"]
    )
    p = sub.add_parser("reference-dataset-show", help="Show one reference dataset version.")
    p.add_argument("id")
    p.add_argument("--values", action="store_true")


def dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == "rates-parse-file":
        return _rates_parse_file(args)
    if command == "rates-fetch":
        return _rates_fetch(args)
    if command == "rates-ingest-archived":
        return _rates_ingest_archived(args)
    if command == "rate-series-list":
        return _rate_series_list()
    if command == "rates-show":
        return _rates_show(args)
    if command == "fx-show":
        return _fx_show(args)
    if command == "reference-import-validate":
        return _reference_validate(args)
    if command == "reference-import":
        return _reference_import(args)
    if command == "reference-dataset-list":
        return _reference_list(args.kind)
    return _reference_show(args.id, args.values)

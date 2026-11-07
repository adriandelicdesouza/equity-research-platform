"""Interest-rate series, rate observations and FX rates (Blueprint §3.3, §4.2, §7.5.1; D58).

* ``market.rate_series`` — the registry of rate series (identity: provider series, rate
  type, currency, tenor, unit and quotation convention). Seeded here from the code-level
  registry ``erp_backend.rates.model.RATE_SERIES``; read-only for the application role and
  append-only for every role (a changed definition is a new series code, never an edit).
* ``market.rate_observations`` — one published value of one series on one date, in
  percentage points, provenanced by the provider fetch that delivered it. A composite FK
  ties ``(series_code, provider_code)`` to the series, so a series can only be observed
  from its own provider.
* ``market.fx_rates`` — one published exchange rate: ``rate`` units of ``quote`` per one
  ``base`` (direction explicit, never inverted), with the provider's series id and fetch.

Observation tables follow D54: append-only (trigger), exact repeats are idempotent through
a unique index over every observed field, a revised value is a *new* row (readers take the
latest per date) — never an overwrite.

Downgrade is possible only while no observation exists (it would lose provenance).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
TABLES = ("rate_series", "rate_observations", "fx_rates")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _series_rows() -> list[dict[str, object]]:
    # Frozen copy of erp_backend.rates.model.RATE_SERIES at revision 0014 (a test checks
    # that the code registry and the seeded rows agree).
    ust = (
        "Par yield (constant maturity), bond-equivalent basis, semi-annual compounding; "
        "percent per annum as published by the US Treasury"
    )
    goc = (
        "Benchmark bond yield, Government of Canada, as published by the Bank of Canada "
        "(Valet); percent per annum"
    )
    rows: list[dict[str, object]] = []
    for header, tenor, months in (
        ("1 Mo", "1M", 1),
        ("2 Mo", "2M", 2),
        ("3 Mo", "3M", 3),
        ("4 Mo", "4M", 4),
        ("6 Mo", "6M", 6),
        ("1 Yr", "1Y", 12),
        ("2 Yr", "2Y", 24),
        ("3 Yr", "3Y", 36),
        ("5 Yr", "5Y", 60),
        ("7 Yr", "7Y", 84),
        ("10 Yr", "10Y", 120),
        ("20 Yr", "20Y", 240),
        ("30 Yr", "30Y", 360),
    ):
        rows.append(
            {
                "code": f"UST_PAR_{tenor}",
                "provider_code": "us_treasury",
                "provider_series_id": header,
                "currency": "USD",
                "rate_type": "par_yield",
                "tenor": tenor,
                "tenor_months": months,
                "unit": "percent",
                "quote_convention": ust,
                "description": f"US Treasury par yield curve rate, {tenor}",
            }
        )
    for series_id, tenor, months, label in (
        ("BD.CDN.2YR.DQ.YLD", "2Y", 24, "2 year"),
        ("BD.CDN.3YR.DQ.YLD", "3Y", 36, "3 year"),
        ("BD.CDN.5YR.DQ.YLD", "5Y", 60, "5 year"),
        ("BD.CDN.7YR.DQ.YLD", "7Y", 84, "7 year"),
        ("BD.CDN.10YR.DQ.YLD", "10Y", 120, "10 year"),
        ("BD.CDN.LONG.DQ.YLD", "LONG", None, "long-term (no fixed maturity)"),
    ):
        rows.append(
            {
                "code": f"GOC_BMK_{tenor}",
                "provider_code": "bank_of_canada",
                "provider_series_id": series_id,
                "currency": "CAD",
                "rate_type": "benchmark_bond_yield",
                "tenor": tenor,
                "tenor_months": months,
                "unit": "percent",
                "quote_convention": goc,
                "description": f"Government of Canada benchmark bond yield, {label}",
            }
        )
    return rows


def _append_only(schema_table: str, name: str) -> None:
    op.execute(
        f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {schema_table} "
        "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {schema_table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "rate_series",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("provider_series_id", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_type", sa.String(32), nullable=False),
        sa.Column("tenor", sa.String(8), nullable=False),
        sa.Column("tenor_months", sa.Integer()),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quote_convention", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        sa.CheckConstraint("rate_type IN ('par_yield', 'benchmark_bond_yield')", name="rate_type"),
        sa.CheckConstraint("unit = 'percent'", name="unit"),
        sa.CheckConstraint("tenor_months IS NULL OR tenor_months > 0", name="tenor_months"),
        sa.UniqueConstraint("provider_code", "provider_series_id", name="uq_rate_series_provider"),
        sa.UniqueConstraint("code", "provider_code", name="uq_rate_series_code_provider"),
        schema="market",
    )
    op.bulk_insert(
        sa.table(
            "rate_series",
            *(
                sa.column(c)
                for c in (
                    "code",
                    "provider_code",
                    "provider_series_id",
                    "currency",
                    "rate_type",
                    "tenor",
                    "tenor_months",
                    "unit",
                    "quote_convention",
                    "description",
                )
            ),
            schema="market",
        ),
        _series_rows(),
    )
    op.create_table(
        "rate_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("series_code", sa.String(32), nullable=False),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("obs_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["series_code", "provider_code"],
            ["market.rate_series.code", "market.rate_series.provider_code"],
            name="fk_rate_observations_series",
        ),
        sa.CheckConstraint("value BETWEEN -10 AND 50", name="value_range"),
        schema="market",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_rate_observations ON market.rate_observations "
        "(series_code, provider_code, obs_date, value)"
    )
    op.create_index(
        "ix_market_rate_observations_lookup",
        "rate_observations",
        ["series_code", "obs_date"],
        schema="market",
    )
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("obs_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("provider_series_id", sa.String(64), nullable=False),
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rate > 0", name="rate_positive"),
        sa.CheckConstraint(
            "base ~ '^[A-Z]{3}$' AND quote ~ '^[A-Z]{3}$' AND base <> quote", name="pair"
        ),
        schema="market",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_fx_rates ON market.fx_rates "
        "(provider_code, provider_series_id, base, quote, obs_date, rate)"
    )
    op.create_index(
        "ix_market_fx_rates_lookup", "fx_rates", ["base", "quote", "obs_date"], schema="market"
    )
    for table in TABLES:
        _append_only(f"market.{table}", table)
    op.execute(f"GRANT SELECT ON market.rate_series TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON market.rate_observations, market.fx_rates TO {app}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM market.rate_observations)
               OR EXISTS (SELECT 1 FROM market.fx_rates) THEN
                RAISE EXCEPTION 'rate or FX observations exist; downgrade would lose provenance';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TABLE market.fx_rates, market.rate_observations, market.rate_series")

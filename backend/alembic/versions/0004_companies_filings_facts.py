"""Companies, securities, identifiers; SEC ticker map, filings and reported facts.

Blueprint §3.3. Source records (ticker snapshots, filings, reported facts) are append-only:
INSERT/SELECT for the application role plus a trigger rejecting UPDATE/DELETE/TRUNCATE.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _append_only(table: str) -> None:
    name = table.split(".")[1]
    op.execute(
        f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA market")
    op.create_table(
        "companies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("common_name", sa.Text()),
        sa.Column("country", sa.String(2)),
        sa.Column("state_of_incorporation", sa.String(8)),
        sa.Column("sic_code", sa.String(8)),
        sa.Column("sic_description", sa.Text()),
        sa.Column("fiscal_year_end_mmdd", sa.String(4)),
        sa.Column("reporting_currency", sa.String(3)),
        sa.Column("accounting_standard", sa.String(16)),
        sa.Column(
            "accounting_standard_inferred", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("financial_classification", sa.String(32)),
        sa.Column("interim_coverage", sa.String(8)),
        sa.Column("interim_coverage_details", postgresql.JSONB()),
        sa.Column("interim_coverage_as_of", TS),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "accounting_standard IS NULL OR accounting_standard IN ('US_GAAP','IFRS','OTHER')",
            name="ck_companies_standard",
        ),
        sa.CheckConstraint(
            "interim_coverage IS NULL OR interim_coverage IN ('none','partial','full')",
            name="ck_companies_interim",
        ),
        schema="market",
    )
    op.create_table(
        "securities",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("exchange_name", sa.String(64), nullable=False),
        sa.Column("exchange_mic", sa.String(4)),
        sa.Column("trading_currency", sa.String(3)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_symbols", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "ticker", "exchange_name", name="uq_securities_listing"),
        schema="market",
    )
    op.create_index("ix_market_securities_ticker", "securities", ["ticker"], schema="market")
    op.create_table(
        "company_identifiers",
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("scheme", sa.String(16), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("scheme", "value", name="pk_company_identifiers"),
        schema="market",
    )
    op.create_index(
        "ix_market_company_identifiers_company",
        "company_identifiers",
        ["company_id"],
        schema="market",
    )

    op.create_table(
        "sec_tickers",
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("exchange", sa.String(64)),
        sa.PrimaryKeyConstraint("fetch_id", "ticker", "cik", name="pk_sec_tickers"),
        schema="source",
    )
    op.create_index("ix_source_sec_tickers_ticker", "sec_tickers", ["ticker"], schema="source")
    op.create_table(
        "filings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("accession_no", sa.String(20), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("period_of_report", sa.Date()),
        sa.Column("primary_document", sa.Text()),
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        sa.UniqueConstraint("accession_no", name="uq_filings_accession_no"),
        schema="source",
    )
    op.create_index(
        "ix_source_filings_company", "filings", ["company_id", "filed_date"], schema="source"
    )
    op.create_table(
        "reported_facts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("taxonomy", sa.String(32), nullable=False),
        sa.Column("concept", sa.String(256), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("fiscal_period", sa.String(4)),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("accession_no", sa.String(20), nullable=False),
        sa.Column("frame", sa.String(16)),
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        schema="source",
    )
    # Exact duplicates collapse; differing values within one filing are both kept so the
    # selection rule can flag them as ambiguous instead of silently choosing (D11).
    op.execute(
        "CREATE UNIQUE INDEX uq_reported_facts_key ON source.reported_facts "
        "(company_id, taxonomy, concept, unit, period_start, period_end, accession_no, value) "
        "NULLS NOT DISTINCT"
    )
    op.create_index(
        "ix_source_reported_facts_lookup",
        "reported_facts",
        ["company_id", "taxonomy", "concept", "period_end"],
        schema="source",
    )
    for table in ("source.sec_tickers", "source.filings", "source.reported_facts"):
        _append_only(table)

    op.execute(f"GRANT USAGE ON SCHEMA market TO {app}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON market.companies, market.securities, "
        f"market.company_identifiers TO {app}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON source.sec_tickers, source.filings, "
        f"source.reported_facts TO {app}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE source.reported_facts, source.filings, source.sec_tickers")
    op.execute("DROP SCHEMA market CASCADE")

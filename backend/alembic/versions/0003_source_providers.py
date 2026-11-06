"""Source schema: provider registry and append-only fetch log (Blueprint §3.3, §4.1).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PROVIDERS = [
    # code, name, kind, priority, rate limit (req/s), markets, categories
    (
        "sec_edgar",
        "SEC EDGAR (XBRL, submissions)",
        "official",
        10,
        "5",
        ["US", "CA"],
        ["profile", "fundamentals"],
    ),
    (
        "yahoo_chart",
        "Yahoo Finance chart (unofficial)",
        "unofficial",
        50,
        "1",
        ["US", "CA"],
        ["prices", "corporate_actions"],
    ),
    ("us_treasury", "US Treasury par yield curve", "official", 10, "1", ["US"], ["rates"]),
    ("bank_of_canada", "Bank of Canada Valet", "official", 10, "1", ["CA"], ["rates", "fx"]),
    ("fred", "FRED (no key)", "official", 20, "1", ["US"], ["rates"]),
    (
        "damodaran",
        "Damodaran reference datasets",
        "reference",
        10,
        "1",
        ["GLOBAL"],
        ["erp", "crp", "betas", "spreads"],
    ),
    (
        "manual_import",
        "Excel/CSV/manual import",
        "manual",
        100,
        "0",
        ["GLOBAL"],
        ["fundamentals", "prices"],
    ),
]


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA source")
    op.create_table(
        "providers",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("rate_limit_per_second", sa.Numeric(), nullable=False),
        sa.Column("markets", postgresql.JSONB(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "kind IN ('official','unofficial','reference','manual')", name="ck_providers_kind"
        ),
        schema="source",
    )
    op.create_table(
        "provider_fetches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("http_status", sa.Integer()),
        sa.Column(
            "retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("content_type", sa.String(128)),
        sa.Column("payload_path", sa.Text()),
        sa.Column("bytes", sa.BigInteger()),
        sa.Column("stored_bytes", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.Column("request_id", sa.String(64)),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_provider_fetches_sha256",
        ),
        schema="source",
    )
    op.create_index(
        "ix_source_provider_fetches_sha", "provider_fetches", ["content_sha256"], schema="source"
    )
    op.create_index(
        "ix_source_provider_fetches_provider_time",
        "provider_fetches",
        ["provider_code", "retrieved_at"],
        schema="source",
    )
    op.execute(
        """
        CREATE FUNCTION source.reject_modification() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '%.% is append-only (% rejected)', TG_TABLE_SCHEMA, TG_TABLE_NAME,
                TG_OP USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER provider_fetches_append_only BEFORE UPDATE OR DELETE "
        "ON source.provider_fetches FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER provider_fetches_no_truncate BEFORE TRUNCATE ON source.provider_fetches "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )
    providers = sa.table(
        "providers",
        sa.column("code"),
        sa.column("name"),
        sa.column("kind"),
        sa.column("priority"),
        sa.column("rate_limit_per_second", sa.Numeric),
        sa.column("markets", postgresql.JSONB),
        sa.column("categories", postgresql.JSONB),
        schema="source",
    )
    op.bulk_insert(
        providers,
        [
            {
                "code": c,
                "name": n,
                "kind": k,
                "priority": p,
                "rate_limit_per_second": r,
                "markets": m,
                "categories": cat,
            }
            for c, n, k, p, r, m, cat in PROVIDERS
        ],
    )
    op.execute(f"GRANT USAGE ON SCHEMA source TO {app}")
    op.execute(f"GRANT SELECT ON source.providers TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON source.provider_fetches TO {app}")


def downgrade() -> None:
    op.execute("DROP SCHEMA source CASCADE")

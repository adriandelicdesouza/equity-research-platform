"""Market observations: prices, corporate actions, provider listing snapshots (§3.3, §4.5; D54).

All three tables are append-only (INSERT/SELECT for the application role; a trigger
rejects UPDATE/DELETE/TRUNCATE for every role) and keyed for idempotency: an exact repeat
of an observation is not inserted again (unique index over every observed field, NULLS NOT
DISTINCT), while a *changed* value (e.g. a provider revising adjusted closes after a
dividend) is kept as a new observation rather than overwriting the old one. Each row
records the fetch that first delivered it (``source.provider_fetches``, archived payload).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
TABLES = ("price_observations", "corporate_actions", "provider_listings")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _common() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("security_id", UUID, sa.ForeignKey("market.securities.id"), nullable=False),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("fetch_id", UUID, sa.ForeignKey("source.provider_fetches.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "price_observations",
        *_common(),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric()),
        sa.Column("high", sa.Numeric()),
        sa.Column("low", sa.Numeric()),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("adj_close", sa.Numeric()),
        sa.Column("volume", sa.BigInteger()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("close_basis", sa.String(16), nullable=False),
        sa.Column("adj_close_basis", sa.String(32), nullable=False),
        sa.CheckConstraint("close > 0 AND (adj_close IS NULL OR adj_close > 0)", name="prices"),
        sa.CheckConstraint("high IS NULL OR low IS NULL OR high >= low", name="range"),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="volume"),
        sa.CheckConstraint(
            "close_basis IN ('raw', 'split_adjusted', 'unverified')", name="close_basis"
        ),
        sa.CheckConstraint(
            "adj_close_basis IN ('split_and_dividend_adjusted', 'unverified')",
            name="adj_close_basis",
        ),
        schema="market",
    )
    op.create_table(
        "corporate_actions",
        *_common(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio_numerator", sa.Numeric()),
        sa.Column("ratio_denominator", sa.Numeric()),
        sa.Column("amount", sa.Numeric()),
        sa.Column("currency", sa.String(3)),
        sa.Column("currency_source", sa.String(32)),
        sa.CheckConstraint("kind IN ('split', 'cash_dividend')", name="kind"),
        sa.CheckConstraint(
            "kind <> 'split' OR (ratio_numerator > 0 AND ratio_denominator > 0 "
            "AND amount IS NULL AND currency IS NULL)",
            name="split_fields",
        ),
        sa.CheckConstraint(
            "kind <> 'cash_dividend' OR (amount > 0 AND currency IS NOT NULL "
            "AND ratio_numerator IS NULL AND ratio_denominator IS NULL)",
            name="dividend_fields",
        ),
        schema="market",
    )
    op.create_table(
        "provider_listings",
        *_common(),
        sa.Column("provider_symbol", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("exchange_code", sa.String(32)),
        sa.Column("exchange_name", sa.String(128)),
        sa.Column("exchange_timezone", sa.String(64)),
        sa.Column("instrument_type", sa.String(32)),
        sa.Column("first_trade_date", sa.Date()),
        sa.Column("quote_price", sa.Numeric()),
        sa.Column("quote_time", TS),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        schema="market",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_price_observations ON market.price_observations (security_id, "
        "provider_code, trade_date, open, high, low, close, adj_close, volume, currency, "
        "close_basis, adj_close_basis) NULLS NOT DISTINCT"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_corporate_actions ON market.corporate_actions (security_id, "
        "provider_code, kind, ex_date, ratio_numerator, ratio_denominator, amount, currency) "
        "NULLS NOT DISTINCT"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_provider_listings ON market.provider_listings (security_id, "
        "provider_code, provider_symbol, currency, exchange_code, exchange_name, "
        "exchange_timezone, instrument_type, first_trade_date, quote_price, quote_time) "
        "NULLS NOT DISTINCT"
    )
    op.create_index(
        "ix_market_price_observations_lookup",
        "price_observations",
        ["security_id", "provider_code", "trade_date"],
        schema="market",
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON market.{table} "
            "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON market.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
        )
    op.execute(f"GRANT SELECT, INSERT ON {', '.join('market.' + t for t in TABLES)} TO {app}")


def downgrade() -> None:
    op.execute(f"DROP TABLE {', '.join('market.' + t for t in TABLES)}")

"""Manual listing registration: instrument attributes and append-only registration provenance.

Blueprint §4.3 step 3 / D15: a listing the SEC path does not create (e.g. a TSX listing) is
registered by an analyst. A row of ``market.securities`` remains one *exchange listing*
(issuer = ``market.companies``); this migration adds what identifies the instrument it
represents, all nullable because SEC-created rows never recorded them:

* ``security_type`` — ``common_equity`` / ``preferred_equity`` / ``depositary_receipt`` /
  ``fund_unit`` / ``other``;
* ``share_class`` — e.g. ``B`` for RCI.B;
* ``isin`` — ISO 6166 format (check digit validated in the application).

``market.listing_registrations`` is append-only: who registered which listing, under which
issuer (and whether the issuer was created by the registration), on what source, why, and
the normalized proposal and warnings that were accepted.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
SECURITY_TYPES = ("common_equity", "preferred_equity", "depositary_receipt", "fund_unit", "other")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.add_column("securities", sa.Column("security_type", sa.String(24)), schema="market")
    op.add_column("securities", sa.Column("share_class", sa.String(16)), schema="market")
    op.add_column("securities", sa.Column("isin", sa.String(12)), schema="market")
    types = ", ".join(f"'{t}'" for t in SECURITY_TYPES)
    op.create_check_constraint(
        "security_type",
        "securities",
        f"security_type IS NULL OR security_type IN ({types})",
        schema="market",
    )
    op.create_check_constraint(
        "isin", "securities", "isin IS NULL OR isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'", schema="market"
    )
    op.create_index("ix_market_securities_isin", "securities", ["isin"], schema="market")
    op.create_table(
        "listing_registrations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("security_id", UUID, sa.ForeignKey("market.securities.id"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("company_created", sa.Boolean(), nullable=False),
        sa.Column("proposal", postgresql.JSONB(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("registered_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("registered_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(btrim(source_name)) > 0", name="source_name"),
        sa.CheckConstraint("length(btrim(reason)) >= 10", name="reason"),
        schema="market",
    )
    op.create_index(
        "ix_market_listing_registrations_security",
        "listing_registrations",
        ["security_id"],
        schema="market",
    )
    op.execute(
        "CREATE TRIGGER listing_registrations_append_only BEFORE UPDATE OR DELETE ON "
        "market.listing_registrations FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER listing_registrations_no_truncate BEFORE TRUNCATE ON "
        "market.listing_registrations FOR EACH STATEMENT "
        "EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(f"GRANT SELECT, INSERT ON market.listing_registrations TO {app}")


def downgrade() -> None:
    op.drop_table("listing_registrations", schema="market")
    op.drop_index("ix_market_securities_isin", table_name="securities", schema="market")
    op.drop_constraint(op.f("ck_securities_isin"), "securities", schema="market", type_="check")
    op.drop_constraint(
        op.f("ck_securities_security_type"), "securities", schema="market", type_="check"
    )
    for column in ("isin", "share_class", "security_type"):
        op.drop_column("securities", column, schema="market")

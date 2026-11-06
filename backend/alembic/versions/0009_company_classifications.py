"""Financial-business classification history (Blueprint §7.6.1; D24, D53).

``market.company_classifications`` is append-only: every classifier suggestion (with its
SIC and XBRL evidence) and every analyst confirmation (with a required reason). The
effective class is the latest confirmation, mirrored in
``market.companies.financial_classification``; until one exists it is ``needs_review``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

CLASSES = (
    "bank", "insurer", "specialty_lender", "reit", "asset_manager_broker",
    "investment_holding", "non_financial", "needs_review",
)  # fmt: skip


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    listed = ", ".join(repr(c) for c in CLASSES)
    op.create_table(
        "company_classifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("market.companies.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("classifier_version", sa.String(16)),
        sa.Column("reason", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("auth.users.id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('suggested', 'confirmed')", name="kind"),
        sa.CheckConstraint(f"classification IN ({listed})", name="classification"),
        sa.CheckConstraint(
            "kind <> 'confirmed' OR (classification <> 'needs_review' "
            "AND char_length(btrim(coalesce(reason, ''))) >= 10)",
            name="confirmed_needs_reason",
        ),
        sa.CheckConstraint(
            "kind <> 'suggested' OR classifier_version IS NOT NULL", name="suggested_version"
        ),
        schema="market",
    )
    op.create_index(
        "ix_market_company_classifications_company",
        "company_classifications",
        ["company_id", "kind", "id"],
        schema="market",
    )
    op.execute(
        "CREATE TRIGGER company_classifications_append_only BEFORE UPDATE OR DELETE "
        "ON market.company_classifications FOR EACH ROW "
        "EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER company_classifications_no_truncate BEFORE TRUNCATE "
        "ON market.company_classifications FOR EACH STATEMENT "
        "EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(f"GRANT SELECT, INSERT ON market.company_classifications TO {app}")


def downgrade() -> None:
    op.execute("DROP TABLE market.company_classifications")

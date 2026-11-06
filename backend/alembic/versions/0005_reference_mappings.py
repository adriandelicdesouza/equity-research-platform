"""Reference schema: standard line items and versioned concept mappings (Blueprint §3.3).

Loads mapping version v1 from the repository YAML (``mappings/v1/``) and refuses to run if
the files differ from the fingerprint pinned here: a loaded version is immutable, so a
change must become v2 with its own migration. The tables are read-only for the
application role and reject UPDATE/DELETE/TRUNCATE for everyone.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from erp_backend.standardization.mapping import load
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

VERSION = "v1"
V1_SHA256 = "40281532363d2a55b00c208345adc6d4f52b588d839e28bc0ce340c71b429a7e"


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    mapping = load(get_settings().mappings_root, VERSION)
    if mapping.sha256 != V1_SHA256:
        raise RuntimeError(
            f"mappings/{VERSION} differs from the pinned fingerprint ({mapping.sha256}); "
            "loaded mapping versions are immutable — put changes in a new version."
        )
    app = _app_role()
    op.execute("CREATE SCHEMA ref")
    op.create_table(
        "mapping_versions",
        sa.Column("version", sa.String(16), primary_key=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="ref",
    )
    op.create_table(
        "standard_line_items",
        sa.Column(
            "mapping_version",
            sa.String(16),
            sa.ForeignKey("ref.mapping_versions.version"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("statement", sa.String(2), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("parent_code", sa.String(64)),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("sign_convention", sa.String(16), nullable=False),
        sa.Column("is_subtotal", sa.Boolean(), nullable=False),
        sa.Column("subtotal_formula", postgresql.JSONB()),
        sa.Column("derived_only", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("mapping_version", "code", name="pk_standard_line_items"),
        sa.CheckConstraint("statement IN ('IS','BS','CF')", name="ck_line_items_statement"),
        sa.CheckConstraint("period_type IN ('duration','instant')", name="ck_line_items_period"),
        sa.CheckConstraint(
            "value_type IN ('flow','stock','per_share','count')", name="ck_line_items_value_type"
        ),
        sa.CheckConstraint(
            "sign_convention IN ('natural','expense','cash_flow')", name="ck_line_items_sign"
        ),
        schema="ref",
    )
    op.create_table(
        "concept_mappings",
        sa.Column("mapping_version", sa.String(16), nullable=False),
        sa.Column("taxonomy", sa.String(32), nullable=False),
        sa.Column("line_item_code", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("concept", sa.String(256), nullable=False),
        sa.Column("sign_multiplier", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "mapping_version", "taxonomy", "line_item_code", "priority", name="pk_concept_mappings"
        ),
        sa.UniqueConstraint(
            "mapping_version",
            "taxonomy",
            "line_item_code",
            "concept",
            name="uq_concept_mappings_concept",
        ),
        sa.ForeignKeyConstraint(
            ["mapping_version", "line_item_code"],
            ["ref.standard_line_items.mapping_version", "ref.standard_line_items.code"],
            name="fk_concept_mappings_line_item",
        ),
        sa.CheckConstraint("priority >= 1", name="ck_concept_mappings_priority"),
        sa.CheckConstraint("sign_multiplier IN (1, -1)", name="ck_concept_mappings_sign"),
        schema="ref",
    )
    op.create_index(
        "ix_ref_concept_mappings_concept",
        "concept_mappings",
        ["taxonomy", "concept"],
        schema="ref",
    )
    for table in ("mapping_versions", "standard_line_items", "concept_mappings"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON ref.{table} "
            "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON ref.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
        )

    op.bulk_insert(
        sa.table(
            "mapping_versions", sa.column("version"), sa.column("content_sha256"), schema="ref"
        ),
        [{"version": VERSION, "content_sha256": mapping.sha256}],
    )
    op.bulk_insert(
        sa.table(
            "standard_line_items",
            sa.column("mapping_version"),
            sa.column("code"),
            sa.column("statement"),
            sa.column("label"),
            sa.column("parent_code"),
            sa.column("period_type"),
            sa.column("value_type"),
            sa.column("sign_convention"),
            sa.column("is_subtotal"),
            sa.column("subtotal_formula", postgresql.JSONB),
            sa.column("derived_only"),
            sa.column("sort_order"),
            schema="ref",
        ),
        [
            {
                "mapping_version": VERSION,
                "code": i.code,
                "statement": i.statement,
                "label": i.label,
                "parent_code": i.parent,
                "period_type": i.period,
                "value_type": i.value_type,
                "sign_convention": i.sign,
                "is_subtotal": i.is_subtotal,
                "subtotal_formula": (
                    [{"sign": s, "code": c} for s, c in i.subtotal] if i.subtotal else None
                ),
                "derived_only": i.derived_only,
                "sort_order": i.sort_order,
            }
            for i in mapping.line_items.values()
        ],
    )
    op.bulk_insert(
        sa.table(
            "concept_mappings",
            sa.column("mapping_version"),
            sa.column("taxonomy"),
            sa.column("line_item_code"),
            sa.column("priority"),
            sa.column("concept"),
            sa.column("sign_multiplier"),
            schema="ref",
        ),
        [
            {
                "mapping_version": VERSION,
                "taxonomy": c.taxonomy,
                "line_item_code": c.line_item,
                "priority": c.priority,
                "concept": c.concept,
                "sign_multiplier": c.sign,
            }
            for cands in mapping.candidates.values()
            for c in cands
        ],
    )
    op.execute(f"GRANT USAGE ON SCHEMA ref TO {app}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA ref TO {app}")


def downgrade() -> None:
    op.execute("DROP SCHEMA ref CASCADE")

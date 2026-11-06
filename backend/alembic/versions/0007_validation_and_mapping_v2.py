"""Integrity-check results, LTM periods and mapping v2 (Blueprint §3.3, §5.3; D51).

* Loads mapping version v2 (v1 + ``CF.FCF``) from ``mappings/v2/``; refuses to run if the
  files differ from the pinned fingerprint. v1 stays loaded and unchanged.
* ``fin.fiscal_periods`` accepts ``LTM`` periods.
* ``fin.financial_datasets.validation_sha256``: fingerprint of the dataset's check results.
* ``fin.validation_results``: one row per check, period and subject. Same lifecycle as
  values: inserted only while the dataset is a draft, never updated, deleted only from a
  draft (discard). Enforced by trigger for every role.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from erp_backend.standardization.mapping import load
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

VERSION = "v2"
V2_SHA256 = "12911fade2e0dd291ba5faa1353099c3484664142d59fdf371a911b0a6fd4de0"
TYPES_OLD = ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M")
TYPES_NEW = (*TYPES_OLD, "LTM")
PERIOD_CK = "ck_fiscal_periods_ck_fiscal_periods_type"


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _period_types(types: tuple[str, ...]) -> None:
    listed = ", ".join(repr(t) for t in types)
    op.execute(
        f"ALTER TABLE fin.fiscal_periods DROP CONSTRAINT {PERIOD_CK}, "
        f"ADD CONSTRAINT {PERIOD_CK} CHECK (period_type IN ({listed}))"
    )


def _load_mapping() -> None:
    mapping = load(get_settings().mappings_root, VERSION)
    if mapping.sha256 != V2_SHA256:
        raise RuntimeError(
            f"mappings/{VERSION} differs from the pinned fingerprint ({mapping.sha256}); "
            "loaded mapping versions are immutable — put changes in a new version."
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
            *(
                sa.column(c)
                for c in (
                    "mapping_version",
                    "code",
                    "statement",
                    "label",
                    "parent_code",
                    "period_type",
                    "value_type",
                    "sign_convention",
                    "is_subtotal",
                    "derived_only",
                    "sort_order",
                )
            ),
            sa.column("subtotal_formula", postgresql.JSONB),
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
            *(
                sa.column(c)
                for c in (
                    "mapping_version",
                    "taxonomy",
                    "line_item_code",
                    "priority",
                    "concept",
                    "sign_multiplier",
                )
            ),
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


def upgrade() -> None:
    app = _app_role()
    _load_mapping()
    _period_types(TYPES_NEW)
    op.add_column("financial_datasets", sa.Column("validation_sha256", sa.String(64)), schema="fin")
    op.create_check_constraint(
        "validation_sha256_hex",
        "financial_datasets",
        "validation_sha256 IS NULL OR validation_sha256 ~ '^[0-9a-f]{64}$'",
        schema="fin",
    )
    op.create_table(
        "validation_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin.financial_datasets.id"),
            nullable=False,
        ),
        sa.Column("check_code", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column(
            "period_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fin.fiscal_periods.id")
        ),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expected", sa.Numeric()),
        sa.Column("actual", sa.Numeric()),
        sa.Column("difference", sa.Numeric()),
        sa.Column("tolerance", sa.Numeric()),
        sa.Column("unit", sa.String(64)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("severity IN ('error', 'warning')", name="severity"),
        sa.CheckConstraint("status IN ('passed', 'failed', 'not_evaluable')", name="status"),
        sa.CheckConstraint(
            "(status = 'not_evaluable') = (difference IS NULL)", name="difference_present"
        ),
        schema="fin",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_validation_results_check ON fin.validation_results "
        "(dataset_id, check_code, subject, period_id) NULLS NOT DISTINCT"
    )
    op.execute(
        """
        CREATE FUNCTION fin.guard_dataset_child() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            st text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '%.% rows are never updated', TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            SELECT status INTO st FROM fin.financial_datasets
            WHERE id = CASE WHEN TG_OP = 'INSERT' THEN NEW.dataset_id ELSE OLD.dataset_id END;
            IF st IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'check results of a % dataset are immutable', st
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN CASE WHEN TG_OP = 'INSERT' THEN NEW ELSE OLD END;
        END;
        $$;
        CREATE TRIGGER validation_results_guard
        BEFORE INSERT OR UPDATE OR DELETE ON fin.validation_results
        FOR EACH ROW EXECUTE FUNCTION fin.guard_dataset_child();
        CREATE TRIGGER validation_results_no_truncate BEFORE TRUNCATE ON fin.validation_results
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        """
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON fin.validation_results TO {app}")


def downgrade() -> None:
    op.execute("DROP TABLE fin.validation_results")
    op.execute("DROP FUNCTION fin.guard_dataset_child()")
    op.drop_column("financial_datasets", "validation_sha256", schema="fin")
    op.execute("ALTER TABLE fin.fiscal_periods DISABLE TRIGGER fiscal_periods_append_only")
    op.execute("DELETE FROM fin.fiscal_periods WHERE period_type = 'LTM'")
    op.execute("ALTER TABLE fin.fiscal_periods ENABLE TRIGGER fiscal_periods_append_only")
    _period_types(TYPES_OLD)
    for table in ("concept_mappings", "standard_line_items", "mapping_versions"):
        op.execute(f"ALTER TABLE ref.{table} DISABLE TRIGGER {table}_immutable")
        column = "version" if table == "mapping_versions" else "mapping_version"
        op.execute(f"DELETE FROM ref.{table} WHERE {column} = '{VERSION}'")
        op.execute(f"ALTER TABLE ref.{table} ENABLE TRIGGER {table}_immutable")

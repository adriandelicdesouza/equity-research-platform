"""Versioned reference datasets: ``ref.reference_datasets`` and ``ref.reference_values`` (D58).

Blueprint §3.2 (``ref.reference_datasets``, e.g. the Damodaran ERP table), §4.2, §7.5.

* ``ref.reference_datasets`` — one immutable version of a reference table. Its *lineage* is
  (kind, edition date, region, variant), held in ``lineage_key``; ``version_no`` counts
  versions within it (unique). Version 1 has no predecessor; every later version names the
  version it supersedes and carries a reason (CHECKs). Each version comes from exactly one
  ingested file (``source.imports``, reused unchanged; the file is hash-verified in the
  raw archive) and records the canonical SHA-256 of its records.
* ``ref.reference_values`` — the records, one row per (record key, field): an exact
  decimal with its unit (``percent`` = percentage points, ``ratio``, ``multiple``,
  ``count``), or a text/date value; with the row and location in the source file.

Both tables are append-only for every role (trigger) and INSERT/SELECT for the application
role: a correction is a new version, never an edit, so every historical version stays
reproducible. Not forced into ``fin.financial_values`` (company/period/line-item schema).

Downgrade is possible only while no reference dataset exists.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
KINDS = ("implied_erp", "country_risk", "industry_betas", "rating_spreads")
REGIONS = ("us", "europe", "japan", "aus_nz_canada", "emerging", "china", "india", "global")
VARIANTS = ("large_cap", "small_cap")
UNITS = ("percent", "ratio", "multiple", "count", "text", "date")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "reference_datasets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("dataset_kind", sa.String(32), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("edition_date", sa.Date(), nullable=False),
        sa.Column("region", sa.String(32)),
        sa.Column("variant", sa.String(32)),
        sa.Column("lineage_key", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", UUID, sa.ForeignKey("ref.reference_datasets.id")),
        sa.Column("import_id", UUID, sa.ForeignKey("source.imports.id"), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("dataset_kind", KINDS), name="kind"),
        sa.CheckConstraint(
            f"(dataset_kind = 'industry_betas') = (region IS NOT NULL) "
            f"AND (region IS NULL OR {_in('region', REGIONS)})",
            name="region",
        ),
        sa.CheckConstraint(
            f"(dataset_kind = 'rating_spreads') = (variant IS NOT NULL) "
            f"AND (variant IS NULL OR {_in('variant', VARIANTS)})",
            name="variant",
        ),
        sa.CheckConstraint(
            "lineage_key = dataset_kind || '|' || to_char(edition_date, 'YYYY-MM-DD') || '|' "
            "|| coalesce(region, '-') || '|' || coalesce(variant, '-')",
            name="lineage_key",
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no"),
        sa.CheckConstraint(
            "(version_no = 1) = (supersedes_id IS NULL) "
            "AND (version_no = 1 OR length(btrim(coalesce(reason, ''))) >= 10)",
            name="supersedes",
        ),
        sa.CheckConstraint("record_count > 0", name="record_count"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
        sa.UniqueConstraint("lineage_key", "version_no", name="uq_reference_datasets_version"),
        sa.UniqueConstraint("import_id", name="uq_reference_datasets_import"),
        sa.UniqueConstraint("supersedes_id", name="uq_reference_datasets_supersedes"),
        schema="ref",
    )
    op.create_table(
        "reference_values",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("dataset_id", UUID, sa.ForeignKey("ref.reference_datasets.id"), nullable=False),
        sa.Column("record_key", sa.Text(), nullable=False),
        sa.Column("field", sa.String(48), nullable=False),
        sa.Column("value", sa.Numeric()),
        sa.Column("value_text", sa.Text()),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("import_row", sa.Integer(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.CheckConstraint(_in("unit", UNITS), name="unit"),
        sa.CheckConstraint(
            "num_nonnulls(value, value_text) = 1 AND (value IS NULL) = (unit IN ('text', 'date'))",
            name="value_kind",
        ),
        sa.CheckConstraint("length(btrim(record_key)) > 0", name="record_key"),
        sa.UniqueConstraint("dataset_id", "record_key", "field", name="uq_reference_values"),
        schema="ref",
    )
    for table in ("reference_datasets", "reference_values"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON ref.{table} "
            "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON ref.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
        )
    op.execute(f"GRANT SELECT, INSERT ON ref.reference_datasets, ref.reference_values TO {app}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ref.reference_datasets) THEN
                RAISE EXCEPTION 'reference datasets exist; downgrade would lose provenance';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TABLE ref.reference_values, ref.reference_datasets")

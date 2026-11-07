"""Financial-statement file imports (Blueprint §4.6, §3.3, §10.1, §10.3; D57).

* ``source.statement_import_values`` — append-only source records of an ingested statement
  file: one row per (line item, fiscal period) cell, with the value exactly as written in
  the file (``reported_value``), the declared scale exponent, the normalized value
  (``value = reported_value × 10^scale``, enforced), unit, currency, source document/page
  and the cell's location. The file itself is the ``source.imports`` row (0011) and its
  hash-verified archive copy; ``source.imports`` is reused unchanged
  (``template_version = 'financial_statements.v1'``).
* ``fin.financial_values`` accepts ``provenance_kind = 'manual_import'`` (§10.1) and gains
  ``import_id`` + ``import_value_id`` (Blueprint §3.3 ``import_id``): both set exactly when
  the provenance is ``manual_import``, and a composite foreign key makes them consistent.
* ``fin.financial_datasets.import_cutoff_id`` — the highest import value id a ``reported``
  (import) dataset considered, so a rebuild uses exactly the same records. Required for
  ``basis = 'reported'``, absent otherwise.
* ``fin.data_conflicts.kind`` gains ``cross_source`` (import vs SEC) and ``cross_import``
  (two imports disagree); ``fin.conflict_resolutions.candidate_source`` tells which id
  space a selected candidate belongs to (``reported_fact`` / ``import_value``).

Downgrade is possible only while no import value exists (it would otherwise lose
provenance); it fails loudly in that case.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
PROVENANCE_CK = "ck_financial_values_ck_values_provenance"
PERIOD_TYPES = ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _drop(table: str, constraint: str) -> str:
    return f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"


def upgrade() -> None:
    app = _app_role()
    types = ", ".join(f"'{t}'" for t in PERIOD_TYPES)
    op.create_table(
        "statement_import_values",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("import_id", UUID, sa.ForeignKey("source.imports.id"), nullable=False),
        sa.Column("import_row", sa.Integer(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("mapping_version", sa.String(16), nullable=False),
        sa.Column("line_item_code", sa.String(64), nullable=False),
        sa.Column("period_type", sa.String(4), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("reported_value", sa.Numeric(), nullable=False),
        sa.Column("scale", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Text()),
        sa.ForeignKeyConstraint(
            ["mapping_version", "line_item_code"],
            ["ref.standard_line_items.mapping_version", "ref.standard_line_items.code"],
            name="fk_statement_import_values_line_item",
        ),
        sa.UniqueConstraint(
            "import_id",
            "line_item_code",
            "period_type",
            "period_start",
            "period_end",
            name="uq_statement_import_values_cell",
        ),
        sa.UniqueConstraint("id", "import_id", name="uq_statement_import_values_id_import"),
        sa.CheckConstraint(f"period_type IN ({types})", name="period_type"),
        sa.CheckConstraint("period_end >= period_start", name="period_dates"),
        sa.CheckConstraint("scale IN (0, 3, 6, 9)", name="scale"),
        sa.CheckConstraint(
            "value = reported_value * power(10::numeric, scale)", name="value_normalized"
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        sa.CheckConstraint("length(btrim(source_document)) > 0", name="source_document"),
        schema="source",
    )
    op.create_index(
        "ix_source_statement_import_values_key",
        "statement_import_values",
        ["company_id", "line_item_code", "period_end"],
        schema="source",
    )
    op.execute(
        """
        CREATE TRIGGER statement_import_values_append_only
        BEFORE UPDATE OR DELETE ON source.statement_import_values
        FOR EACH ROW EXECUTE FUNCTION source.reject_modification();
        CREATE TRIGGER statement_import_values_no_truncate
        BEFORE TRUNCATE ON source.statement_import_values
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON source.statement_import_values TO {app}")

    op.add_column("financial_values", sa.Column("import_id", UUID), schema="fin")
    op.add_column("financial_values", sa.Column("import_value_id", sa.BigInteger()), schema="fin")
    op.create_foreign_key(
        "fk_financial_values_import_value",
        "financial_values",
        "statement_import_values",
        ["import_value_id", "import_id"],
        ["id", "import_id"],
        source_schema="fin",
        referent_schema="source",
    )
    op.execute(
        f"ALTER TABLE fin.financial_values DROP CONSTRAINT {PROVENANCE_CK}, "
        f"ADD CONSTRAINT {PROVENANCE_CK} "
        "CHECK (provenance_kind IN ('reported', 'derived', 'analyst', 'manual_import'))"
    )
    op.create_check_constraint(
        "import_iff_manual",
        "financial_values",
        "(provenance_kind = 'manual_import') = (import_value_id IS NOT NULL) "
        "AND (import_id IS NULL) = (import_value_id IS NULL)",
        schema="fin",
    )

    op.add_column(
        "financial_datasets", sa.Column("import_cutoff_id", sa.BigInteger()), schema="fin"
    )
    op.create_check_constraint(
        "import_cutoff_iff_reported",
        "financial_datasets",
        "(basis = 'reported') = (import_cutoff_id IS NOT NULL)",
        schema="fin",
    )

    op.execute(
        "ALTER TABLE fin.data_conflicts DROP CONSTRAINT ck_data_conflicts_kind, "
        "ADD CONSTRAINT ck_data_conflicts_kind CHECK "
        "(kind IN ('within_filing', 'mixed_vintage', 'cross_source', 'cross_import'))"
    )
    op.add_column(
        "conflict_resolutions", sa.Column("candidate_source", sa.String(16)), schema="fin"
    )
    op.create_check_constraint(
        "candidate_source",
        "conflict_resolutions",
        "candidate_source IS NULL OR (action = 'select_candidate' AND "
        "candidate_source IN ('reported_fact', 'import_value'))",
        schema="fin",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM source.statement_import_values) THEN
                RAISE EXCEPTION 'statement import values exist; downgrade would lose provenance';
            END IF;
        END $$;
        """
    )
    op.execute(_drop("fin.conflict_resolutions", "ck_conflict_resolutions_candidate_source"))
    op.drop_column("conflict_resolutions", "candidate_source", schema="fin")
    op.execute(
        "ALTER TABLE fin.data_conflicts DROP CONSTRAINT ck_data_conflicts_kind, "
        "ADD CONSTRAINT ck_data_conflicts_kind CHECK "
        "(kind IN ('within_filing', 'mixed_vintage'))"
    )
    op.execute(_drop("fin.financial_datasets", "ck_financial_datasets_import_cutoff_iff_reported"))
    op.drop_column("financial_datasets", "import_cutoff_id", schema="fin")
    op.execute(
        "ALTER TABLE fin.financial_values DROP CONSTRAINT ck_financial_values_import_iff_manual"
    )
    op.execute(
        f"ALTER TABLE fin.financial_values DROP CONSTRAINT {PROVENANCE_CK}, "
        f"ADD CONSTRAINT {PROVENANCE_CK} "
        "CHECK (provenance_kind IN ('reported', 'derived', 'analyst'))"
    )
    op.execute(_drop("fin.financial_values", "fk_financial_values_import_value"))
    op.drop_column("financial_values", "import_value_id", schema="fin")
    op.drop_column("financial_values", "import_id", schema="fin")
    op.drop_table("statement_import_values", schema="source")

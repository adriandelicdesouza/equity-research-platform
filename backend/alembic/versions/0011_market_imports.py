"""File imports of market data: ``source.imports`` and import provenance on observations.

Blueprint §3.3 defines ``source.imports`` (one row per imported file); §10.1 defines the
``manual_import`` provenance (file hash, location in the file). This migration:

* creates ``source.imports`` — append-only, one row per *ingested* file: template, original
  file name, SHA-256 of the exact bytes, path of the hash-verified copy in the raw archive,
  the analyst's source attribution, the validation counts and every skipped record;
* lets ``market.price_observations`` and ``market.corporate_actions`` be provenanced by
  either a provider fetch (``fetch_id``) or an import (``import_id`` + ``import_row``),
  exactly one of the two (CHECK). The unique indexes over observed values (0010) are
  unchanged, so idempotency works the same way for both origins.

Downgrade is only possible while no imported observation exists (they cannot be deleted:
the tables are append-only); it fails loudly otherwise.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
OBSERVATION_TABLES = ("price_observations", "corporate_actions")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "imports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "provider_code", sa.String(32), sa.ForeignKey("source.providers.code"), nullable=False
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("skipped", postgresql.JSONB(), nullable=False),
        sa.Column("imported_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("imported_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('csv', 'excel')", name="kind"),
        sa.CheckConstraint("status IN ('complete', 'partial')", name="status"),
        sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        sa.CheckConstraint("length(btrim(source_name)) > 0", name="source_name"),
        schema="source",
    )
    op.create_index("ix_source_imports_sha", "imports", ["file_sha256"], schema="source")
    op.execute(
        "CREATE TRIGGER imports_append_only BEFORE UPDATE OR DELETE ON source.imports "
        "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER imports_no_truncate BEFORE TRUNCATE ON source.imports "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(f"GRANT SELECT, INSERT ON source.imports TO {app}")
    for table in OBSERVATION_TABLES:
        op.alter_column(table, "fetch_id", nullable=True, schema="market")
        op.add_column(
            table,
            sa.Column("import_id", UUID, sa.ForeignKey("source.imports.id")),
            schema="market",
        )
        op.add_column(table, sa.Column("import_row", sa.Integer()), schema="market")
        op.create_check_constraint(
            "origin",
            table,
            "num_nonnulls(fetch_id, import_id) = 1 AND (import_id IS NULL) = (import_row IS NULL)",
            schema="market",
        )
        op.create_index(f"ix_market_{table}_import", table, ["import_id"], schema="market")


def downgrade() -> None:
    for table in OBSERVATION_TABLES:
        op.drop_index(f"ix_market_{table}_import", table_name=table, schema="market")
        op.drop_constraint(op.f(f"ck_{table}_origin"), table, schema="market", type_="check")
        op.drop_column(table, "import_row", schema="market")
        op.drop_column(table, "import_id", schema="market")
        op.alter_column(table, "fetch_id", nullable=False, schema="market")
    op.drop_table("imports", schema="source")

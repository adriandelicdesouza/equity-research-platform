"""Immutable sensitivity results ``val.sensitivity_runs`` (Phase 5b; design §9.5, §10).

One row per sensitivity grid computed over a stored run: one-way, two-way or tornado, every
cell a full engine execution of the run's stored input document with one or two inputs
replaced. Cells violating hard checks are stored as refused with their code, never as a number.

Append-only for every role (``source.reject_modification``); unique per
(run, configuration hash, engine version) for content idempotency. The application role gets
SELECT/INSERT only.

Downgrade is possible only while no sensitivity result exists.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
SHA = "~ '^[0-9a-f]{64}$'"


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "sensitivity_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("val.valuation_runs.id"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("val.valuation_projects.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("config_sha256", sa.String(64), nullable=False),
        sa.Column("base", JSONB, nullable=False),
        sa.Column("cells", JSONB, nullable=False),
        sa.Column("cell_count", sa.Integer(), nullable=False),
        sa.Column("refused_count", sa.Integer(), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120)),
        sa.Column("action_id", UUID),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('one_way', 'two_way', 'tornado')", name="kind"),
        sa.CheckConstraint(
            "metric IN ('per_share', 'equity_value', 'enterprise_value')", name="metric"
        ),
        sa.CheckConstraint("cell_count BETWEEN 1 AND 100", name="cell_count"),
        sa.CheckConstraint("refused_count BETWEEN 0 AND cell_count", name="refused_count"),
        sa.CheckConstraint(f"config_sha256 {SHA}", name="config_sha256"),
        sa.CheckConstraint(f"result_sha256 {SHA}", name="result_sha256"),
        sa.UniqueConstraint("run_id", "config_sha256", "engine_version"),
        schema="val",
    )
    op.create_index(
        "ix_sensitivity_runs_run", "sensitivity_runs", ["run_id", "created_at"], schema="val"
    )
    op.execute(
        """
        CREATE FUNCTION val.check_sensitivity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM val.valuation_runs r
                           WHERE r.id = NEW.run_id AND r.project_id = NEW.project_id) THEN
                RAISE EXCEPTION 'a sensitivity belongs to its run''s project'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER sensitivity_runs_check BEFORE INSERT ON val.sensitivity_runs "
        "FOR EACH ROW EXECUTE FUNCTION val.check_sensitivity()"
    )
    op.execute(
        "CREATE TRIGGER sensitivity_runs_append_only BEFORE UPDATE OR DELETE ON "
        "val.sensitivity_runs FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER sensitivity_runs_no_truncate BEFORE TRUNCATE ON val.sensitivity_runs "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(f"GRANT SELECT, INSERT ON val.sensitivity_runs TO {_app_role()}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM val.sensitivity_runs) THEN
                RAISE EXCEPTION 'sensitivity results exist; downgrade would lose them';
            END IF;
        END $$;
        """
    )
    op.execute("DROP TABLE val.sensitivity_runs")
    op.execute("DROP FUNCTION val.check_sensitivity()")

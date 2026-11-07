"""Valuation workflow schema ``val`` and classification blocks (M3 phase 5; P16 approved).

Design: ``docs/architecture/valuation-api.md`` §10.

* ``ref.valuation_blocks`` — classification blocks loaded from
  ``mappings/valuation-blocks/v1.yaml`` (SHA-256 pinned here, like the D49 mappings).
* ``val.valuation_projects`` / ``val.scenarios`` — mutable configuration: UPDATE allowed (the
  services audit every change), DELETE/TRUNCATE refused. Triggers keep a project's company
  fixed and its dataset and listing within that company; a scenario stays in its project.
* ``val.assumption_revisions`` — append-only assumption history.
* ``val.valuation_runs``, ``val.run_traces``, ``val.run_inputs``, ``val.run_decisions`` —
  immutable runs. An insert trigger refuses a run whose dataset is not locked or not the
  company's, or whose scenario is not the project's (defence in depth behind the services).

Append-only tables refuse UPDATE/DELETE/TRUNCATE for every role (``source.reject_modification``).
The application role gets SELECT/INSERT, plus UPDATE on the two mutable tables; never DELETE.

Downgrade is possible only while no project exists.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from erp_backend.valuation.blocks import load as load_blocks
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)
BLOCK_VERSION = "v1"
V1_SHA256 = "3c04f74def8b119c66e0e0483e420a5b8984a599f41946413e32da1fb1c6c9ab"
SHA = "~ '^[0-9a-f]{64}$'"
APPEND_ONLY = (
    "ref.valuation_blocks",
    "val.assumption_revisions",
    "val.valuation_runs",
    "val.run_traces",
    "val.run_inputs",
    "val.run_decisions",
)


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _blocks_table() -> None:
    op.create_table(
        "valuation_blocks",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("block_version", sa.String(16), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("methodology", sa.String(32), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            _in("policy", ("permitted", "permitted_with_warnings", "blocked")), name="policy"
        ),
        sa.CheckConstraint(f"definition_sha256 {SHA}", name="definition_sha256"),
        sa.CheckConstraint(f"file_sha256 {SHA}", name="file_sha256"),
        sa.UniqueConstraint("block_version", "classification", "methodology"),
        schema="ref",
    )
    blocks = load_blocks(get_settings().mappings_root, BLOCK_VERSION)
    if blocks.sha256 != V1_SHA256:
        raise RuntimeError(
            f"mappings/valuation-blocks/{BLOCK_VERSION}.yaml differs from the pinned fingerprint "
            f"({blocks.sha256}); loaded block versions are immutable — add a new version."
        )
    table = sa.table(
        "valuation_blocks",
        sa.column("block_version"),
        sa.column("classification"),
        sa.column("methodology"),
        sa.column("policy"),
        sa.column("definition", postgresql.JSONB()),
        sa.column("definition_sha256"),
        sa.column("file_sha256"),
        schema="ref",
    )
    op.bulk_insert(
        table,
        [
            {
                "block_version": BLOCK_VERSION,
                "classification": b.classification,
                "methodology": b.methodology,
                "policy": b.policy,
                "definition": b.definition,
                "definition_sha256": b.definition_sha256,
                "file_sha256": blocks.sha256,
            }
            for b in blocks.blocks
        ],
    )


def _projects_and_scenarios() -> None:
    op.create_table(
        "valuation_projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("listing_id", UUID, sa.ForeignKey("market.securities.id")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("thesis", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("dataset_id", UUID, sa.ForeignKey("fin.financial_datasets.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("updated_at", TS),
        sa.Column("status_reason", sa.Text()),
        sa.CheckConstraint(_in("status", ("active", "archived")), name="status"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="name"),
        sa.CheckConstraint("thesis IS NULL OR length(thesis) <= 20000", name="thesis"),
        sa.CheckConstraint("revision_no >= 1", name="revision_no"),
        schema="val",
    )
    op.create_index(
        "uq_valuation_projects_active_name",
        "valuation_projects",
        ["company_id", sa.text("lower(btrim(name))")],
        unique=True,
        schema="val",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_valuation_projects_company",
        "valuation_projects",
        ["company_id", "status"],
        schema="val",
    )
    op.create_index(
        "ix_valuation_projects_owner", "valuation_projects", ["owner_user_id"], schema="val"
    )
    op.create_table(
        "scenarios",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("val.valuation_projects.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_scenario_id", UUID, sa.ForeignKey("val.scenarios.id")),
        sa.Column("probability", sa.Numeric()),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS),
        sa.CheckConstraint(_in("kind", ("base", "bull", "bear", "custom")), name="kind"),
        sa.CheckConstraint(_in("status", ("active", "archived")), name="status"),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)", name="probability"
        ),
        sa.CheckConstraint("revision_no >= 0", name="revision_no"),
        sa.CheckConstraint("parent_scenario_id IS DISTINCT FROM id", name="no_self_parent"),
        sa.UniqueConstraint("project_id", "name"),
        schema="val",
    )
    op.create_index(
        "uq_scenarios_one_base",
        "scenarios",
        ["project_id"],
        unique=True,
        schema="val",
        postgresql_where=sa.text("kind = 'base'"),
    )
    op.execute(
        """
        CREATE FUNCTION val.check_project() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.company_id <> OLD.company_id THEN
                RAISE EXCEPTION 'a project''s company is immutable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM fin.financial_datasets d
                           WHERE d.id = NEW.dataset_id AND d.company_id = NEW.company_id) THEN
                RAISE EXCEPTION 'dataset % does not belong to company %',
                    NEW.dataset_id, NEW.company_id USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.listing_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM market.securities s
                    WHERE s.id = NEW.listing_id AND s.company_id = NEW.company_id) THEN
                RAISE EXCEPTION 'listing % does not belong to company %',
                    NEW.listing_id, NEW.company_id USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER valuation_projects_check BEFORE INSERT OR UPDATE ON "
        "val.valuation_projects FOR EACH ROW EXECUTE FUNCTION val.check_project()"
    )
    op.execute(
        """
        CREATE FUNCTION val.check_scenario() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND (NEW.project_id <> OLD.project_id OR NEW.kind <> OLD.kind)
            THEN
                RAISE EXCEPTION 'a scenario''s project and kind are immutable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.parent_scenario_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM val.scenarios p
                    WHERE p.id = NEW.parent_scenario_id AND p.project_id = NEW.project_id) THEN
                RAISE EXCEPTION 'parent scenario must belong to the same project'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER scenarios_check BEFORE INSERT OR UPDATE ON val.scenarios "
        "FOR EACH ROW EXECUTE FUNCTION val.check_scenario()"
    )
    for table in ("val.valuation_projects", "val.scenarios"):
        name = table.split(".")[1]
        op.execute(
            f"CREATE TRIGGER {name}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
        )
        op.execute(
            f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
        )


def _assumptions() -> None:
    op.create_table(
        "assumption_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("scenario_id", UUID, sa.ForeignKey("val.scenarios.id"), nullable=False),
        sa.Column("scenario_revision_no", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("period_index", sa.SmallInteger()),
        sa.Column("value", sa.Numeric()),
        sa.Column("value_json", JSONB),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("provenance_kind", sa.String(24), nullable=False),
        sa.Column("source", JSONB),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", ("suggested", "approved", "cleared")), name="status"),
        sa.CheckConstraint(
            _in(
                "provenance_kind",
                ("analyst_assumption", "suggested", "reference", "provider_market"),
            ),
            name="provenance_kind",
        ),
        sa.CheckConstraint(
            "(status = 'cleared' AND value IS NULL AND value_json IS NULL) OR "
            "(status <> 'cleared' AND (value IS NULL) <> (value_json IS NULL))",
            name="one_value",
        ),
        sa.CheckConstraint(
            "period_index IS NULL OR period_index BETWEEN 1 AND 20", name="period_index"
        ),
        sa.CheckConstraint("scenario_revision_no >= 1", name="scenario_revision_no"),
        schema="val",
    )
    op.create_index(
        "uq_assumption_revisions_key",
        "assumption_revisions",
        ["scenario_id", "key", sa.text("coalesce(period_index, 0)"), "scenario_revision_no"],
        unique=True,
        schema="val",
    )
    op.create_index(
        "ix_assumption_revisions_current",
        "assumption_revisions",
        ["scenario_id", "key", "period_index", sa.text("id DESC")],
        schema="val",
    )


def _runs() -> None:
    op.create_table(
        "valuation_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("val.valuation_projects.id"), nullable=False),
        sa.Column("scenario_id", UUID, sa.ForeignKey("val.scenarios.id"), nullable=False),
        sa.Column("scenario_revision_no", sa.Integer(), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("listing_id", UUID, sa.ForeignKey("market.securities.id")),
        sa.Column("methodology", sa.String(32), nullable=False),
        sa.Column("method_version", sa.String(16), nullable=False),
        sa.Column("produces", sa.String(32), nullable=False),
        sa.Column("dataset_id", UUID, sa.ForeignKey("fin.financial_datasets.id"), nullable=False),
        sa.Column("dataset_version_no", sa.Integer(), nullable=False),
        sa.Column("dataset_basis", sa.String(16), nullable=False),
        sa.Column("dataset_content_sha256", sa.String(64), nullable=False),
        sa.Column("block_id", UUID, sa.ForeignKey("ref.valuation_blocks.id"), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("balance_sheet_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("input_document", JSONB, nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("engine_input_sha256", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(16), nullable=False),
        sa.Column("module_versions", JSONB, nullable=False),
        sa.Column("outputs", JSONB, nullable=False),
        sa.Column("primary_value", sa.Numeric(), nullable=False),
        sa.Column("per_share_value", sa.Numeric(), nullable=False),
        sa.Column("validation", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120)),
        sa.Column("action_id", UUID),
        sa.Column("request_id", sa.String(64)),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("methodology", ("dcf_fcff", "dcf_fcfe")), name="methodology"),
        sa.CheckConstraint(
            _in("produces", ("enterprise_value", "equity_value", "per_share_value")),
            name="produces",
        ),
        sa.CheckConstraint("valuation_date >= balance_sheet_date", name="dates"),
        sa.CheckConstraint(f"input_sha256 {SHA}", name="input_sha256"),
        sa.CheckConstraint(f"engine_input_sha256 {SHA}", name="engine_input_sha256"),
        sa.CheckConstraint(f"output_sha256 {SHA}", name="output_sha256"),
        sa.CheckConstraint(f"dataset_content_sha256 {SHA}", name="dataset_content_sha256"),
        sa.UniqueConstraint(
            "project_id", "input_sha256", "engine_version", name="uq_valuation_runs_content"
        ),
        schema="val",
    )
    op.create_index(
        "ix_valuation_runs_project",
        "valuation_runs",
        ["project_id", sa.text("created_at DESC")],
        schema="val",
    )
    op.create_index("ix_valuation_runs_dataset", "valuation_runs", ["dataset_id"], schema="val")
    op.create_index(
        "ix_valuation_runs_company",
        "valuation_runs",
        ["company_id", "valuation_date"],
        schema="val",
    )
    op.execute(
        """
        CREATE FUNCTION val.check_run() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM fin.financial_datasets d
                           WHERE d.id = NEW.dataset_id AND d.company_id = NEW.company_id
                             AND d.status = 'locked'
                             AND d.version_no = NEW.dataset_version_no
                             AND d.content_sha256 = NEW.dataset_content_sha256) THEN
                RAISE EXCEPTION 'a run needs the company''s locked dataset (got %)',
                    NEW.dataset_id USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM val.scenarios s JOIN val.valuation_projects p
                               ON p.id = s.project_id
                           WHERE s.id = NEW.scenario_id AND p.id = NEW.project_id
                             AND p.company_id = NEW.company_id) THEN
                RAISE EXCEPTION 'scenario, project and company of a run must match'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER valuation_runs_check BEFORE INSERT ON val.valuation_runs "
        "FOR EACH ROW EXECUTE FUNCTION val.check_run()"
    )
    op.create_table(
        "run_traces",
        sa.Column("run_id", UUID, sa.ForeignKey("val.valuation_runs.id"), primary_key=True),
        sa.Column("trace", JSONB, nullable=False),
        sa.Column("trace_sha256", sa.String(64), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"trace_sha256 {SHA}", name="trace_sha256"),
        schema="val",
    )
    op.create_table(
        "run_inputs",
        sa.Column("run_id", UUID, sa.ForeignKey("val.valuation_runs.id"), primary_key=True),
        sa.Column("input_key", sa.String(96), primary_key=True),
        sa.Column("value", sa.Numeric()),
        sa.Column("value_json", JSONB),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("source_value", sa.Numeric()),
        sa.Column("source_unit", sa.String(16)),
        sa.Column("conversion", sa.String(32)),
        sa.Column("provenance_kind", sa.String(24), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("as_of_date", sa.Date()),
        sa.Column("warnings", JSONB, nullable=False),
        schema="val",
    )
    op.create_index(
        "ix_run_inputs_source", "run_inputs", ["source_type", "source_ref"], schema="val"
    )
    op.create_table(
        "run_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("val.valuation_runs.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", JSONB, nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("auth.users.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            _in(
                "kind",
                (
                    "classification_acknowledgement",
                    "check_override",
                    "input_override",
                    "admin_override",
                ),
            ),
            name="kind",
        ),
        sa.CheckConstraint("length(btrim(reason)) >= 10 AND reason ~ '[[:alpha:]]'", name="reason"),
        schema="val",
    )
    op.create_index("ix_run_decisions_run", "run_decisions", ["run_id"], schema="val")


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA val")
    _blocks_table()
    _projects_and_scenarios()
    _assumptions()
    _runs()
    for table in APPEND_ONLY:
        name = table.split(".")[1]
        op.execute(
            f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
        )
        op.execute(
            f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
        )
    op.execute(f"GRANT USAGE ON SCHEMA val TO {app}")
    op.execute(f"GRANT SELECT ON ref.valuation_blocks TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON val.valuation_projects, val.scenarios TO {app}")
    op.execute(
        "GRANT SELECT, INSERT ON val.assumption_revisions, val.valuation_runs, "
        f"val.run_traces, val.run_inputs, val.run_decisions TO {app}"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM val.valuation_projects) THEN
                RAISE EXCEPTION 'valuation projects exist; downgrade would lose them';
            END IF;
        END $$;
        """
    )
    op.execute("DROP SCHEMA val CASCADE")
    op.execute("DROP TABLE ref.valuation_blocks")

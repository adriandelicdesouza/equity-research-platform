"""Fiscal periods and versioned standardized datasets (Blueprint §3.3, §3.4; D10, D50).

Lifecycle rules are enforced by triggers for every role, not only by application code:

* ``fin.fiscal_periods`` is append-only (periods are shared by all dataset versions).
* ``fin.financial_datasets``: rows are never deleted. The only permitted updates are the
  transitions draft → locked and draft → discarded, changing nothing but the status
  fields. Locking requires no ambiguous values and the recorded value count.
  Discarding requires the values to have been removed first. At most one draft per
  company and basis.
* ``fin.financial_values``: never updated; inserted only into a draft (with the dataset's
  mapping version); deleted only from a draft (discard).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
PERIOD_TYPES = ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M")


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA fin")
    op.create_table(
        "fiscal_periods",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(4), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("weeks", sa.SmallInteger()),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "company_id", "period_type", "start_date", "end_date", name="uq_fiscal_periods_window"
        ),
        sa.CheckConstraint(_in("period_type", PERIOD_TYPES), name="ck_fiscal_periods_type"),
        sa.CheckConstraint("end_date >= start_date", name="ck_fiscal_periods_dates"),
        sa.CheckConstraint("weeks IS NULL OR weeks IN (52, 53)", name="ck_fiscal_periods_weeks"),
        schema="fin",
    )
    op.create_table(
        "financial_datasets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_dataset_id", UUID, sa.ForeignKey("fin.financial_datasets.id")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "mapping_version",
            sa.String(16),
            sa.ForeignKey("ref.mapping_versions.version"),
            nullable=False,
        ),
        sa.Column("mapping_sha256", sa.String(64), nullable=False),
        sa.Column("taxonomy", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("selection_policy", sa.String(16), nullable=False),
        sa.Column("facts_as_of", sa.Date(), nullable=False),
        sa.Column("format_version", sa.SmallInteger(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("interim_coverage", sa.String(8), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column("source_summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", TS),
        sa.Column("locked_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column("discarded_at", TS),
        sa.Column("status_reason", sa.Text()),
        sa.UniqueConstraint("company_id", "basis", "version_no", name="uq_datasets_version"),
        sa.CheckConstraint(
            _in("basis", ("reported", "standardized", "adjusted")), name="ck_datasets_basis"
        ),
        sa.CheckConstraint(
            _in("status", ("draft", "locked", "discarded")), name="ck_datasets_status"
        ),
        sa.CheckConstraint(
            _in("selection_policy", ("latest_reported", "first_reported")),
            name="ck_datasets_policy",
        ),
        sa.CheckConstraint(
            _in("interim_coverage", ("none", "partial", "full")), name="ck_datasets_coverage"
        ),
        sa.CheckConstraint(
            "(status = 'locked') = (locked_at IS NOT NULL)", name="ck_datasets_locked_at"
        ),
        sa.CheckConstraint(
            "(status = 'discarded') = (discarded_at IS NOT NULL)", name="ck_datasets_discarded_at"
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' AND input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_datasets_hashes",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_datasets_version_no"),
        schema="fin",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_datasets_one_draft ON fin.financial_datasets "
        "(company_id, basis) WHERE status = 'draft'"
    )
    op.create_table(
        "financial_values",
        sa.Column("dataset_id", UUID, sa.ForeignKey("fin.financial_datasets.id"), nullable=False),
        sa.Column("period_id", UUID, sa.ForeignKey("fin.fiscal_periods.id"), nullable=False),
        sa.Column("line_item_code", sa.String(64), nullable=False),
        sa.Column("mapping_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric()),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("scale", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("provenance_kind", sa.String(16), nullable=False),
        sa.Column("concept", sa.String(256), nullable=False),
        sa.Column("concept_priority", sa.SmallInteger(), nullable=False),
        sa.Column("sign_multiplier", sa.SmallInteger(), nullable=False),
        sa.Column("source_fact_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "dataset_id", "period_id", "line_item_code", name="pk_financial_values"
        ),
        sa.ForeignKeyConstraint(
            ["mapping_version", "line_item_code"],
            ["ref.standard_line_items.mapping_version", "ref.standard_line_items.code"],
            name="fk_financial_values_line_item",
        ),
        sa.CheckConstraint(_in("status", ("ok", "ambiguous")), name="ck_values_status"),
        sa.CheckConstraint("(status = 'ok') = (value IS NOT NULL)", name="ck_values_value_present"),
        sa.CheckConstraint(
            _in("provenance_kind", ("reported", "derived")), name="ck_values_provenance"
        ),
        sa.CheckConstraint("sign_multiplier IN (1, -1)", name="ck_values_sign"),
        sa.CheckConstraint(
            "provenance_kind <> 'derived' OR detail ? 'rule'", name="ck_values_derivation"
        ),
        schema="fin",
    )
    op.create_index(
        "ix_fin_values_period", "financial_values", ["period_id", "line_item_code"], schema="fin"
    )

    op.execute(
        """
        CREATE TRIGGER fiscal_periods_append_only BEFORE UPDATE OR DELETE ON fin.fiscal_periods
        FOR EACH ROW EXECUTE FUNCTION source.reject_modification();
        CREATE TRIGGER fiscal_periods_no_truncate BEFORE TRUNCATE ON fin.fiscal_periods
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        CREATE TRIGGER datasets_no_truncate BEFORE TRUNCATE ON fin.financial_datasets
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        CREATE TRIGGER values_no_truncate BEFORE TRUNCATE ON fin.financial_values
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        """
    )
    op.execute(
        """
        CREATE FUNCTION fin.guard_dataset() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            lifecycle text[] := ARRAY['status', 'locked_at', 'locked_by', 'discarded_at',
                                      'status_reason'];
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'fin.financial_datasets rows are never deleted (discard instead)'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'dataset % is % and immutable', OLD.id, OLD.status
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status NOT IN ('locked', 'discarded') THEN
                RAISE EXCEPTION 'a draft dataset can only be locked or discarded'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF (to_jsonb(NEW) - lifecycle) IS DISTINCT FROM (to_jsonb(OLD) - lifecycle) THEN
                RAISE EXCEPTION 'only lifecycle fields of a dataset may change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status = 'locked' THEN
                IF EXISTS (SELECT 1 FROM fin.financial_values
                           WHERE dataset_id = NEW.id AND status = 'ambiguous') THEN
                    RAISE EXCEPTION 'dataset % has ambiguous values and cannot be locked', NEW.id
                        USING ERRCODE = 'check_violation';
                END IF;
                IF (SELECT count(*) FROM fin.financial_values WHERE dataset_id = NEW.id)
                   <> NEW.value_count THEN
                    RAISE EXCEPTION 'dataset % value count does not match', NEW.id
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF EXISTS (SELECT 1 FROM fin.financial_values WHERE dataset_id = NEW.id) THEN
                RAISE EXCEPTION 'remove the values before discarding dataset %', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER datasets_lifecycle BEFORE UPDATE OR DELETE ON fin.financial_datasets
        FOR EACH ROW EXECUTE FUNCTION fin.guard_dataset();

        CREATE FUNCTION fin.guard_value() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            ds record;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'fin.financial_values rows are never updated'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            SELECT status, mapping_version INTO ds FROM fin.financial_datasets
            WHERE id = CASE WHEN TG_OP = 'INSERT' THEN NEW.dataset_id ELSE OLD.dataset_id END;
            IF ds.status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'values of a % dataset are immutable', ds.status
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.mapping_version <> ds.mapping_version THEN
                    RAISE EXCEPTION 'value mapping version differs from its dataset'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            RETURN OLD;
        END;
        $$;
        CREATE TRIGGER values_guard BEFORE INSERT OR UPDATE OR DELETE ON fin.financial_values
        FOR EACH ROW EXECUTE FUNCTION fin.guard_value();
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA fin TO {app}")
    op.execute(f"GRANT SELECT, INSERT ON fin.fiscal_periods TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON fin.financial_datasets TO {app}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON fin.financial_values TO {app}")


def downgrade() -> None:
    op.execute("DROP SCHEMA fin CASCADE")

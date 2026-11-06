"""Data conflicts and analyst resolutions (Blueprint §10.3; D13, D52).

* ``fin.conflict_resolutions``: append-only analyst decisions per company and conflict
  fingerprint (select a candidate, enter a value with a source, documented override,
  revoke). A meaningful reason is required for every action. Never updated or deleted.
* ``fin.data_conflicts``: conflicts detected by a dataset build, with candidates (filing,
  form, filing date, concept, value), materiality and status. Inserted only into a
  draft; never updated or deleted, so discarded datasets keep their conflict history.
* ``fin.financial_values`` accepts ``provenance_kind = 'analyst'`` (an entered value).
* ``fin.financial_datasets.resolution_cutoff_id``: the highest resolution id the build
  considered, so a rebuild applies exactly the same resolutions.
* Locking is refused while the dataset has open material conflicts (trigger).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
PROVENANCE_CK = "ck_financial_values_ck_values_provenance"

GUARD_DATASET = """
CREATE OR REPLACE FUNCTION fin.guard_dataset() RETURNS trigger
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
        %(conflict_rule)s
        IF (SELECT count(*) FROM fin.financial_values WHERE dataset_id = NEW.id)
           <> NEW.value_count THEN
            RAISE EXCEPTION 'dataset % value count does not match', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM fin.financial_values WHERE dataset_id = NEW.id) THEN
        RAISE EXCEPTION 'remove the values before discarding dataset %%', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""
CONFLICT_RULE = """IF EXISTS (SELECT 1 FROM fin.data_conflicts
                   WHERE dataset_id = NEW.id AND is_material AND status = 'open') THEN
            RAISE EXCEPTION 'dataset %% has open material conflicts and cannot be locked', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;"""


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def _guard(conflict_rule: str) -> str:
    # Only the conflict rule placeholder is substituted; %% escapes literal percent signs.
    return GUARD_DATASET.replace("%(conflict_rule)s", conflict_rule).replace("%%", "%")


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "conflict_resolutions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("company_id", UUID, sa.ForeignKey("market.companies.id"), nullable=False),
        sa.Column("conflict_key", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("candidate_fact_id", sa.BigInteger()),
        sa.Column("value", sa.Numeric()),
        sa.Column("source_reference", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detected_in_dataset_id",
            UUID,
            sa.ForeignKey("fin.financial_datasets.id"),
            nullable=False,
        ),
        sa.Column("line_item_code", sa.String(64), nullable=False),
        sa.Column("period_type", sa.String(4), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("auth.users.id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('select_candidate', 'enter_value', 'override', 'revoke')", name="action"
        ),
        sa.CheckConstraint("char_length(btrim(reason)) >= 10", name="reason_meaningful"),
        sa.CheckConstraint(
            "(action = 'select_candidate') = (candidate_fact_id IS NOT NULL)",
            name="candidate_iff_select",
        ),
        sa.CheckConstraint(
            "(action = 'enter_value') = (value IS NOT NULL) AND "
            "(action <> 'enter_value' OR char_length(btrim(coalesce(source_reference, ''))) > 0)",
            name="value_iff_enter",
        ),
        schema="fin",
    )
    op.create_index(
        "ix_fin_conflict_resolutions_key",
        "conflict_resolutions",
        ["company_id", "conflict_key", "id"],
        schema="fin",
    )
    op.create_table(
        "data_conflicts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("dataset_id", UUID, sa.ForeignKey("fin.financial_datasets.id"), nullable=False),
        sa.Column("conflict_key", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("line_item_code", sa.String(64), nullable=False),
        sa.Column("period_id", UUID, sa.ForeignKey("fin.fiscal_periods.id"), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("policy_fact_id", sa.BigInteger()),
        sa.Column("is_material", sa.Boolean(), nullable=False),
        sa.Column("absolute_difference", sa.Numeric(), nullable=False),
        sa.Column("relative_difference", sa.Numeric(), nullable=False),
        sa.Column("threshold", sa.Numeric(), nullable=False),
        sa.Column("materiality_basis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resolution_id", sa.BigInteger(), sa.ForeignKey("fin.conflict_resolutions.id")),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("dataset_id", "conflict_key", name="dataset_key"),
        sa.CheckConstraint("kind IN ('within_filing', 'mixed_vintage')", name="kind"),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'overridden', 'auto_resolved_immaterial')",
            name="status",
        ),
        sa.CheckConstraint(
            "(status IN ('resolved', 'overridden')) = (resolution_id IS NOT NULL)",
            name="resolution_iff_handled",
        ),
        sa.CheckConstraint(
            "status <> 'auto_resolved_immaterial' OR NOT is_material", name="auto_only_immaterial"
        ),
        schema="fin",
    )
    op.add_column(
        "financial_datasets", sa.Column("resolution_cutoff_id", sa.BigInteger()), schema="fin"
    )
    op.execute(
        f"ALTER TABLE fin.financial_values DROP CONSTRAINT {PROVENANCE_CK}, "
        f"ADD CONSTRAINT {PROVENANCE_CK} "
        "CHECK (provenance_kind IN ('reported', 'derived', 'analyst'))"
    )
    op.execute(
        """
        CREATE TRIGGER conflict_resolutions_append_only
        BEFORE UPDATE OR DELETE ON fin.conflict_resolutions
        FOR EACH ROW EXECUTE FUNCTION source.reject_modification();
        CREATE TRIGGER conflict_resolutions_no_truncate BEFORE TRUNCATE ON fin.conflict_resolutions
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();

        CREATE FUNCTION fin.guard_conflict() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            st text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'fin.data_conflicts rows are never updated or deleted'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            SELECT status INTO st FROM fin.financial_datasets WHERE id = NEW.dataset_id;
            IF st IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'conflicts can only be recorded on a draft dataset'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER data_conflicts_guard BEFORE INSERT OR UPDATE OR DELETE
        ON fin.data_conflicts FOR EACH ROW EXECUTE FUNCTION fin.guard_conflict();
        CREATE TRIGGER data_conflicts_no_truncate BEFORE TRUNCATE ON fin.data_conflicts
        FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification();
        """
    )
    op.execute(_guard(CONFLICT_RULE))
    op.execute(f"GRANT SELECT, INSERT ON fin.conflict_resolutions, fin.data_conflicts TO {app}")


def downgrade() -> None:
    op.execute(_guard(""))
    op.execute("DROP TABLE fin.data_conflicts")
    op.execute("DROP FUNCTION fin.guard_conflict()")
    op.drop_column("financial_datasets", "resolution_cutoff_id", schema="fin")
    op.execute("DROP TABLE fin.conflict_resolutions")
    op.execute(
        f"ALTER TABLE fin.financial_values DROP CONSTRAINT {PROVENANCE_CK}, "
        f"ADD CONSTRAINT {PROVENANCE_CK} CHECK (provenance_kind IN ('reported', 'derived'))"
    )

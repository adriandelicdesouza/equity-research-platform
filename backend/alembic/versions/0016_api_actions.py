"""Workflow action log for the API: ``audit.api_actions`` (D60).

One row per workflow action requested through the API — succeeded or refused — with the
HTTP status and the response body returned to the client. It makes results inspectable
after the fact (``GET /api/actions/{id}``) and backs idempotent retries: a unique index on
(user, ``Idempotency-Key``) means the same key can be acted on once; a retry replays the
stored response.

Append-only for every role (the generic ``source.reject_modification`` trigger), INSERT/
SELECT for the application role — like ``audit.audit_events``. Domain changes keep being
audited in ``audit.audit_events`` by the services themselves.

Downgrade is possible only while the table is empty.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-07
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.create_table(
        "api_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128)),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("outcome IN ('succeeded', 'refused')", name="outcome"),
        sa.CheckConstraint("status_code BETWEEN 200 AND 599", name="status_code"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="request_sha256"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key ~ '^[A-Za-z0-9_.:-]{8,128}$'",
            name="idempotency_key",
        ),
        schema="audit",
    )
    op.create_index(
        "uq_api_actions_idempotency",
        "api_actions",
        ["user_id", "idempotency_key"],
        unique=True,
        schema="audit",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_api_actions_user_time", "api_actions", ["user_id", "created_at"], schema="audit"
    )
    op.execute(
        "CREATE TRIGGER api_actions_append_only BEFORE UPDATE OR DELETE ON audit.api_actions "
        "FOR EACH ROW EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER api_actions_no_truncate BEFORE TRUNCATE ON audit.api_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION source.reject_modification()"
    )
    op.execute(f"GRANT SELECT, INSERT ON audit.api_actions TO {app}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM audit.api_actions) THEN
                RAISE EXCEPTION 'API action records exist; downgrade would lose them';
            END IF;
        END $$;
        """
    )
    op.drop_table("api_actions", schema="audit")

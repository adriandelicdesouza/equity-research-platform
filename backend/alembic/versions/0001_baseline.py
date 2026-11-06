"""Baseline: auth and audit schemas, roles, append-only audit log, app-role grants.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from erp_backend.auth.rbac import ROLE_DESCRIPTIONS
from erp_backend.config import get_settings
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA auth")
    op.execute("CREATE SCHEMA audit")

    op.create_table(
        "roles",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        schema="auth",
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("username = lower(username)", name="ck_users_username_lower"),
        schema="auth",
    )
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role_code", sa.String(32), sa.ForeignKey("auth.roles.code"), primary_key=True),
        schema="auth",
    )
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("user_agent", sa.String(512)),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        schema="auth",
    )
    op.create_index("ix_auth_sessions_user_id", "sessions", ["user_id"], schema="auth")

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.Column("reason", sa.Text()),
        sa.Column("request_id", sa.String(64)),
        schema="audit",
    )
    op.create_index(
        "ix_audit_audit_events_occurred_at", "audit_events", ["occurred_at"], schema="audit"
    )
    op.create_index("ix_audit_audit_events_user_id", "audit_events", ["user_id"], schema="audit")
    op.create_index(
        "ix_audit_audit_events_entity", "audit_events", ["entity_type", "entity_id"], schema="audit"
    )

    # Append-only enforcement (applies to every role, including the owner).
    op.execute(
        """
        CREATE FUNCTION audit.reject_modification() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit.audit_events is append-only (% rejected)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE "
        "ON audit.audit_events FOR EACH ROW EXECUTE FUNCTION audit.reject_modification()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE "
        "ON audit.audit_events FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_modification()"
    )

    roles = sa.table(
        "roles", sa.column("code", sa.String), sa.column("description", sa.Text), schema="auth"
    )
    op.bulk_insert(
        roles, [{"code": r.value, "description": d} for r, d in ROLE_DESCRIPTIONS.items()]
    )

    # Least-privilege grants for the application role.
    op.execute(f"GRANT USAGE ON SCHEMA auth, audit TO {app}")
    op.execute(f"GRANT SELECT ON auth.roles TO {app}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON auth.users, auth.user_roles, "
        f"auth.sessions TO {app}"
    )
    op.execute(f"GRANT SELECT, INSERT ON audit.audit_events TO {app}")


def downgrade() -> None:
    op.execute("DROP SCHEMA audit CASCADE")
    op.execute("DROP SCHEMA auth CASCADE")

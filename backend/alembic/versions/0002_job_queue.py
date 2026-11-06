"""Job queue: procrastinate 3.10.0 schema in schema ``jobs`` (D2, D41).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import op
from erp_backend.config import get_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "procrastinate_3_10_0.sql"


def _app_role() -> str:
    role = get_settings().db_app_user
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"Unsafe application role name: {role!r}")
    return role


def upgrade() -> None:
    app = _app_role()
    op.execute("CREATE SCHEMA jobs")
    op.execute("SET LOCAL search_path TO jobs")
    op.execute(SQL.read_text(encoding="utf-8"))
    op.execute("SET LOCAL search_path TO public")
    op.execute(f"GRANT USAGE ON SCHEMA jobs TO {app}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA jobs TO {app}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA jobs TO {app}")
    op.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA jobs TO {app}")


def downgrade() -> None:
    op.execute("DROP SCHEMA jobs CASCADE")

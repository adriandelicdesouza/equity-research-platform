"""Administrative CLI.

    python -m erp_backend.cli create-user --username admin --display-name "Admin" \
        --role admin --password-file /run/secrets/initial_admin_password
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from erp_backend import audit
from erp_backend.api.admin import create_user_record
from erp_backend.auth.rbac import Role
from erp_backend.db import get_db
from erp_backend.errors import ApiError


def _create_user(args: argparse.Namespace) -> int:
    if args.password_file:
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
    else:
        password = sys.stdin.readline().rstrip("\n")
    db = next(get_db())
    try:
        user = create_user_record(
            db,
            username=args.username,
            display_name=args.display_name,
            password=password,
            roles=args.role,
        )
        audit.record(
            db,
            action="user_created",
            entity_type="user",
            entity_id=str(user.id),
            after={"username": user.username, "roles": sorted(user.role_codes)},
            reason="created via CLI",
        )
        db.commit()
    except ApiError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"created user {user.username} ({', '.join(sorted(user.role_codes))})")
    return 0


def _enqueue_ping(message: str) -> int:
    from erp_backend import tasks
    from erp_backend.jobs import defer, sync_app

    with sync_app().open() as queue:
        job_id = defer(queue, tasks.ping.name, message=message)
    print(f"queued system.ping job {job_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="erp_backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user", help="Create a user (password from file or stdin).")
    cu.add_argument("--username", required=True)
    cu.add_argument("--display-name", required=True)
    cu.add_argument("--role", action="append", required=True, choices=[r.value for r in Role])
    cu.add_argument("--password-file")
    ping = sub.add_parser("enqueue-ping", help="Queue a system.ping job (queue health check).")
    ping.add_argument("--message", default="ping")
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return _create_user(args)
    if args.command == "enqueue-ping":
        return _enqueue_ping(args.message)
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

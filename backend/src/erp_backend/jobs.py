"""Background job queue on PostgreSQL (procrastinate; D2, D41).

Tables and functions live in schema ``jobs``; every queue connection uses
``search_path=jobs``. The worker uses the async connector; synchronous code (CLI, API)
defers jobs through :func:`sync_app`, which shares the same task registry.
"""

from __future__ import annotations

from typing import Any

import procrastinate
import procrastinate.tasks
import structlog
from psycopg.conninfo import make_conninfo

from erp_backend.config import Settings, get_settings

log = structlog.get_logger("erp.jobs")

DEFAULT_QUEUE = "default"
INGESTION_QUEUE = "ingestion"


def conninfo(settings: Settings, *, database: str | None = None) -> str:
    return make_conninfo(
        host=settings.db_host,
        port=settings.db_port,
        dbname=database or settings.db_name,
        user=settings.db_app_user,
        password=settings.app_password(),
        options="-c search_path=jobs",
        application_name="erp-jobs",
    )


class _LazyConnector(procrastinate.PsycopgConnector):
    """Async connector whose connection details are resolved when the pool opens."""

    def __init__(self) -> None:
        super().__init__()

    async def open_async(self, pool: Any = None) -> None:
        if pool is None:
            self._pool_args = {"conninfo": conninfo(get_settings()), "min_size": 1, "max_size": 3}
        await super().open_async(pool)


app = procrastinate.App(connector=_LazyConnector(), import_paths=["erp_backend.tasks"])


def sync_app(settings: Settings | None = None, *, database: str | None = None) -> procrastinate.App:
    s = settings or get_settings()
    return app.with_connector(
        procrastinate.SyncPsycopgConnector(conninfo=conninfo(s, database=database))
    )


def defer(queue_app: procrastinate.App, task_name: str, **task_kwargs: Any) -> int:
    """Defer ``task_name`` through ``queue_app``'s own connector.

    ``App.configure_task`` resolves the task's *original* app job manager, which would
    bypass the synchronous connector created by :func:`sync_app`; bind explicitly instead.
    """
    task = app.tasks[task_name]
    options: dict[str, Any] = {"queue": task.queue}
    if task.lock:
        options["lock"] = task.lock  # e.g. "sec": serialize jobs sharing a rate limit
    job_id: int = procrastinate.tasks.configure_task(
        name=task_name, job_manager=queue_app.job_manager, **options
    ).defer(**task_kwargs)
    return job_id

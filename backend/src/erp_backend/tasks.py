"""Background tasks. Ingestion tasks are added with their adapters (M2)."""

from __future__ import annotations

import structlog

from erp_backend.jobs import DEFAULT_QUEUE, app

log = structlog.get_logger("erp.tasks")


@app.task(name="system.ping", queue=DEFAULT_QUEUE)
def ping(message: str) -> str:
    """End-to-end queue check: enqueue → worker → completion."""
    log.info("ping", message=message)
    return message

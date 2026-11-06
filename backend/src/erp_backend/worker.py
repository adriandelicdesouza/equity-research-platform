"""Worker entry point: ``python -m erp_backend.worker`` (concurrency from settings)."""

from __future__ import annotations

import asyncio
import os

from erp_backend.config import get_settings
from erp_backend.jobs import app
from erp_backend.observability import configure_logging


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    concurrency = int(os.environ.get("ERP_WORKER_CONCURRENCY", "1"))
    async with app.open_async():
        await app.run_worker_async(concurrency=concurrency)


def _check() -> int:
    """Health check: can this process reach the queue database?"""
    from erp_backend.jobs import sync_app

    queue = sync_app()
    with queue.open():
        return 0 if queue.check_connection() else 1


def main() -> None:
    import sys

    if "--check" in sys.argv[1:]:
        raise SystemExit(_check())
    asyncio.run(_main())


if __name__ == "__main__":
    main()

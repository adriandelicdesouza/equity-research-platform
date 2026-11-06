"""Synthetic load for memory measurement (dev only).

Runs N concurrent workers for D seconds against the dev gateway: each worker logs in
(Argon2 verify), then loops over /auth/me and /health, logging in again every 20 requests.
Credentials are read from secrets/dev; nothing is printed except request counts.

    .venv/bin/python infra/scripts/loadgen.py --base http://127.0.0.1:8091 -c 8 -d 60
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]


async def worker(base: str, password: str, deadline: float, counts: collections.Counter[str]) -> None:
    origin = base.rstrip("/")
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        n = 0
        while time.monotonic() < deadline:
            if n % 20 == 0:
                r = await c.post("/api/auth/login", json={"username": "admin", "password": password},
                                 headers={"Origin": origin})
                counts[f"login {r.status_code}"] += 1
                # The session cookie is Secure; send it explicitly over plain-HTTP loopback.
                token = r.cookies.get("__Host-erp_session")
                if token:
                    c.headers["Cookie"] = f"__Host-erp_session={token}"
            path = "/api/auth/me" if n % 2 else "/api/health"
            r = await c.get(path)
            counts[f"{path} {r.status_code}"] += 1
            n += 1


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    ap.add_argument("-c", "--concurrency", type=int, default=8)
    ap.add_argument("-d", "--duration", type=int, default=60)
    a = ap.parse_args()
    password = (REPO / "secrets/dev/initial_admin_password").read_text().strip()
    counts: collections.Counter[str] = collections.Counter()
    deadline = time.monotonic() + a.duration
    await asyncio.gather(*(worker(a.base, password, deadline, counts) for _ in range(a.concurrency)))
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())

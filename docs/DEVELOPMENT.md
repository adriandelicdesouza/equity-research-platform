# Development Guide

Status: M0 Foundation. Architecture: `TECHNICAL_BLUEPRINT.md` (approved Revision 2), decisions: `DECISIONS.md`.

## Prerequisites (already on the server)

Docker + Compose v2, Python 3.12, Node 24, GNU make. Nothing is installed system-wide:
- `uv` is pinned in a project-local venv: `.tools/`, created by `make tools`.
- Python dependencies go in `.venv/`.
- Frontend dependencies go in `frontend/node_modules/`.

## Layout

```
engine/      erp_engine — pure calculation library (no I/O). Calculation modules start in M1.
backend/     erp_backend — FastAPI app, auth, audit; Alembic migrations in backend/alembic/
frontend/    React 19 + TypeScript + Vite
infra/       compose.yaml (+ dev/prod overrides), Dockerfiles, Caddyfile, postgres init, scripts
docs/        development, operations (memory measurements)
data/<env>/  runtime data (git-ignored): postgres/, caddy/
secrets/<env>/  generated passwords (git-ignored, dir 0700)
```

## First-time setup (dev)

```bash
make setup         # uv sync (from uv.lock) + npm ci (from package-lock.json)
make up-dev        # creates .env.dev, secrets/dev/*, data/dev/*, builds and starts erp-dev
make create-admin-dev   # user 'admin', password in secrets/dev/initial_admin_password
```

Dev endpoints, all on loopback only:

| URL | What |
|---|---|
| `http://localhost:8091` | App via Caddy (HTTP) |
| `https://localhost:8444` or `https://127.0.0.1:8444` | App via Caddy (HTTPS, Caddy internal CA) |
| `127.0.0.1:5433` | PostgreSQL, for tests and tooling |
| `http://localhost:5173` | Optional Vite dev server (`cd frontend && npm run dev`); proxies `/api` to 8091 |
| `http://localhost:8091/api/docs` | OpenAPI UI (disabled in prod) |

To trust the HTTPS listener in a browser, import Caddy's root CA. Copy it out with:
```bash
docker cp erp-dev-web-1:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

## Checks

```bash
make check         # ruff, ruff format --check, eslint, mypy --strict, tsc, pytest, vitest
make test-backend  # needs the dev DB; uses a disposable 'erp_test' database
```

Backend tests:
- They create and drop only databases named `erp_test*`, using the dev superuser.
- Each test runs inside a rolled-back transaction.

## Independent reference valuation (M1)

`engine/tests/reference/` is the independent DCF oracle (Blueprint §13.1.1, D26).
- **Source:** written only from `docs/methodology/dcf.md`.
- **Arithmetic:** exact `Fraction` throughout; `mpmath` at 50 digits for fractional exponents.
- **Independence:** it never imports `erp_engine`, `erp_backend` or `decimal`. Two checks enforce this:
  - the import-linter contracts in `pyproject.toml`, run by `make lint`;
  - `engine/tests/test_reference_independence.py`.
- **Benchmarks:** the fixtures in `engine/tests/fixtures/benchmarks/` hold the methodology document's benchmarks B1, B1-VD, B2, B3 and B4.

## Database access model

| Role | Used by | Rights |
|---|---|---|
| `postgres` | container bootstrap, test harness only | superuser |
| `erp_owner` | Alembic migrations (`migrate` service) | owns database and schemas; not superuser |
| `erp_app` | API at runtime | DML on `auth.*`; **INSERT/SELECT only** on `audit.audit_events`; cannot create objects |

`audit.audit_events` also has triggers rejecting UPDATE, DELETE and TRUNCATE for every role.

## Migrations

```bash
make up-dev     # the 'migrate' service runs `alembic upgrade head` before the API starts
```

New migration: add a file under `backend/alembic/versions/`. Grant `erp_app` explicit privileges in the same migration.

## Reproducibility

- Python: `uv.lock`, resolved with `exclude-newer` (see root `pyproject.toml`).
- Frontend: `package-lock.json`, exact versions, installed with `npm install --before=<date>`.
- uv 0.13.7 is pinned in both the `.tools/` venv and the image build.
- Images: `python:3.12-slim-bookworm`, `postgres:17-alpine`, `caddy:2-alpine`, `node:24-alpine`. Pinning image digests is part of M5.

## Memory measurement

```bash
make measure-memory LABEL="description"            # cgroup current/peak/limit per container
.venv/bin/python infra/scripts/loadgen.py -c 8 -d 60   # synthetic load (dev)
```

Results are recorded in `docs/operations/memory.md`, kept separate from the estimates.

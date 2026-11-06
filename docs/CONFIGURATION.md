# Configuration Reference

Configuration has two layers:

1. **Application settings**: read by the Python backend and worker from environment variables prefixed `ERP_` (`backend/src/erp_backend/config.py`).
2. **Deployment variables**: read by Docker Compose from a per-environment env file (`.env.dev` / `.env.prod`, created from `infra/env/*.env.example`). Compose passes the relevant subset to the containers.

**Secrets are never placed in env files.** Database passwords are generated into files under `secrets/<env>/` (`make secrets-dev`, mode 0644 inside a 0700 directory) and mounted as Docker secrets. `.env*` and `secrets/` are git-ignored. The values below are illustrative placeholders.

## 1. Application settings (23)

| Variable | Required | Default | Format | Purpose |
|---|---|---|---|---|
| `ERP_ENV` | no | `dev` | `dev` \| `test` \| `prod` | Environment. `prod` forbids inline passwords and disables the OpenAPI UI. |
| `ERP_LOG_LEVEL` | no | `INFO` | Python level name | Log verbosity (structured JSON to stdout). |
| `ERP_DB_HOST` | no | `db` | hostname | PostgreSQL host (the Compose service name inside the stack). |
| `ERP_DB_PORT` | no | `5432` | integer | PostgreSQL port. |
| `ERP_DB_NAME` | no | `erp` | identifier | Application database name. |
| `ERP_DB_APP_USER` | no | `erp_app` | role name | Least-privilege runtime role (DML only; append-only audit and source tables). |
| `ERP_DB_APP_PASSWORD_FILE` | **yes** (api, worker, migrate) | none | file path | Path to the runtime role's password file, e.g. `/run/secrets/db_app_password`. |
| `ERP_DB_APP_PASSWORD` | no | none | string | Inline password. **For the local test harness only; rejected when `ERP_ENV=prod`.** |
| `ERP_DB_OWNER_USER` | no | `erp_owner` | role name | Schema-owner role used only by migrations. |
| `ERP_DB_OWNER_PASSWORD_FILE` | **yes** (migrate only) | none | file path | Path to the owner role's password file, e.g. `/run/secrets/db_owner_password`. |
| `ERP_DB_OWNER_PASSWORD` | no | none | string | Inline owner password. Test harness only; rejected in prod. |
| `ERP_DB_POOL_SIZE` | no | `5` | integer ≥ 1 | SQLAlchemy connection pool size per process. |
| `ERP_DB_MAX_OVERFLOW` | no | `3` | integer ≥ 0 | Extra connections allowed above the pool size. |
| `ERP_ALLOWED_ORIGINS` | **yes** (api) | empty | comma-separated origins | Exact origins allowed to send state-changing requests (CSRF Origin check), e.g. `http://localhost:8091,https://localhost:8444`. With an empty value every unsafe request is rejected. |
| `ERP_COOKIE_NAME` | no | `__Host-erp_session` | must start with `__Host-` | Session cookie name. Always `Secure; HttpOnly; SameSite=Strict`. |
| `ERP_SESSION_IDLE_MINUTES` | no | `60` | integer minutes | Idle timeout for server-side sessions. |
| `ERP_SESSION_ABSOLUTE_HOURS` | no | `12` | integer hours | Absolute session lifetime. |
| `ERP_LOGIN_MAX_ATTEMPTS` | no | `5` | integer | Failed logins allowed per window, per (IP, username) and per username. |
| `ERP_LOGIN_WINDOW_SECONDS` | no | `900` | integer seconds | Sliding window for the login rate limit. |
| `ERP_RAW_ROOT` | no | `/data/raw` | directory path | Root of the content-addressed raw provider payload archive (worker). |
| `ERP_MAPPINGS_ROOT` | no | `/app/mappings` | directory path | Root of the versioned concept-mapping YAML (`mappings/` in the repository, copied into the image). Read by migrations and standardization. |
| `ERP_MAPPING_VERSION` | no | `v2` | `v<n>` | Mapping version used for new dataset builds (D49, D51). Loaded versions: v1, v2. |
| `ERP_SEC_USER_AGENT` | **yes** for SEC ingestion | none | `"<name or organisation> <contact email>"` | Contact User-Agent that the SEC requires. Placeholders and reserved domains are rejected; without it, no SEC request can be made. Set only in the git-ignored env file. |

Process-level variables (not part of `Settings`):

| Variable | Default | Purpose |
|---|---|---|
| `ERP_WORKER_CONCURRENCY` | `1` | Concurrent jobs per worker process. |
| `MALLOC_ARENA_MAX` | `2` (set in Compose for api/worker) | Limits glibc malloc arenas to keep memory retained after load low (D33). |

## 2. Deployment variables (Compose env file)

| Variable | Example (dev) | Purpose |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `erp-dev` | Isolates dev and prod stacks (separate containers, networks, volumes). |
| `ERP_IMAGE_TAG` | `dev` | Tag for the locally built `erp-app` and `erp-web` images. |
| `ERP_REPO` | `/path/to/equity-research-platform` | Absolute repository path (filled in by `make env-dev`). |
| `ERP_DATA_ROOT` | `<repo>/data/dev` | Runtime data (PostgreSQL files, Caddy data, raw archive). Git-ignored. |
| `ERP_SECRETS_DIR` | `<repo>/secrets/dev` | Directory holding generated password files. Git-ignored. |
| `ERP_UID` / `ERP_GID` | `1000` | Host user/group used by containers that write bind-mounted data (web, worker). |
| `ERP_SUBNET` / `ERP_WEB_IP` | `172.30.90.0/24` / `172.30.90.10` | Private Docker network and the gateway's fixed address (trusted proxy for the API). Choose a subnet unused on the host. |
| `ERP_HTTP_BIND` | `127.0.0.1:8091` | Host binding of the gateway's plain-HTTP listener (loopback only). |
| `ERP_HTTPS_BIND` | `127.0.0.1:8444` | Host binding of the HTTPS listener (internal CA). In prod, a private-network address. |
| `ERP_DB_BIND` | `127.0.0.1:5433` | Dev only: loopback port for tests and tooling. Prod publishes no database port. |
| `ERP_TLS_SITES` | `https://localhost:8443, https://127.0.0.1:8443` | Site addresses served with Caddy's internal CA. |
| `ERP_TLS_DEFAULT_SNI` | `127.0.0.1` | Certificate name for clients connecting by IP without SNI (D30). |
| `ERP_ALLOWED_ORIGINS` | see above | Passed to the API. |
| `ERP_POSTGRES_IMAGE` | `postgres:17-alpine` | Database image. |
| `ERP_DB_MEM` / `ERP_API_MEM` / `ERP_WORKER_MEM` / `ERP_WEB_MEM` | `576m` / `384m` / `576m` / `48m` | Container hard memory limits (Blueprint §1.2). |
| `ERP_DB_CPUS` / `ERP_API_CPUS` / `ERP_WORKER_CPUS` | `1.0` / `1.5` / `1.0` | CPU limits (optional; defaults in `infra/compose.yaml`). |
| `ERP_SEC_USER_AGENT` | *(empty in examples)* | Forwarded to the worker only. |

## 3. Generated secrets (`secrets/<env>/`)

| File | Used by |
|---|---|
| `pg_superuser_password` | PostgreSQL bootstrap and the local test harness only |
| `db_owner_password` | Migrations (`migrate` service) |
| `db_app_password` | API and worker |
| `initial_admin_password` | `make create-admin-dev` (first application user) |

Generate them with `infra/scripts/init-secrets.sh <env>`. Existing files are never overwritten.

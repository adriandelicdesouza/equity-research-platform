# Equity Research Platform — developer entry points. See docs/DEVELOPMENT.md.
SHELL := /bin/bash
REPO  := $(abspath .)
UV    := $(REPO)/.tools/bin/uv
PY    := $(REPO)/.venv/bin/python
DEV   := docker compose --env-file .env.dev -f infra/compose.yaml -f infra/compose.dev.yaml

.PHONY: help tools setup env-dev secrets-dev data-dev up-dev down-dev ps-dev logs-dev \
        build-dev create-admin-dev lint typecheck test test-backend test-engine test-frontend \
        check measure-memory

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | sort | tr '\n' ' '; echo

tools:            ## project-local uv (no system changes)
	@test -x $(UV) || (python3 -m venv .tools && .tools/bin/pip install -q uv==0.13.7)

setup: tools      ## Python + frontend dependencies from lock files
	$(UV) sync --all-packages --frozen
	cd frontend && npm ci --no-audit --no-fund

env-dev:
	@test -f .env.dev || sed -e 's#__REPO__#$(REPO)#g' -e "s#__UID__#$$(id -u)#" \
	  -e "s#__GID__#$$(id -g)#" infra/env/dev.env.example > .env.dev && echo ".env.dev ready"

secrets-dev:
	infra/scripts/init-secrets.sh dev

data-dev:
	mkdir -p data/dev/postgres data/dev/caddy/data data/dev/caddy/config

build-dev: env-dev
	$(DEV) build

up-dev: env-dev secrets-dev data-dev
	$(DEV) up -d --build --wait

down-dev:
	$(DEV) down

ps-dev:
	$(DEV) ps

logs-dev:
	$(DEV) logs --tail=100

create-admin-dev:  ## creates 'admin' with secrets/dev/initial_admin_password
	$(DEV) run --rm --no-deps -v $(REPO)/secrets/dev/initial_admin_password:/run/secrets/initial_admin_password:ro \
	  api python -m erp_backend.cli create-user --username admin --display-name "Administrator" \
	  --role admin --password-file /run/secrets/initial_admin_password

lint:
	$(UV) run --all-packages ruff check engine backend
	$(UV) run --all-packages ruff format --check engine backend
	cd frontend && npm run lint

typecheck:
	$(UV) run --all-packages mypy engine/src backend/src
	cd frontend && npm run typecheck

test-engine:
	$(UV) run --all-packages pytest engine/tests -q

test-backend:     ## needs the dev database (make up-dev)
	$(UV) run --all-packages pytest backend/tests -q

test-frontend:
	cd frontend && npm test

test: test-engine test-backend test-frontend

check: lint typecheck test

measure-memory:   ## usage: make measure-memory LABEL=idle
	infra/scripts/measure-memory.sh $(or $(LABEL),snapshot)

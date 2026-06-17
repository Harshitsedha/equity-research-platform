# Phase 0 task runner. Requires: uv, docker.
# DATABASE_URL can be overridden; defaults to the docker-compose Postgres.
DATABASE_URL ?= postgresql+psycopg://research:research@localhost:5434/research
export DATABASE_URL

.PHONY: help install up down migrate downgrade test test-domain lint e2e fmt

help:
	@echo "Targets:"
	@echo "  install   - uv sync (create venv, install deps)"
	@echo "  up        - start PostgreSQL (docker-compose) and wait until healthy"
	@echo "  down      - stop PostgreSQL"
	@echo "  migrate   - alembic upgrade head (tables + immutability triggers)"
	@echo "  lint      - import-linter boundary contracts"
	@echo "  test      - import-linter + full pytest suite (needs DB up + migrated)"
	@echo "  e2e       - run the Phase 0 end-to-end script"

install:
	uv sync

up:
	docker compose up -d db redis
	@echo "waiting for postgres to become healthy..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' erp_postgres 2>/dev/null)" = "healthy" ]; do sleep 1; done
	@echo "postgres is healthy (redis broker also started)"

down:
	docker compose down

migrate:
	uv run alembic upgrade head

downgrade:
	uv run alembic downgrade base

lint:
	uv run lint-imports

test-domain:
	uv run pytest tests/domain -q

test: lint
	uv run pytest -q

e2e:
	uv run python scripts/phase0_e2e.py

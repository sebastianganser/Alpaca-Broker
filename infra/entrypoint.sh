#!/bin/sh
# =============================================================================
# Alpaca-Broker – Entrypoint Script
# =============================================================================
# 1) Wait for PostgreSQL to be ready
# 2) Run Alembic migrations
# 3) Execute the main CMD (FastAPI + Scheduler)

set -e

echo "==> Waiting for database at ${DB_HOST:-localhost}:${DB_PORT:-5432}..."
until pg_isready -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -q; do
    sleep 2
done
echo "==> Database is ready."

echo "==> Running Alembic migrations..."
uv run alembic upgrade head || echo "WARN: Alembic migration failed (may be first run)"

echo "==> Starting Alpaca-Broker (FastAPI + Scheduler)..."
exec "$@"

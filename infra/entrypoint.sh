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
# Fail hard: starting the app on a half-migrated / outdated schema corrupts
# data silently. The container restarts (restart: unless-stopped) and retries.
.venv/bin/alembic upgrade head || { echo "ERROR: Alembic migration failed - aborting startup"; exit 1; }

echo "==> Starting Alpaca-Broker (FastAPI + Scheduler)..."
exec "$@"

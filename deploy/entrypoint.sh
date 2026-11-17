#!/usr/bin/env bash
set -euo pipefail

: "${PEAWORK_DATABASE_URL:?PEAWORK_DATABASE_URL is required}"

echo "Applying database migrations..."
alembic upgrade head

echo "Ensuring organisation, administrator and reference data..."
python -m peawork init

echo "Starting PeaWork HRMS..."
exec gunicorn \
  --workers "${WEB_CONCURRENCY:-2}" \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT:-8090}" \
  --timeout "${WEB_TIMEOUT:-60}" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  peawork.main:app

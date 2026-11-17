#!/usr/bin/env bash
# PeaWork HRMS backup: PostgreSQL custom-format dump + private document volume.
#
# Usage:
#   sudo -u peawork ./deploy/backup.sh
#   PEAWORK_ENV_FILE=/etc/peawork/peawork.env ./deploy/backup.sh
#
# Restore outline:
#   1. Stop the application.
#   2. Strip the SQLAlchemy driver suffix from PEAWORK_DATABASE_URL, then:
#      pg_restore --clean --if-exists -d "$PG_URL" database.dump
#   3. tar -xzf documents.tar.gz -C /var/lib/peawork
#   4. Start the application and run `python -m peawork check`.
#
# This script targets native/systemd installs. For Docker Compose, run pg_dump
# inside the `db` service and archive the `peawork_documents` volume.
set -euo pipefail

ENV_FILE="${PEAWORK_ENV_FILE:-/etc/peawork/peawork.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

BACKUP_DIR="${PEAWORK_BACKUP_DIR:-/var/backups/peawork}"
RETENTION_DAYS="${PEAWORK_BACKUP_RETENTION_DAYS:-14}"
STORAGE_DIR="${PEAWORK_STORAGE_DIR:-/var/lib/peawork/documents}"
DATABASE_URL="${PEAWORK_DATABASE_URL:-}"

if [[ -z "$DATABASE_URL" ]]; then
  echo "PEAWORK_DATABASE_URL is not set" >&2
  exit 1
fi
if [[ ! -d "$STORAGE_DIR" ]]; then
  echo "Document storage not found: $STORAGE_DIR" >&2
  exit 1
fi
if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump is required (install postgresql-client)" >&2
  exit 1
fi

# libpq accepts a postgresql:// URI; strip the SQLAlchemy driver suffix.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql:\/\/}"
PG_URL="${PG_URL/postgresql+psycopg2:\/\//postgresql:\/\/}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="${BACKUP_DIR}/peawork-${STAMP}"
mkdir -p "$WORK_DIR"
chmod 0700 "$BACKUP_DIR" "$WORK_DIR"

echo "Backing up database..."
pg_dump --format=custom --no-owner --no-acl --file="${WORK_DIR}/database.dump" "$PG_URL"

echo "Backing up documents..."
tar -czf "${WORK_DIR}/documents.tar.gz" -C "$(dirname "$STORAGE_DIR")" "$(basename "$STORAGE_DIR")"

echo "Writing checksums..."
(
  cd "$WORK_DIR"
  sha256sum database.dump documents.tar.gz > SHA256SUMS
)

echo "Pruning backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'peawork-*' \
  -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "Backup complete: $WORK_DIR"

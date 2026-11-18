"""Cron job for scheduled backups and DR maintenance.

Usage:
  python -m peawork.backup_cron --label=auto-daily

Or with systemd timers / cron:
  0 2 * * * cd /path/to/peawork && python -m peawork.backup_cron --label=daily-$(date +%Y%m%d)

Runs:
1. Create backup if PEAWORK_BACKUP_ENABLED=true
2. Replicate to object storage if PEAWORK_BACKUP_CROSS_REGION=true
3. Clean up old backups older than PEAWORK_BACKUP_RETENTION_DAYS
4. Report PITR status if PEAWORK_PITR_ENABLED=true
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backup import (
    create_backup,
    delete_backup,
    list_backups,
    pitr_status,
)
from .config import get_settings

log = logging.getLogger("peawork.backup_cron")


def _cleanup_old_backups(retention_days: int) -> list[str]:
    """Delete backups older than retention_days."""
    deleted = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for backup in list_backups():
        try:
            created = datetime.fromisoformat(backup["created_at"])
            if created < cutoff:
                delete_backup(backup["filename"])
                deleted.append(backup["filename"])
                log.info("Deleted expired backup: %s (created %s)",
                         backup["filename"], backup["created_at"])
        except Exception as exc:
            log.warning("Failed to check/delete backup %s: %s",
                        backup["filename"], exc)
    return deleted


def run_backup_job(label: str = "auto") -> dict:
    """Run a full backup job: create + cleanup + report."""
    settings = get_settings()
    if not settings.backup_enabled:
        log.info("Backups disabled (PEAWORK_BACKUP_ENABLED=false), skipping")
        return {"status": "skipped", "reason": "backup_disabled"}

    log.info("Starting backup job (label=%s)", label)
    try:
        result = create_backup(label=label)
        log.info("Backup created: %s (%d bytes, checksum=%s)",
                 result["filename"], result["size"], result["checksum"][:8])
    except Exception as exc:
        log.exception("Backup creation failed")
        return {"status": "error", "error": str(exc)}

    # Cleanup old backups
    deleted = _cleanup_old_backups(settings.backup_retention_days)
    if deleted:
        log.info("Cleaned up %d expired backup(s)", len(deleted))

    # PITR status
    pitr_info = None
    if settings.pitr_enabled:
        pitr_info = pitr_status()
        log.info("PITR: archived_wal_files=%d, retention_days=%d",
                 pitr_info["archived_wal_files"], pitr_info["retention_days"])

    return {
        "status": "ok",
        "backup": result,
        "cleaned_up": deleted,
        "pitr": pitr_info,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="PeaWork HRMS backup cron job")
    parser.add_argument("--label", default="auto", help="Label for backup filename")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    result = run_backup_job(label=args.label)
    print(f"Backup job result: {result}")
    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
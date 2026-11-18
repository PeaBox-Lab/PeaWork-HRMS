"""Disaster Recovery & Backup utilities for PeaWork HRMS.

Provides:
- Full backup / restore of the database (PostgreSQL or SQLite).
- PITR (Point-In-Time Recovery) status and WAL archive helpers.
- Cross-region backup replication via object storage.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup_dir() -> Path:
    settings = get_settings()
    base = Path(settings.backup_object_storage_bucket or "./data/backups")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _wal_archive_dir() -> Path | None:
    """Get WAL archive directory if PITR is enabled."""
    settings = get_settings()
    if not settings.pitr_enabled or not settings.pitr_wal_archive_dir:
        return None
    wal_dir = Path(settings.pitr_wal_archive_dir)
    wal_dir.mkdir(parents=True, exist_ok=True)
    return wal_dir


def list_backups() -> list[dict[str, Any]]:
    """Return a list of backup files with metadata."""
    base = _backup_dir()
    result = []
    for f in sorted(base.glob("backup_*.sql*")):
        result.append(
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "checksum": _sha256(f),
                "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return result


def create_backup(label: str | None = None) -> dict[str, Any]:
    """Create a database backup.

    - For SQLite: copies the database file.
    - For PostgreSQL: uses pg_dump if available.
    - Compresses with gzip when ``backup_compression == 'gzip'``.
    """
    settings = get_settings()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label_part = f"_{label}" if label else ""
    base = _backup_dir()
    sqlite = settings.database_url.startswith("sqlite")

    if sqlite:
        # database_url looks like sqlite:///./data/peawork_v2.db
        db_path_str = settings.database_url.split("///")[-1]
        db_path = Path(db_path_str).expanduser().resolve()
        if not db_path.exists():
            raise FileNotFoundError(f"Database file not found: {db_path}")
        raw = db_path.read_bytes()
    else:
        # PostgreSQL: use pg_dump
        try:
            result = subprocess.run(
                ["pg_dump", "--no-owner", "--no-acl", settings.database_url],
                capture_output=True,
                check=True,
                timeout=300,
            )
            raw = result.stdout
        except FileNotFoundError:
            raise RuntimeError(
                "pg_dump not found. Install PostgreSQL client tools or use SQLite for dev."
            ) from None

    filename = f"backup_{ts}{label_part}.sql"
    if settings.backup_compression == "gzip":
        filename += ".gz"
        data = gzip.compress(raw)
    else:
        data = raw

    path = base / filename
    path.write_bytes(data)

    backup = {
        "filename": filename,
        "size": path.stat().st_size,
        "checksum": _sha256(path),
        "created_at": _now_iso(),
        "compression": settings.backup_compression,
    }

    # Cross-region replication
    if settings.backup_cross_region and settings.object_storage_enabled:
        try:
            from . import minio_s3_module as storage

            storage.put_object(f"backups/{filename}", data, "application/octet-stream")
            backup["replicated"] = True
        except Exception:
            backup["replicated"] = False

    return backup


def restore_backup(filename: str) -> dict[str, Any]:
    """Restore a database from a backup file."""
    settings = get_settings()
    path = _backup_dir() / filename
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {filename}")

    data = path.read_bytes()
    if filename.endswith(".gz"):
        data = gzip.decompress(data)

    sqlite = settings.database_url.startswith("sqlite")
    if sqlite:
        db_path_str = settings.database_url.split("///")[-1]
        db_path = Path(db_path_str).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(data)
        return {"status": "restored", "target": str(db_path), "timestamp": _now_iso()}

    # PostgreSQL restore
    try:
        subprocess.run(
            ["psql", settings.database_url],
            input=data,
            capture_output=True,
            check=True,
            timeout=600,
        )
    except FileNotFoundError:
        raise RuntimeError("psql not found. Install PostgreSQL client tools.") from None
    return {"status": "restored", "target": "postgresql", "timestamp": _now_iso()}


def delete_backup(filename: str) -> None:
    """Delete a backup file."""
    path = _backup_dir() / filename
    if path.exists():
        path.unlink()


def pitr_status() -> dict[str, Any]:
    """Return PITR status information."""
    settings = get_settings()
    wal_dir = _wal_archive_dir()
    archived = 0
    if wal_dir and wal_dir.exists():
        archived = len(list(wal_dir.glob("*.wal")))
    return {
        "enabled": settings.pitr_enabled,
        "wal_archive_dir": str(wal_dir) if wal_dir else None,
        "archived_wal_files": archived,
        "retention_days": settings.pitr_retention_days,
        "recovery_target_time": settings.pitr_recovery_target_time,
        "timestamp": _now_iso(),
    }


def cleanup_old_backups(retention_days: int | None = None) -> list[str]:
    """Delete backups older than retention_days.

    Uses PEAWORK_BACKUP_RETENTION_DAYS if not specified.
    """
    settings = get_settings()
    days = retention_days or settings.backup_retention_days
    deleted = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for backup in list_backups():
        try:
            created = datetime.fromisoformat(backup["created_at"])
            if created < cutoff:
                delete_backup(backup["filename"])
                deleted.append(backup["filename"])
        except Exception:
            pass
    return deleted


def cleanup_old_wal_files(retention_days: int | None = None) -> int:
    """Delete WAL archive files older than retention_days.

    Uses PEAWORK_PITR_RETENTION_DAYS if not specified.
    """
    settings = get_settings()
    days = retention_days or settings.pitr_retention_days
    wal_dir = _wal_archive_dir()
    if not wal_dir or not wal_dir.exists():
        return 0

    deleted = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for wal_file in wal_dir.glob("*.wal"):
        try:
            mtime = datetime.fromtimestamp(wal_file.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                wal_file.unlink()
                deleted += 1
        except Exception:
            pass
    return deleted


def pitr_recover(
    backup_filename: str,
    target_time: str | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Prepare PITR recovery configuration.

    This function creates a recovery configuration that can be used to restore
    a PostgreSQL database to a specific point in time.

    Steps:
    1. Restore the base backup from backup_filename
    2. Create recovery.conf / recovery.signal with target time
    3. Start PostgreSQL which will replay WAL files up to target_time

    Note: This requires PostgreSQL with archive_mode=on and a configured
    archive_command. This is a helper that prepares the recovery files;
    actual recovery happens when PostgreSQL restarts.
    """
    settings = get_settings()
    if not settings.pitr_enabled:
        raise RuntimeError("PITR is not enabled. Set PEAWORK_PITR_ENABLED=true")

    target = target_time or settings.pitr_recovery_target_time
    if not target:
        raise ValueError("target_time is required for PITR recovery")

    wal_dir = _wal_archive_dir()
    if not wal_dir or not wal_dir.exists():
        raise FileNotFoundError(
            f"WAL archive directory not found: {wal_dir}. "
            "Ensure archive_mode is on and archive_command is configured."
        )

    # Verify the base backup exists
    backup_path = _backup_dir() / backup_filename
    if not backup_path.exists():
        raise FileNotFoundError(f"Base backup not found: {backup_filename}")

    # Create recovery configuration (for PostgreSQL 12+: recovery.signal + settings)
    recovery_dir = Path(output_dir) if output_dir else Path("./data/recovery")
    recovery_dir.mkdir(parents=True, exist_ok=True)

    recovery_conf = recovery_dir / "postgresql.auto.conf.recovery"
    recovery_conf.write_text(
        f"# PITR recovery configuration\n"
        f"# Generated at {_now_iso()}\n"
        f"recovery_target_time = '{target}'\n"
        f"restore_command = 'cp {wal_dir}/%f %p'\n"
        f"recovery_target_action = 'promote'\n"
    )

    # Create a marker file that PostgreSQL will detect
    signal_file = recovery_dir / "recovery.signal"
    if not signal_file.exists():
        signal_file.write_text("")

    return {
        "status": "prepared",
        "backup_file": backup_filename,
        "target_time": target,
        "wal_archive_dir": str(wal_dir),
        "recovery_dir": str(recovery_dir),
        "recovery_config": recovery_conf.name,
        "signal_file": signal_file.name,
        "wal_files_available": len(list(wal_dir.glob("*.wal"))),
        "next_steps": [
            f"1. Stop PostgreSQL: systemctl stop postgresql",
            f"2. Restore base backup: pg_restore -d {recovery_dir} {backup_path}",
            f"3. Copy {recovery_conf.name} to PG_DATA/postgresql.auto.conf",
            f"4. Copy {signal_file.name} to PG_DATA/recovery.signal",
            f"5. Start PostgreSQL: systemctl start postgresql",
            f"6. Verify recovery: SELECT pg_is_in_recovery();",
        ],
        "timestamp": _now_iso(),
    }


def sha256_of_file(filename: str) -> str:
    """Return the SHA-256 checksum for a backup file."""
    return _sha256(_backup_dir() / filename)


__all__ = [
    "list_backups",
    "create_backup",
    "restore_backup",
    "delete_backup",
    "pitr_status",
    "pitr_recover",
    "cleanup_old_backups",
    "cleanup_old_wal_files",
    "sha256_of_file",
]
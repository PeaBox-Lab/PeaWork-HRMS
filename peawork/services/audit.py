from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from ..domain import AuditAction
from ..models import AuditEvent, User

REDACTED_KEYS = {
    "password",
    "password_hash",
    "token",
    "csrf",
    "pan",
    "pan_masked",
    "bank",
    "bank_masked",
    "salary",
    "ctc",
    "amount",
    "secret",
}


def _jsonable(value: Any) -> Any:
    """Return a JSON-serialisable copy of audit detail values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _redact(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if not details:
        return None
    clean: dict[str, Any] = {}
    for key, value in details.items():
        clean[key] = "***" if key.lower() in REDACTED_KEYS else _jsonable(value)
    return clean


def record(
    db: Session,
    *,
    actor: User | None,
    action: AuditAction,
    entity_type: str,
    entity_id: str | UUID | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
    org_id: UUID | None = None,
) -> AuditEvent:
    event = AuditEvent(
        org_id=org_id or (actor.org_id if actor else None),
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=_redact(details),
        ip=getattr(request.state, "client_ip", None) if request else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
    )
    db.add(event)
    db.flush()
    return event

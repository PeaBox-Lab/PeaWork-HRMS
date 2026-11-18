"""Employee lifecycle change-request service (R5 candidate / lifecycle module).

Handles effective-dated change requests for:
resignation, promotion, position/location/department changes, manager change,
employment-type change, contract change, rehire, group-company transfer,
country transfer, legal entity change, project assignment, cost-center change,
timesheet adjustment, leave request, attendance adjustment.

Workflow per request:
  draft -> pending_approval -> approved/rejected/cancelled -> completed

On approval, ``apply`` mutates the target ``Employee`` and appends an
``EmployeeHistory`` row with matching ``change_type``.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from ..domain import AuditAction, ChangeRequestType, ChangeRequestStatus
from ..models import (
    ChangeRequest,
    Employee,
    EmployeeHistory,
    User,
)
from . import audit
from .errors import ConflictError, NotFoundError, ValidationError


def _utcnow() -> datetime:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc)


def _to_change_type(value: str | ChangeRequestType) -> ChangeRequestType:
    """Convert string or enum to ChangeRequestType enum."""
    if isinstance(value, ChangeRequestType):
        return value
    try:
        return ChangeRequestType(value)
    except ValueError:
        raise ValidationError(f"Invalid change_type: {value}. Valid: {[e.value for e in ChangeRequestType]}")


def create_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    code: str,
    change_type: str | ChangeRequestType,
    employee_id: UUID | None = None,
    effective_date: date | None = None,
    description: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Create a new lifecycle change request.

    Returns a dict with keys: id, org_id, code, employee_id, change_type,
    status, effective_date, description, old_value, new_value, notes, actor_id, approved_by, approved_at, created_at.
    """
    change_type_enum = _to_change_type(change_type)

    existing = db.scalar(
        select(ChangeRequest).where(
            ChangeRequest.org_id == org_id, ChangeRequest.code == code
        )
    )
    if existing is not None:
        raise ConflictError(f"Change request code '{code}' already exists in {org_id}")

    cr = ChangeRequest(
        org_id=org_id,
        code=code,
        employee_id=employee_id,
        change_type=change_type_enum,
        status=ChangeRequestStatus.DRAFT,
        effective_date=effective_date or date.today(),
        description=description,
        old_value=old_value,
        new_value=new_value,
        notes=notes,
        actor_id=actor.id,
    )
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"code": code, "change_type": change_type_enum.value},
        org_id=org_id,
    )
    return {
        "id": str(cr.id),
        "org_id": str(org_id),
        "code": code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": change_type_enum.value,
        "status": cr.status.value,
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": description,
        "old_value": old_value,
        "new_value": new_value,
        "notes": notes,
        "reason": None,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": None,
        "approved_at": None,
        "created_at": cr.created_at.isoformat() if cr.created_at else None,
    }


def list_change_requests(
    db: Session,
    *,
    org_id: UUID,
    user: User,
    status: str | None = None,
    change_type: str | None = None,
    employee_id: str | None = None,
) -> list[dict]:
    stmt = select(ChangeRequest).where(ChangeRequest.org_id == org_id)
    if user.role in {"admin", "hr"}:
        pass
    elif user.role == "manager" and user.employee_id:
        stmt = stmt.where(ChangeRequest.employee_id == user.employee_id)
    elif user.employee_id:
        stmt = stmt.where(ChangeRequest.employee_id == user.employee_id)
    else:
        stmt = stmt.where(false())
    if status:
        stmt = stmt.where(ChangeRequest.status == status)
    if change_type:
        stmt = stmt.where(ChangeRequest.change_type == change_type)
    if employee_id:
        stmt = stmt.where(ChangeRequest.employee_id == employee_id)

    rows = db.execute(stmt).scalars().all()
    result = []
    for cr in rows:
        result.append({
            "id": str(cr.id),
            "org_id": str(cr.org_id),
            "code": cr.code,
            "employee_id": str(cr.employee_id) if cr.employee_id else None,
            "change_type": cr.change_type,
            "status": cr.status,
            "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
            "description": cr.description,
            "old_value": cr.old_value,
            "new_value": cr.new_value,
            "notes": cr.notes,
            "actor_id": str(cr.actor_id) if cr.actor_id else None,
            "approved_by": str(cr.approved_by) if cr.approved_by else None,
            "approved_at": str(cr.approved_at) if cr.approved_at else None,
            "created_at": str(cr.created_at) if cr.created_at else None,
        })
    return result


def get_change_request(db: Session, *, org_id: UUID, cr_id: str) -> dict:
    cr = db.get(ChangeRequest, UUID(cr_id))
    if cr is None or cr.org_id != org_id:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}")
    return {
        "id": str(cr.id),
        "org_id": str(cr.org_id),
        "code": cr.code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": cr.change_type,
        "status": cr.status,
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": cr.description,
        "old_value": cr.old_value,
        "new_value": cr.new_value,
        "notes": cr.notes,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": str(cr.approved_by) if cr.approved_by else None,
        "approved_at": str(cr.approved_at) if cr.approved_at else None,
        "created_at": str(cr.created_at) if cr.created_at else None,
    }


def approve_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
) -> dict:
    cr = db.get(ChangeRequest, UUID(cr_id))
    if cr is None or cr.org_id != org_id:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}")
    if cr.status == ChangeRequestStatus.COMPLETED:
        raise ValidationError(f"Change request {cr_id} already completed")
    if cr.status == ChangeRequestStatus.CANCELLED:
        raise ValidationError(f"Change request {cr_id} already cancelled")

    cr.status = ChangeRequestStatus.APPROVED
    cr.approved_by = actor.id
    cr.approved_at = _utcnow()
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"status": ChangeRequestStatus.APPROVED.value},
        org_id=org_id,
    )

    return {
        "id": str(cr.id),
        "org_id": str(cr.org_id),
        "code": cr.code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": cr.change_type,
        "status": cr.status,
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": cr.description,
        "old_value": cr.old_value,
        "new_value": cr.new_value,
        "notes": cr.notes,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": str(cr.approved_by) if cr.approved_by else None,
        "approved_at": str(cr.approved_at) if cr.approved_at else None,
        "created_at": str(cr.created_at) if cr.created_at else None,
    }


def reject_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
    reason: str | None = None,
) -> dict:
    cr = db.get(ChangeRequest, UUID(cr_id))
    if cr is None or cr.org_id != org_id:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}")
    if cr.status == ChangeRequestStatus.COMPLETED:
        raise ValidationError(f"Change request {cr_id} already completed")
    cr.status = ChangeRequestStatus.REJECTED
    cr.reason = reason
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"reason": reason},
        org_id=org_id,
    )

    return {
        "id": str(cr.id),
        "org_id": str(cr.org_id),
        "code": cr.code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": cr.change_type,
        "status": cr.status,
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": cr.description,
        "old_value": cr.old_value,
        "new_value": cr.new_value,
        "notes": cr.notes,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": str(cr.approved_by) if cr.approved_by else None,
        "approved_at": str(cr.approved_at) if cr.approved_at else None,
        "created_at": str(cr.created_at) if cr.created_at else None,
    }


def cancel_change_request(db: Session, *, org_id: UUID, actor: User, cr_id: str) -> dict:
    cr = db.get(ChangeRequest, UUID(cr_id))
    if cr is None or cr.org_id != org_id:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}")
    cr.status = ChangeRequestStatus.CANCELLED
    cr.approved_by = actor.id
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"status": ChangeRequestStatus.CANCELLED.value},
        org_id=org_id,
    )

    return {
        "id": str(cr.id),
        "org_id": str(cr.org_id),
        "code": cr.code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": cr.change_type,
        "status": cr.status,
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": cr.description,
        "old_value": cr.old_value,
        "new_value": cr.new_value,
        "notes": cr.notes,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": str(cr.approved_by) if cr.approved_by else None,
        "approved_at": str(cr.approved_at) if cr.approved_at else None,
        "created_at": str(cr.created_at) if cr.created_at else None,
    }


def apply_change_request(
    db: Session,
    *,
    org_id: UUID,
    employee: Employee,
    data: dict,
    actor: User,
    change_type: str,
) -> Employee:
    """
    Apply an approved change request to the employee record and append history.

    ``data`` maps attribute names to new values. Each change is recorded in
    EmployeeHistory with ``change_type`` and stored for audit.
    """
    # Determine which attributes of employee are being changed
    fields_to_update = {}
    for key, value in data.items():
        attr_map = {
            "department_id": "department_id",
            "position_id": "position_id",
            "manager_id": "manager_id",
            "location": "location",
            "employment_type": "employment_type",
            "status": "status",
            "work_email": "work_email",
            "personal_email": "personal_email",
            "phone": "phone",
            "date_of_joining": "date_of_joining",
            "date_of_birth": "date_of_birth",
            "gender": "gender",
            "pan_masked": "pan_masked",
            "bank_masked": "bank_masked",
        }
        col = attr_map.get(key.lower(), key.lower())
        fields_to_update[col] = value

    # Append history entries first (capture old values before mutation)
    for col, new_val in fields_to_update.items():
        old_val = getattr(employee, col)
        db.add(EmployeeHistory(
            employee_id=employee.id,
            effective_date=_utcnow(),
            change_type=change_type,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val) if new_val is not None else None,
            notes="applied via lifecycle change request",
            actor_id=actor.id,
        ))
        setattr(employee, col, new_val)

    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="employee",
        entity_id=str(employee.id),
        details={"changes": fields_to_update},
        org_id=org_id,
    )

    return employee
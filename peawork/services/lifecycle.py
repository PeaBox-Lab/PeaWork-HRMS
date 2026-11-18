"""Employee lifecycle change-request service (R5 candidate / lifecycle module).

Handles effective-dated change requests for:
resignation, promotion, position/location/department changes, manager change,
employment-type change, contract change, rehire, group-company transfer,
country transfer, legal entity change, project assignment, cost-center change,
timesheet adjustment, leave request, attendance adjustment.

Workflow per request:
  draft -> pending_approval -> approved/rejected/cancelled -> completed

On completion, ``complete_change_request`` mutates the target ``Employee`` and
appends an ``EmployeeHistory`` row with matching ``change_type``.

Authorization is role-aware and field-aware:

* Admin/HR may raise, edit, submit, cancel, approve, reject and complete any
  request in the organisation, and may apply any writable employee column.
* Managers may raise/edit/submit/cancel requests for themselves and their
  direct reports, but may only propose (never apply) HR-only fields.
* Employees may raise/edit/submit/cancel their own requests and may only apply
  self-service fields (contact, location, gender, dates of birth).
* Approve/reject is HR-only; completion of HR-only fields is HR-only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from ..domain import (
    AuditAction,
    ChangeRequestStatus,
    ChangeRequestType,
    EmployeeStatus,
    EmploymentType,
    Role,
)
from ..models import (
    ChangeRequest,
    Department,
    Employee,
    EmployeeHistory,
    Position,
    User,
)
from . import audit
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_change_type(value: str | ChangeRequestType) -> ChangeRequestType:
    """Convert string or enum to ChangeRequestType enum."""
    if isinstance(value, ChangeRequestType):
        return value
    try:
        return ChangeRequestType(value)
    except ValueError:
        raise ValidationError(
            f"Invalid change_type: {value}. Valid: {[e.value for e in ChangeRequestType]}"
        )


# Employee columns a change request is allowed to write. Everything else must be
# handled by its dedicated module (leave, timesheets, payroll, documents, ...).
_WRITABLE_FIELDS: dict[str, str] = {
    "work_email": "str",
    "personal_email": "str",
    "phone": "str",
    "location": "str",
    "gender": "str",
    "date_of_joining": "date",
    "date_of_birth": "date",
    "employment_type": "employment_type",
    "status": "employee_status",
    "department_id": "uuid",
    "position_id": "uuid",
    "manager_id": "uuid",
}

_EDITABLE_FIELDS = {
    "employee_id",
    "effective_date",
    "description",
    "old_value",
    "new_value",
    "details",
    "notes",
    "reason",
}

# Sensible defaults applied on completion when no explicit details were supplied.
_DEFAULT_DETAILS: dict[ChangeRequestType, dict[str, Any]] = {
    ChangeRequestType.RESIGNATION: {"status": EmployeeStatus.NOTICE},
    ChangeRequestType.REHIRE: {"status": EmployeeStatus.ACTIVE},
}

_HR_ROLES = {Role.ADMIN, Role.HR}
# Roles that may read every change request in the organisation.
_READ_ALL_ROLES = {Role.ADMIN, Role.HR, Role.AUDITOR, Role.PAYROLL}

# Fields a non-HR actor may apply to their own employee record. Everything else
# in ``_WRITABLE_FIELDS`` (status, org assignment, employment terms, work email)
# changes only through HR.
SELF_SERVICE_FIELDS = frozenset(
    {"location", "phone", "personal_email", "gender", "date_of_birth"}
)
HR_ONLY_FIELDS = frozenset(_WRITABLE_FIELDS) - SELF_SERVICE_FIELDS

# Statuses from which a request may still be approved.
_APPROVABLE_STATUSES = {ChangeRequestStatus.DRAFT, ChangeRequestStatus.PENDING_APPROVAL}


def _is_hr(actor: User) -> bool:
    return actor.role in _HR_ROLES


def allowed_fields_for(actor: User) -> frozenset[str]:
    """Employee columns this actor may apply through a completed request."""
    return frozenset(_WRITABLE_FIELDS) if _is_hr(actor) else SELF_SERVICE_FIELDS


def _deny_fields(actor: User, fields: set[str] | frozenset[str]) -> None:
    denied = sorted(set(fields) - allowed_fields_for(actor))
    if denied:
        raise ForbiddenError(
            "Your role cannot apply these employee fields: " + ", ".join(denied)
        )


def can_view_change_request(actor: User, cr: ChangeRequest) -> bool:
    """Object-level read policy: HR-wide, manager-of, or self."""
    if actor.role in _READ_ALL_ROLES:
        return True
    if cr.employee_id is None or actor.employee_id is None:
        return False
    if cr.employee_id == actor.employee_id:
        return True
    if actor.role == Role.MANAGER and cr.employee is not None:
        return cr.employee.manager_id == actor.employee_id
    return False


def can_manage_change_request(actor: User, cr: ChangeRequest) -> bool:
    """Whether ``actor`` may edit, submit or cancel this request."""
    if _is_hr(actor):
        return True
    if cr.employee_id is None or actor.employee_id is None:
        return False
    if cr.employee_id == actor.employee_id:
        return True
    return bool(
        actor.role == Role.MANAGER
        and cr.employee is not None
        and cr.employee.manager_id == actor.employee_id
    )


def _require_view(actor: User, cr: ChangeRequest) -> None:
    if not can_view_change_request(actor, cr):
        # Do not leak the existence of other employees' requests.
        raise NotFoundError(f"Change request {cr.code} not found")


def _require_manage(actor: User, cr: ChangeRequest) -> None:
    if not can_manage_change_request(actor, cr):
        raise ForbiddenError("You do not have permission to change this request")



def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _serialize(cr: ChangeRequest) -> dict:
    event = cr.event.value if getattr(cr, "event", None) else None
    return {
        "id": str(cr.id),
        "org_id": str(cr.org_id),
        "code": cr.code,
        "employee_id": str(cr.employee_id) if cr.employee_id else None,
        "change_type": cr.change_type.value
        if isinstance(cr.change_type, ChangeRequestType)
        else str(cr.change_type),
        "event": event,
        "reason_code": getattr(cr, "reason_code", None),
        "transaction_date": cr.transaction_date.isoformat()
        if getattr(cr, "transaction_date", None)
        else None,
        "current_snapshot": getattr(cr, "current_snapshot", None),
        "proposed_snapshot": getattr(cr, "proposed_snapshot", None),
        "approval_step": getattr(cr, "approval_step", None),
        "applied_at": cr.applied_at.isoformat()
        if getattr(cr, "applied_at", None)
        else None,
        "status": cr.status.value
        if isinstance(cr.status, ChangeRequestStatus)
        else str(cr.status),
        "effective_date": cr.effective_date.isoformat() if cr.effective_date else None,
        "description": cr.description,
        "old_value": cr.old_value,
        "new_value": cr.new_value,
        "details": cr.details,
        "notes": cr.notes,
        "reason": cr.reason,
        "actor_id": str(cr.actor_id) if cr.actor_id else None,
        "approved_by": str(cr.approved_by) if cr.approved_by else None,
        "approved_at": cr.approved_at.isoformat() if cr.approved_at else None,
        "created_at": cr.created_at.isoformat() if cr.created_at else None,
    }


def _get_change_request(db: Session, org_id: UUID, cr_id: str | UUID) -> ChangeRequest:
    try:
        key = UUID(str(cr_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}") from exc
    cr = db.get(ChangeRequest, key)
    if cr is None or cr.org_id != org_id:
        raise NotFoundError(f"Change request {cr_id} not found in {org_id}")
    return cr


def _normalize_details(
    db: Session, *, org_id: UUID, employee: Employee, raw: dict[str, Any]
) -> dict[str, Any]:
    """Validate a details mapping and coerce values to the employee's column types."""
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        field = str(key).strip().lower()
        if field not in _WRITABLE_FIELDS:
            raise ValidationError(
                f"Field '{key}' cannot be applied via a change request. "
                f"Allowed: {sorted(_WRITABLE_FIELDS)}"
            )
        kind = _WRITABLE_FIELDS[field]
        if value is None or value == "":
            normalized[field] = None
            continue
        try:
            if kind == "str":
                normalized[field] = str(value)
            elif kind == "date":
                normalized[field] = (
                    value if isinstance(value, date) else date.fromisoformat(str(value))
                )
            elif kind == "uuid":
                normalized[field] = (
                    value if isinstance(value, UUID) else UUID(str(value))
                )
            elif kind == "employment_type":
                normalized[field] = EmploymentType(str(value))
            elif kind == "employee_status":
                normalized[field] = EmployeeStatus(str(value))
            else:  # pragma: no cover - guarded by the table above
                raise ValidationError(f"Unsupported field type for '{field}'")
        except (ValueError, TypeError) as exc:
            raise ValidationError(f"Invalid value for '{field}': {value}") from exc

    if normalized.get("department_id"):
        dept = db.get(Department, normalized["department_id"])
        if dept is None or dept.org_id != org_id:
            raise ValidationError("Department not found in this organisation")
    if normalized.get("position_id"):
        position = db.get(Position, normalized["position_id"])
        if position is None or position.org_id != org_id:
            raise ValidationError("Position not found in this organisation")
    if normalized.get("manager_id"):
        if normalized["manager_id"] == employee.id:
            raise ValidationError("An employee cannot be their own manager")
        manager = db.get(Employee, normalized["manager_id"])
        if manager is None or manager.org_id != org_id:
            raise ValidationError("Manager not found in this organisation")
    return normalized


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
    details: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict:
    """
    Create a new lifecycle change request.

    Returns a dict with keys: id, org_id, code, employee_id, change_type,
    status, effective_date, description, old_value, new_value, details, notes,
    actor_id, approved_by, approved_at, created_at.
    """
    change_type_enum = _to_change_type(change_type)

    existing = db.scalar(
        select(ChangeRequest).where(
            ChangeRequest.org_id == org_id, ChangeRequest.code == code
        )
    )
    if existing is not None:
        raise ConflictError(f"Change request code '{code}' already exists in {org_id}")

    employee_uuid = UUID(str(employee_id)) if employee_id else None
    if employee_uuid is None and not _is_hr(actor):
        # Self-service defaults the target to the caller's own record.
        employee_uuid = actor.employee_id
        if employee_uuid is None:
            raise ValidationError(
                "Your account is not linked to an employee record; ask HR to raise this request"
            )

    if employee_uuid is not None:
        employee = db.get(Employee, employee_uuid)
        if employee is None or employee.org_id != org_id:
            raise ValidationError("Employee not found in this organisation")
        if not _is_hr(actor):
            is_self = employee.id == actor.employee_id
            is_report = bool(
                actor.role == Role.MANAGER
                and actor.employee_id is not None
                and employee.manager_id == actor.employee_id
            )
            if not (is_self or is_report):
                raise ForbiddenError(
                    "You can only raise change requests for yourself or your direct reports"
                )

    cr = ChangeRequest(
        org_id=org_id,
        code=code,
        employee_id=employee_uuid,
        change_type=change_type_enum,
        status=ChangeRequestStatus.DRAFT,
        effective_date=effective_date or date.today(),
        description=description,
        old_value=old_value,
        new_value=new_value,
        details=details,
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
    return _serialize(cr)


def list_change_requests(
    db: Session,
    *,
    org_id: UUID,
    user: User,
    status: str | None = None,
    change_type: str | None = None,
    employee_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    stmt = select(ChangeRequest).where(ChangeRequest.org_id == org_id)
    if user.role in _READ_ALL_ROLES:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        report_ids = select(Employee.id).where(
            Employee.org_id == org_id, Employee.manager_id == user.employee_id
        )
        stmt = stmt.where(
            or_(
                ChangeRequest.employee_id == user.employee_id,
                ChangeRequest.employee_id.in_(report_ids),
            )
        )
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

    stmt = stmt.order_by(ChangeRequest.created_at.desc(), ChangeRequest.code)
    if offset:
        stmt = stmt.offset(max(0, offset))
    if limit:
        stmt = stmt.limit(limit)

    rows = db.execute(stmt).scalars().all()
    return [_serialize(cr) for cr in rows]


def get_change_request(
    db: Session, *, org_id: UUID, cr_id: str, actor: User | None = None
) -> dict:
    cr = _get_change_request(db, org_id, cr_id)
    if actor is not None:
        _require_view(actor, cr)
    return _serialize(cr)


def approve_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
) -> dict:
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can approve change requests")
    cr = _get_change_request(db, org_id, cr_id)
    if cr.status not in _APPROVABLE_STATUSES:
        raise ValidationError(
            f"Change request {cr_id} cannot be approved from status '{cr.status.value}'"
        )

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

    return _serialize(cr)


def reject_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
    reason: str | None = None,
) -> dict:
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can reject change requests")
    cr = _get_change_request(db, org_id, cr_id)
    if cr.status == ChangeRequestStatus.COMPLETED:
        raise ValidationError(f"Change request {cr_id} already completed")
    if cr.status == ChangeRequestStatus.CANCELLED:
        raise ValidationError(f"Change request {cr_id} already cancelled")
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

    return _serialize(cr)


def cancel_change_request(db: Session, *, org_id: UUID, actor: User, cr_id: str) -> dict:
    cr = _get_change_request(db, org_id, cr_id)
    _require_manage(actor, cr)
    if cr.status in {ChangeRequestStatus.COMPLETED, ChangeRequestStatus.CANCELLED}:
        raise ValidationError(
            f"Change request {cr_id} cannot be cancelled from status '{cr.status.value}'"
        )
    cr.status = ChangeRequestStatus.CANCELLED
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

    return _serialize(cr)


def submit_change_request(db: Session, *, org_id: UUID, actor: User, cr_id: str) -> dict:
    """Move a draft change request into the approval queue."""
    cr = _get_change_request(db, org_id, cr_id)
    _require_manage(actor, cr)
    if cr.status != ChangeRequestStatus.DRAFT:
        raise ValidationError(
            f"Only draft change requests can be submitted (current: {cr.status.value})"
        )
    if cr.employee_id is None:
        raise ValidationError("Link an employee before submitting for approval")

    cr.status = ChangeRequestStatus.PENDING_APPROVAL
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"status": ChangeRequestStatus.PENDING_APPROVAL.value},
        org_id=org_id,
    )
    return _serialize(cr)


def update_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
    changes: dict[str, Any],
) -> dict:
    """Edit the mutable fields of a draft change request."""
    cr = _get_change_request(db, org_id, cr_id)
    _require_manage(actor, cr)
    if cr.status != ChangeRequestStatus.DRAFT:
        raise ValidationError(
            f"Only draft change requests can be edited (current: {cr.status.value})"
        )

    unknown = sorted(set(changes) - _EDITABLE_FIELDS)
    if unknown:
        raise ValidationError(
            f"Field(s) not editable: {', '.join(unknown)}. "
            f"Editable: {', '.join(sorted(_EDITABLE_FIELDS))}"
        )

    if "employee_id" in changes and not _is_hr(actor):
        raw_employee = changes["employee_id"]
        current = str(cr.employee_id) if cr.employee_id else ""
        if str(raw_employee or "") != current:
            raise ForbiddenError(
                "You cannot reassign a change request to another employee"
            )

    if "employee_id" in changes:
        raw_employee = changes["employee_id"]
        if raw_employee:
            try:
                employee_key = UUID(str(raw_employee))
            except (ValueError, TypeError) as exc:
                raise ValidationError(f"Invalid employee_id: {raw_employee}") from exc
            employee = db.get(Employee, employee_key)
            if employee is None or employee.org_id != org_id:
                raise ValidationError("Employee not found in this organisation")
            cr.employee_id = employee.id
        else:
            cr.employee_id = None

    if "effective_date" in changes:
        raw_date = changes["effective_date"]
        if raw_date is None:
            raise ValidationError("effective_date may not be cleared")
        if isinstance(raw_date, date):
            cr.effective_date = raw_date
        else:
            try:
                cr.effective_date = date.fromisoformat(str(raw_date))
            except ValueError as exc:
                raise ValidationError(f"Invalid effective_date: {raw_date}") from exc

    if "details" in changes:
        details = changes["details"]
        if details is not None and not isinstance(details, dict):
            raise ValidationError("details must be an object")
        cr.details = details

    for field in ("description", "old_value", "new_value", "notes", "reason"):
        if field in changes:
            setattr(cr, field, changes[field])

    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"updated": sorted(changes)},
        org_id=org_id,
    )
    return _serialize(cr)


def complete_change_request(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    cr_id: str,
    details: dict[str, Any] | None = None,
) -> dict:
    """
    Apply an approved change request to the employee record and close it.

    ``details`` (optional) is merged over the request's stored details and maps
    employee columns to their new values, for example
    ``{"position_id": "<uuid>", "status": "notice"}``.
    """
    cr = _get_change_request(db, org_id, cr_id)
    if not _is_hr(actor) and actor.employee_id != cr.employee_id:
        raise ForbiddenError("You do not have permission to complete this request")
    if cr.status != ChangeRequestStatus.APPROVED:
        raise ValidationError(
            f"Only approved change requests can be completed (current: {cr.status.value})"
        )
    if cr.employee_id is None:
        raise ValidationError(
            "A change request must be linked to an employee before completion"
        )

    employee = db.get(Employee, cr.employee_id)
    if employee is None or employee.org_id != org_id:
        raise NotFoundError("Linked employee not found in this organisation")

    merged: dict[str, Any] = dict(_DEFAULT_DETAILS.get(cr.change_type, {}))
    if cr.details:
        merged.update(cr.details)
    if details:
        merged.update(details)

    # Field-level authorization: non-HR actors may only write self-service
    # columns, even when the request's stored details mention HR-only fields.
    updates = _normalize_details(db, org_id=org_id, employee=employee, raw=merged)
    _deny_fields(actor, set(updates))

    changed: list[str] = []
    for field, new_value in updates.items():
        old_value = getattr(employee, field)
        if old_value == new_value:
            continue
        db.add(
            EmployeeHistory(
                employee_id=employee.id,
                effective_date=cr.effective_date,
                change_type=cr.change_type.value,
                old_value=_stringify(old_value),
                new_value=_stringify(new_value),
                notes=f"Applied via change request {cr.code}",
                actor_id=actor.id,
            )
        )
        setattr(employee, field, new_value)
        changed.append(field)

    if not changed:
        # Changes that do not touch an employee column directly (e.g. project
        # assignment bookkeeping) still leave an auditable history entry.
        db.add(
            EmployeeHistory(
                employee_id=employee.id,
                effective_date=cr.effective_date,
                change_type=cr.change_type.value,
                old_value=cr.old_value,
                new_value=cr.new_value,
                notes=f"Applied via change request {cr.code} (no direct employee field change)",
                actor_id=actor.id,
            )
        )

    if merged:
        cr.details = merged
    cr.status = ChangeRequestStatus.COMPLETED
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="change_request",
        entity_id=str(cr.id),
        details={"status": ChangeRequestStatus.COMPLETED.value, "changed": changed},
        org_id=org_id,
    )
    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="employee",
        entity_id=str(employee.id),
        details={"changed": changed, "change_request": cr.code},
        org_id=org_id,
    )
    return _serialize(cr)
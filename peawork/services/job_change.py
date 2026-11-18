"""Market-standard job changes (R5): event + reason, snapshots, approvals.

Builds on the existing effective-dated ``ChangeRequest`` engine, but exposes the
structure HR teams expect: a **job change event** (promotion, transfer,
demotion, lateral move, manager change, location change, data change, contract
change) with a **mandatory reason**, a current-vs-proposed snapshot, and a
manager → HR approval chain. Applying the change is separate from approving it:
an approved change takes effect on its effective date.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import (
    ChangeRequestStatus,
    ChangeRequestType,
    JobChangeEvent,
    Role,
)
from ..models import ChangeRequest, Employee, User
from . import audit
from . import lifecycle as lifecycle_service
from .errors import ForbiddenError, NotFoundError, ValidationError

# Event → reason catalogue. Codes are stable; labels are shown in the UI.
EVENT_REASONS: dict[JobChangeEvent, tuple[tuple[str, str], ...]] = {
    JobChangeEvent.PROMOTION: (
        ("increase_in_responsibility", "Increase in responsibility"),
        ("promotion_with_pay_increase", "Promotion with pay increase"),
        ("promotion_without_pay_increase", "Promotion without pay increase"),
        ("internal_hire", "Internal hire into a higher role"),
    ),
    JobChangeEvent.TRANSFER: (
        ("department_transfer", "Move to another department"),
        ("manager_transfer", "Move to another manager"),
        ("location_transfer", "Move to another location"),
        ("inter_company_transfer", "Inter-company transfer"),
        ("cross_border_transfer", "Cross-border transfer"),
    ),
    JobChangeEvent.DEMOTION: (
        ("decrease_in_responsibility", "Decrease in responsibility"),
        ("performance_improvement", "Performance improvement move"),
    ),
    JobChangeEvent.LATERAL_MOVE: (
        ("same_level_move", "Same-level move"),
        ("position_change", "Position change"),
        ("project_assignment", "Project assignment"),
    ),
    JobChangeEvent.MANAGER_CHANGE: (
        ("new_manager", "New manager"),
        ("reorganisation", "Reorganisation"),
    ),
    JobChangeEvent.LOCATION_CHANGE: (
        ("relocation", "Relocation"),
        ("office_to_remote", "Office to remote"),
        ("remote_to_office", "Remote to office"),
    ),
    JobChangeEvent.DATA_CHANGE: (
        ("correction", "Data correction"),
        ("title_change", "Business title change"),
        ("cost_center_change", "Cost-centre change"),
        ("employment_type_change", "Employment-type change"),
        ("contract_details", "Contract details"),
    ),
    JobChangeEvent.CONTRACT_CHANGE: (
        ("extension", "Contract extension"),
        ("renewal", "Contract renewal"),
        ("conversion_to_permanent", "Conversion to permanent"),
    ),
    JobChangeEvent.TERMINATION: (
        ("resignation", "Resignation"),
        ("end_of_contract", "End of fixed-term contract"),
        ("retirement", "Retirement"),
    ),
}

REASON_LABELS: dict[str, str] = {
    code: label for reasons in EVENT_REASONS.values() for code, label in reasons
}

# Employee columns a job change may propose, by event. Everything else has its
# own module (leave, attendance, payroll, documents).
EVENT_FIELDS: dict[JobChangeEvent, tuple[str, ...]] = {
    JobChangeEvent.PROMOTION: ("position_id", "manager_id", "department_id", "location"),
    JobChangeEvent.TRANSFER: ("department_id", "manager_id", "location", "position_id"),
    JobChangeEvent.DEMOTION: ("position_id", "manager_id", "department_id"),
    JobChangeEvent.LATERAL_MOVE: ("position_id", "department_id", "manager_id"),
    JobChangeEvent.MANAGER_CHANGE: ("manager_id",),
    JobChangeEvent.LOCATION_CHANGE: ("location",),
    JobChangeEvent.DATA_CHANGE: (
        "work_email",
        "personal_email",
        "phone",
        "employment_type",
        "department_id",
        "position_id",
        "manager_id",
        "location",
    ),
    JobChangeEvent.CONTRACT_CHANGE: ("employment_type", "status"),
    JobChangeEvent.TERMINATION: ("status",),
}

_EVENT_TO_CHANGE_TYPE = {
    JobChangeEvent.PROMOTION: ChangeRequestType.PROMOTION,
    JobChangeEvent.TRANSFER: ChangeRequestType.POSITION_CHANGE,
    JobChangeEvent.DEMOTION: ChangeRequestType.POSITION_CHANGE,
    JobChangeEvent.LATERAL_MOVE: ChangeRequestType.POSITION_CHANGE,
    JobChangeEvent.MANAGER_CHANGE: ChangeRequestType.MANAGER_CHANGE,
    JobChangeEvent.LOCATION_CHANGE: ChangeRequestType.LOCATION_CHANGE,
    JobChangeEvent.DATA_CHANGE: ChangeRequestType.OTHER,
    JobChangeEvent.CONTRACT_CHANGE: ChangeRequestType.CONTRACT_CHANGE,
    JobChangeEvent.TERMINATION: ChangeRequestType.RESIGNATION,
}

_FIELD_LABELS = {
    "work_email": "Work email",
    "personal_email": "Personal email",
    "phone": "Phone",
    "location": "Location",
    "employment_type": "Employment type",
    "status": "Employment status",
    "department_id": "Department",
    "position_id": "Position",
    "manager_id": "Manager",
}

_HR_ROLES = {Role.ADMIN, Role.HR}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def event_options() -> list[tuple[str, str]]:
    return [(item.value, item.label) for item in JobChangeEvent]


def reason_options(event: str | JobChangeEvent) -> list[tuple[str, str]]:
    try:
        event_enum = JobChangeEvent(str(event))
    except ValueError:
        return []
    return [("", "Choose a reason"), *list(EVENT_REASONS[event_enum])]


def _event(value: str | JobChangeEvent) -> JobChangeEvent:
    try:
        return JobChangeEvent(str(value))
    except ValueError as exc:
        raise ValidationError(
            "event must be one of: " + ", ".join(item.value for item in JobChangeEvent)
        ) from exc


def _reason(event_enum: JobChangeEvent, reason_code: str) -> str:
    code = (reason_code or "").strip()
    valid = {code for code, _ in EVENT_REASONS[event_enum]}
    if not code:
        raise ValidationError("Choose a reason for the job change")
    if code not in valid:
        raise ValidationError(
            f"Reason '{code}' is not valid for event '{event_enum.value}'. "
            f"Valid: {', '.join(sorted(valid))}"
        )
    return code


def _employee(db: Session, org_id, employee_id) -> Employee:
    try:
        key = UUID(str(employee_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Employee {employee_id} not found") from exc
    employee = db.get(Employee, key)
    if employee is None or employee.org_id != org_id:
        raise NotFoundError(f"Employee {employee_id} not found in this organisation")
    return employee


def _serialize_value(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _clean_proposed(
    event_enum: JobChangeEvent, proposed: dict | None, employee: Employee
) -> dict:
    allowed = set(EVENT_FIELDS[event_enum])
    cleaned: dict = {}
    for key, value in (proposed or {}).items():
        field = str(key).strip()
        if field not in allowed:
            raise ValidationError(
                f"Field '{field}' cannot be changed for a {event_enum.label.lower()}. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )
        if value in (None, ""):
            continue
        cleaned[field] = value
    if not cleaned:
        raise ValidationError("Propose at least one changed field")
    if cleaned.get("manager_id") and str(cleaned["manager_id"]) == str(employee.id):
        raise ValidationError("An employee cannot be their own manager")
    return cleaned


def _reason_actor(user) -> None:
    if user.role in {Role.AUDITOR, Role.PAYROLL}:
        raise ForbiddenError("Your role cannot raise job changes")


def can_create_for(actor: User, employee: Employee) -> bool:
    if actor.role in _HR_ROLES:
        return True
    if actor.employee_id == employee.id:
        return True
    return bool(
        actor.role == Role.MANAGER
        and actor.employee_id
        and employee.manager_id == actor.employee_id
    )


def _next_code(db: Session, org_id) -> str:
    prefix = f"JC-{date.today().year}-"
    existing = set(
        db.scalars(
            select(ChangeRequest.code).where(
                ChangeRequest.org_id == org_id, ChangeRequest.code.like(f"{prefix}%")
            )
        ).all()
    )
    n = 1
    while f"{prefix}{n:04d}" in existing:
        n += 1
    return f"{prefix}{n:04d}"


def create_job_change(
    db: Session,
    *,
    org_id,
    actor: User,
    employee_id,
    event: str | JobChangeEvent,
    reason_code: str,
    effective_date: date,
    proposed: dict | None,
    notes: str | None = None,
) -> dict:
    """Create a draft job change with current/proposed snapshots."""
    _reason_actor(actor)
    event_enum = _event(event)
    code = _reason(event_enum, reason_code)
    employee = _employee(db, org_id, employee_id)
    if not can_create_for(actor, employee):
        raise ForbiddenError(
            "You can only raise job changes for yourself or your direct reports"
        )

    cleaned = _clean_proposed(event_enum, proposed, employee)
    normalized = lifecycle_service._normalize_details(
        db, org_id=org_id, employee=employee, raw=cleaned
    )
    lifecycle_service._deny_fields(actor, set(normalized))

    current = {field: _serialize_value(getattr(employee, field)) for field in normalized}
    summary = ", ".join(
        f"{_FIELD_LABELS.get(field, field)}: {current[field] or '—'} → {_serialize_value(value) or '—'}"
        for field, value in normalized.items()
    )

    created = lifecycle_service.create_change_request(
        db,
        org_id=org_id,
        actor=actor,
        code=_next_code(db, org_id),
        change_type=_EVENT_TO_CHANGE_TYPE[event_enum],
        employee_id=employee.id,
        effective_date=effective_date,
        description=f"{event_enum.label}: {REASON_LABELS[code]}",
        old_value=summary,
        new_value=summary,
        details={key: _serialize_value(value) for key, value in normalized.items()},
        notes=notes,
    )
    cr = db.get(ChangeRequest, UUID(created["id"]))
    assert cr is not None
    cr.event = event_enum
    cr.reason_code = code
    cr.transaction_date = date.today()
    cr.current_snapshot = current
    cr.proposed_snapshot = {
        field: _serialize_value(value) for field, value in normalized.items()
    }
    cr.approval_step = "manager" if employee.manager_id else "hr"
    db.add(cr)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="change_request",
        entity_id=cr.id,
        details={"event": event_enum.value, "reason_code": code},
        org_id=org_id,
    )
    return serialize_job_change(lifecycle_service._serialize(cr))


def serialize_job_change(row: dict) -> dict:
    event = JobChangeEvent(row["event"]) if row.get("event") else None
    reason = row.get("reason_code")
    return {
        **row,
        "event_label": event.label if event else None,
        "reason_label": REASON_LABELS.get(reason or "", reason),
    }


def list_job_changes(
    db: Session,
    *,
    org_id,
    user: User,
    status: str | None = None,
    event: str | None = None,
) -> list[dict]:
    rows = lifecycle_service.list_change_requests(
        db, org_id=org_id, user=user, status=status
    )
    result = []
    for row in rows:
        if not row.get("event"):
            continue
        if event and row["event"] != event:
            continue
        result.append(serialize_job_change(row))
    return result


def get_job_change(db: Session, *, org_id, actor: User, cr_id) -> dict:
    row = lifecycle_service.get_change_request(
        db, org_id=org_id, cr_id=str(cr_id), actor=actor
    )
    if not row.get("event"):
        raise NotFoundError(f"Job change {cr_id} not found")
    return serialize_job_change(row)


def submit_job_change(db: Session, *, org_id, actor: User, cr_id) -> dict:
    lifecycle_service.submit_change_request(
        db, org_id=org_id, actor=actor, cr_id=str(cr_id)
    )
    cr = db.get(ChangeRequest, UUID(str(cr_id)))
    assert cr is not None
    employee = db.get(Employee, cr.employee_id) if cr.employee_id else None
    cr.approval_step = "manager" if employee and employee.manager_id else "hr"
    db.add(cr)
    db.flush()
    return get_job_change(db, org_id=org_id, actor=actor, cr_id=cr_id)


def approve_job_change(
    db: Session, *, org_id, actor: User, cr_id
) -> dict:
    """Manager-of approves first; HR/admin approves and applies if due."""
    cr = db.get(ChangeRequest, UUID(str(cr_id)))
    if cr is None or cr.org_id != org_id or not cr.event:
        raise NotFoundError(f"Job change {cr_id} not found")
    if cr.status not in {ChangeRequestStatus.DRAFT, ChangeRequestStatus.PENDING_APPROVAL}:
        raise ValidationError(
            f"Job change cannot be approved from status '{cr.status.value}'"
        )
    employee = db.get(Employee, cr.employee_id) if cr.employee_id else None
    is_hr = actor.role in _HR_ROLES
    is_manager = bool(
        actor.role == Role.MANAGER
        and actor.employee_id
        and employee
        and employee.manager_id == actor.employee_id
    )
    if not (is_hr or is_manager):
        raise ForbiddenError("Only the employee's manager, HR or an administrator can approve")

    if is_manager and not is_hr:
        if cr.approval_step != "manager":
            raise ValidationError("This job change is already with HR for approval")
        cr.approval_step = "hr"
        db.add(cr)
        db.flush()
        audit.record(
            db,
            actor=actor,
            action="update",
            entity_type="change_request",
            entity_id=cr.id,
            details={"approval_step": "hr"},
            org_id=org_id,
        )
        return get_job_change(db, org_id=org_id, actor=actor, cr_id=cr_id)

    lifecycle_service.approve_change_request(
        db, org_id=org_id, actor=actor, cr_id=str(cr_id)
    )
    cr = db.get(ChangeRequest, UUID(str(cr_id)))
    assert cr is not None
    cr.approval_step = "done"
    db.add(cr)
    db.flush()
    return apply_if_due(db, org_id=org_id, actor=actor, cr=cr)


def apply_if_due(db: Session, *, org_id, actor: User, cr: ChangeRequest) -> dict:
    """Apply an approved job change once its effective date has arrived."""
    if cr.status != ChangeRequestStatus.APPROVED:
        return serialize_job_change(lifecycle_service._serialize(cr))
    if cr.effective_date and cr.effective_date > date.today():
        return serialize_job_change(lifecycle_service._serialize(cr))
    lifecycle_service.complete_change_request(
        db, org_id=org_id, actor=actor, cr_id=str(cr.id)
    )
    cr = db.get(ChangeRequest, UUID(str(cr.id)))
    assert cr is not None
    cr.applied_at = _utcnow()
    db.add(cr)
    db.flush()
    return serialize_job_change(lifecycle_service._serialize(cr))


def apply_due_job_changes(db: Session, *, org_id, actor: User) -> list[dict]:
    """Apply every approved job change whose effective date has passed."""
    rows = db.scalars(
        select(ChangeRequest).where(
            ChangeRequest.org_id == org_id,
            ChangeRequest.event.isnot(None),
            ChangeRequest.status == ChangeRequestStatus.APPROVED,
            ChangeRequest.effective_date <= date.today(),
        )
    ).all()
    applied = []
    for row in rows:
        applied.append(apply_if_due(db, org_id=org_id, actor=actor, cr=row))
    return applied


def reject_job_change(
    db: Session, *, org_id, actor: User, cr_id, reason: str | None = None
) -> dict:
    lifecycle_service.reject_change_request(
        db, org_id=org_id, actor=actor, cr_id=str(cr_id), reason=reason
    )
    return get_job_change(db, org_id=org_id, actor=actor, cr_id=cr_id)


def cancel_job_change(db: Session, *, org_id, actor: User, cr_id) -> dict:
    lifecycle_service.cancel_change_request(
        db, org_id=org_id, actor=actor, cr_id=str(cr_id)
    )
    return get_job_change(db, org_id=org_id, actor=actor, cr_id=cr_id)


__all__ = [
    "EVENT_REASONS",
    "EVENT_FIELDS",
    "REASON_LABELS",
    "apply_due_job_changes",
    "apply_if_due",
    "approve_job_change",
    "cancel_job_change",
    "create_job_change",
    "event_options",
    "get_job_change",
    "list_job_changes",
    "reason_options",
    "reject_job_change",
    "serialize_job_change",
    "submit_job_change",
]

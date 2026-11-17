"""Weekly timesheets (project/activity) with submit/correct/approve flow."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain import AuditAction, Role, TimesheetStatus
from ..models import Employee, Timesheet, TimesheetEntry, User
from . import audit
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError

MAX_ENTRY_HOURS = Decimal("24")
MAX_DAY_HOURS = Decimal("24")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _employee_or_404(db: Session, org_id: UUID, employee_id: UUID) -> Employee:
    employee = db.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")
    return employee


def _get_timesheet(db: Session, org_id: UUID, timesheet_id: UUID) -> Timesheet:
    timesheet = db.get(Timesheet, timesheet_id)
    if not timesheet or timesheet.org_id != org_id:
        raise NotFoundError("Timesheet not found")
    return timesheet


def _can_view(user: User, employee: Employee | None) -> bool:
    if user.role in {Role.ADMIN, Role.HR, Role.AUDITOR}:
        return True
    if employee is None:
        return False
    if user.employee_id and employee.id == user.employee_id:
        return True
    return bool(user.employee_id and employee.manager_id == user.employee_id)


def _can_edit(user: User, timesheet: Timesheet) -> bool:
    if user.role in {Role.ADMIN, Role.HR}:
        return True
    return bool(user.employee_id and timesheet.employee_id == user.employee_id)


def create_timesheet(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    employee_id: UUID,
    data: dict,
) -> Timesheet:
    """Create a draft timesheet for the week containing ``week_start`` (a Monday)."""
    employee = _employee_or_404(db, org_id, employee_id)
    try:
        week_start = date.fromisoformat(str(data.get("week_start") or "").strip())
    except ValueError:
        raise ValidationError("week_start must be an ISO date (YYYY-MM-DD)") from None
    if week_start.isoweekday() != 1:
        raise ValidationError("week_start must be a Monday")

    # Only self (or admin/hr on behalf) may create for an employee.
    if actor.role not in {Role.ADMIN, Role.HR}:
        if not actor.employee_id or actor.employee_id != employee.id:
            raise ForbiddenError("Not authorized to create a timesheet for this employee")

    existing = db.scalar(
        select(Timesheet).where(
            Timesheet.employee_id == employee.id,
            Timesheet.week_start == week_start,
        )
    )
    if existing is not None:
        raise ConflictError("A timesheet already exists for this employee and week")

    timesheet = Timesheet(
        org_id=org_id,
        employee_id=employee.id,
        week_start=week_start,
        status=TimesheetStatus.DRAFT,
    )
    db.add(timesheet)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="timesheet",
        entity_id=timesheet.id,
        details={"employee_id": str(employee.id), "week_start": week_start.isoformat()},
        org_id=org_id,
    )
    return timesheet


def add_entry(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    timesheet_id: UUID,
    data: dict,
) -> TimesheetEntry:
    timesheet = _get_timesheet(db, org_id, timesheet_id)
    if not _can_edit(actor, timesheet):
        raise ForbiddenError("Not authorized to edit this timesheet")
    if timesheet.status not in {TimesheetStatus.DRAFT, TimesheetStatus.REJECTED}:
        raise ValidationError("Only draft or rejected timesheets can be edited")

    try:
        work_date = date.fromisoformat(str(data.get("work_date") or "").strip())
    except ValueError:
        raise ValidationError("work_date must be an ISO date (YYYY-MM-DD)") from None
    if not (timesheet.week_start <= work_date <= timesheet.week_start + timedelta(days=6)):
        raise ValidationError("work_date must be within the timesheet week")

    activity = str(data.get("activity") or "").strip()
    if not activity:
        raise ValidationError("activity is required")

    try:
        hours = Decimal(str(data.get("hours")))
    except Exception:
        raise ValidationError("hours must be a number") from None
    if hours <= 0 or hours > MAX_ENTRY_HOURS:
        raise ValidationError(f"hours must be between 0 (exclusive) and {MAX_ENTRY_HOURS}")

    day_total = db.scalar(
        select(func.coalesce(func.sum(TimesheetEntry.hours), 0)).where(
            TimesheetEntry.timesheet_id == timesheet.id,
            TimesheetEntry.work_date == work_date,
        )
    )
    if Decimal(str(day_total)) + hours > MAX_DAY_HOURS:
        raise ValidationError(f"Total hours for {work_date.isoformat()} would exceed {MAX_DAY_HOURS}")

    entry = TimesheetEntry(
        timesheet_id=timesheet.id,
        work_date=work_date,
        project_code=(str(data.get("project_code") or "").strip() or None),
        activity=activity[:200],
        hours=hours,
        notes=(str(data.get("notes") or "").strip() or None),
    )
    db.add(entry)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="timesheet_entry",
        entity_id=entry.id,
        details={"timesheet_id": str(timesheet.id), "work_date": work_date.isoformat(),
                 "hours": str(hours)},
        org_id=org_id,
    )
    return entry


def submit_timesheet(
    db: Session, *, org_id: UUID, actor: User, timesheet_id: UUID
) -> Timesheet:
    timesheet = _get_timesheet(db, org_id, timesheet_id)
    if not _can_edit(actor, timesheet):
        raise ForbiddenError("Not authorized to submit this timesheet")
    if timesheet.status not in {TimesheetStatus.DRAFT, TimesheetStatus.REJECTED}:
        raise ValidationError(f"Timesheet is {timesheet.status.value}; cannot submit")

    total = db.scalar(
        select(func.coalesce(func.sum(TimesheetEntry.hours), 0)).where(
            TimesheetEntry.timesheet_id == timesheet.id
        )
    )
    if Decimal(str(total)) <= 0:
        raise ValidationError("Add at least one entry before submitting")

    timesheet.status = TimesheetStatus.SUBMITTED
    timesheet.submitted_at = _utcnow()
    db.add(timesheet)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="timesheet",
        entity_id=timesheet.id,
        details={"status": "submitted", "total_hours": str(total)},
        org_id=org_id,
    )
    return timesheet


def decide_timesheet(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    timesheet_id: UUID,
    decision: str,
    comment: str | None = None,
) -> Timesheet:
    timesheet = _get_timesheet(db, org_id, timesheet_id)
    employee = _employee_or_404(db, org_id, timesheet.employee_id)

    # Admin/HR may decide; managers only for direct reports, never their own.
    is_admin_hr = actor.role in {Role.ADMIN, Role.HR}
    is_manager_of = bool(
        actor.employee_id
        and employee.manager_id == actor.employee_id
        and employee.id != actor.employee_id
    )
    if not (is_admin_hr or is_manager_of):
        raise ForbiddenError("Not authorized to decide this timesheet")

    if timesheet.status != TimesheetStatus.SUBMITTED:
        raise ValidationError(f"Timesheet is {timesheet.status.value}; decision requires submitted status")

    verdict = (decision or "").strip().lower()
    if verdict == "approve":
        timesheet.status = TimesheetStatus.APPROVED
    elif verdict == "reject":
        timesheet.status = TimesheetStatus.REJECTED
    else:
        raise ValidationError("decision must be 'approve' or 'reject'")

    timesheet.approver_id = actor.id
    timesheet.approver_comment = (comment or "").strip() or None
    timesheet.decided_at = _utcnow()
    db.add(timesheet)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.APPROVE if verdict == "approve" else AuditAction.REJECT,
        entity_type="timesheet",
        entity_id=timesheet.id,
        details={"status": timesheet.status.value, "comment": timesheet.approver_comment},
        org_id=org_id,
    )
    return timesheet


def list_timesheets(
    db: Session,
    *,
    org_id: UUID,
    user: User,
    status: TimesheetStatus | None = None,
    employee_id: UUID | None = None,
) -> list[Timesheet]:
    stmt = select(Timesheet).where(Timesheet.org_id == org_id)
    if status is not None:
        stmt = stmt.where(Timesheet.status == status)
    if employee_id is not None:
        stmt = stmt.where(Timesheet.employee_id == employee_id)

    if user.role in {Role.ADMIN, Role.HR, Role.AUDITOR}:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        stmt = stmt.where(
            Timesheet.employee_id.in_(
                select(Employee.id).where(
                    (Employee.id == user.employee_id)
                    | (Employee.manager_id == user.employee_id)
                )
            )
        )
    elif user.employee_id:
        stmt = stmt.where(Timesheet.employee_id == user.employee_id)
    else:
        return []
    return list(db.scalars(stmt.order_by(Timesheet.week_start.desc())))

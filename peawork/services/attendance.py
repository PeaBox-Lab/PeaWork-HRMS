"""Attendance capture and derivation (R2).

Rule engine (documented contract — see docs/ROADMAP.md R2):

- Punches are stored as UTC instants; naive timestamps are rejected.
- Punches more than ``MAX_FUTURE_SKEW_MINUTES`` ahead of server time are
  rejected (device clock skew policy). Past punches are accepted.
- ``worked_minutes`` = (check_out - check_in) - shift.break_minutes, floored at 0.
  When no shift applies, break_minutes is treated as 0.
- ``overtime_minutes`` = max(0, worked_minutes - shift.minimum_hours * 60).
- ``late_minutes`` = max(0, check_in - (shift start + shift.grace_minutes)),
  compared in the organization timezone. No shift or no check_in ⇒ 0.
- Status derivation (only when status is not supplied or is ``present``/``late``/``half_day``):
    HALF_DAY  if 0 < worked_minutes < shift.minimum_hours * 30 (half of minimum)
    LATE      if late_minutes > 0
    PRESENT   otherwise
- Duplicate punch resolution:
    - manual source: overwrite (operator-corrected)
    - biometric/csv sources: first check_in wins, last check_out wins (min/max merge)
- Reconciliation marks ABSENT/LEAVE/HOLIDAY/WEEK_OFF for days with no record;
  existing records are never overwritten (idempotent).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..domain import AuditAction, AttendanceStatus, LeaveStatus, Role
from ..models import (
    AttendanceRecord,
    Employee,
    Holiday,
    LeaveRequest,
    Organization,
    Shift,
    User,
    WorkSchedule,
)
from . import audit
from .errors import BulkImportError, NotFoundError, ValidationError

MAX_FUTURE_SKEW_MINUTES = 15
_MACHINE_SOURCES = {"biometric", "csv"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; treat naive values as UTC instants."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _org_tz(db: Session, org_id: UUID) -> ZoneInfo:
    org = db.get(Organization, org_id)
    name = (org.timezone if org else None) or get_settings().organization_timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kolkata")


def _parse_instant(value, field: str) -> datetime | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValidationError(f"{field} is not a valid ISO datetime: {value!r}") from None
    else:
        raise ValidationError(f"{field} must be an ISO datetime string")
    if parsed.tzinfo is None:
        raise ValidationError(f"{field} must include a timezone offset (e.g. 2026-11-18T09:15:00+05:30)")
    return parsed.astimezone(timezone.utc)


def _check_skew(ts: datetime | None, field: str) -> None:
    if ts is not None and ts > _utcnow() + timedelta(minutes=MAX_FUTURE_SKEW_MINUTES):
        raise ValidationError(f"{field} is more than {MAX_FUTURE_SKEW_MINUTES} minutes in the future")


def derive(
    check_in: datetime | None,
    check_out: datetime | None,
    shift: Shift | None,
    org_tz: ZoneInfo,
) -> tuple[int, int, int, AttendanceStatus]:
    """Pure derivation: (worked_minutes, overtime_minutes, late_minutes, status)."""
    break_minutes = shift.break_minutes if shift else 0
    minimum_minutes = int((shift.minimum_hours if shift else Decimal("8")) * 60)

    worked = 0
    if check_in is not None and check_out is not None:
        delta = check_out - check_in
        worked = max(0, int(delta.total_seconds() // 60) - break_minutes)

    overtime = max(0, worked - minimum_minutes) if shift else max(0, worked - 480)

    late = 0
    if shift is not None and check_in is not None:
        local_in = check_in.astimezone(org_tz)
        shift_start = datetime.combine(local_in.date(), shift.start_time, tzinfo=org_tz)
        threshold = shift_start + timedelta(minutes=shift.grace_minutes)
        late = max(0, int((local_in - threshold).total_seconds() // 60))

    if shift is not None and 0 < worked < minimum_minutes // 2:
        status = AttendanceStatus.HALF_DAY
    elif late > 0:
        status = AttendanceStatus.LATE
    else:
        status = AttendanceStatus.PRESENT

    return worked, overtime, late, status


def _parse_date(value, field: str = "attendance_date") -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError(f"{field} must be an ISO date (YYYY-MM-DD)") from None


def _get_employee(db: Session, org_id: UUID, employee_id: UUID) -> Employee:
    employee = db.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")
    return employee


def _get_shift(db: Session, org_id: UUID, shift_id: UUID | None) -> Shift | None:
    if shift_id is None:
        return None
    shift = db.get(Shift, shift_id)
    if not shift or shift.org_id != org_id:
        raise NotFoundError("Shift not found")
    return shift


def upsert_attendance(
    db: Session,
    *,
    org_id: UUID,
    actor: User | None,
    employee_id: UUID,
    data: dict,
) -> AttendanceRecord:
    """Create or update the attendance record for (employee, date)."""
    employee = _get_employee(db, org_id, employee_id)
    on_date = _parse_date(data.get("attendance_date"))
    shift = _get_shift(db, org_id, UUID(str(data["shift_id"])) if data.get("shift_id") else None)

    check_in = _parse_instant(data.get("check_in"), "check_in")
    check_out = _parse_instant(data.get("check_out"), "check_out")
    _check_skew(check_in, "check_in")
    _check_skew(check_out, "check_out")
    if check_in and check_out and check_out <= check_in:
        raise ValidationError("check_out must be after check_in")

    source = (data.get("source") or "manual").strip().lower()
    org_tz = _org_tz(db, org_id)
    worked, overtime, late, derived_status = derive(check_in, check_out, shift, org_tz)

    status_value = data.get("status")
    try:
        status = AttendanceStatus(str(status_value).lower()) if status_value else derived_status
    except ValueError:
        raise ValidationError(f"Unknown attendance status: {status_value}") from None

    existing = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.employee_id == employee_id,
            AttendanceRecord.attendance_date == on_date,
        )
    )

    if existing is not None:
        record = existing
        existing_in = _as_aware_utc(existing.check_in)
        existing_out = _as_aware_utc(existing.check_out)
        if source in _MACHINE_SOURCES or source.startswith("biometric"):
            # Duplicate-punch resolution: first in wins, last out wins.
            if check_in is not None:
                check_in = min(existing_in, check_in) if existing_in else check_in
            else:
                check_in = existing_in
            if check_out is not None:
                check_out = max(existing_out, check_out) if existing_out else check_out
            else:
                check_out = existing_out
            worked, overtime, late, derived_status = derive(check_in, check_out, shift, org_tz)
            if not status_value:
                status = derived_status
        else:
            # Manual edits are operator-corrected: attach shift evidence but keep derived figures.
            if check_in is None:
                check_in = existing_in
            if check_out is None:
                check_out = existing_out
            worked, overtime, late, derived_status = derive(check_in, check_out, shift, org_tz)
            if not status_value:
                status = derived_status
        record.shift_id = shift.id if shift else record.shift_id
        record.check_in = check_in
        record.check_out = check_out
        record.worked_minutes = worked
        record.overtime_minutes = overtime
        record.status = status
        record.source = source
        record.notes = data.get("notes", record.notes)
    else:
        record = AttendanceRecord(
            org_id=org_id,
            employee_id=employee.id,
            attendance_date=on_date,
            shift_id=shift.id if shift else None,
            check_in=check_in,
            check_out=check_out,
            worked_minutes=worked,
            overtime_minutes=overtime,
            status=status,
            source=source,
            notes=data.get("notes"),
        )
        db.add(record)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE if existing is None else AuditAction.UPDATE,
        entity_type="attendance_record",
        entity_id=record.id,
        details={"employee_id": str(employee_id), "attendance_date": on_date.isoformat(),
                 "worked_minutes": worked, "overtime_minutes": overtime, "source": source},
        org_id=org_id,
    )
    return record


def import_attendance_csv(
    db: Session, *, org_id: UUID, actor: User, content: str
) -> dict:
    """Import attendance rows; all-or-nothing on validation errors.

    Expected columns: employee_no, attendance_date, check_in, check_out, status, notes
    (check_in/check_out ISO with offset; status optional).
    """
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise ValidationError("CSV file is empty")

    errors: list[str] = []
    parsed: list[dict] = []
    for line, row in enumerate(rows, start=2):
        try:
            employee_no = (row.get("employee_no") or "").strip()
            if not employee_no:
                raise ValueError("employee_no missing")
            employee = db.scalar(
                select(Employee).where(
                    Employee.org_id == org_id, Employee.employee_no == employee_no
                )
            )
            if employee is None:
                raise ValueError(f"employee {employee_no} not found")
            parsed.append({
                "employee_id": employee.id,
                "attendance_date": (row.get("attendance_date") or "").strip(),
                "check_in": (row.get("check_in") or "").strip() or None,
                "check_out": (row.get("check_out") or "").strip() or None,
                "status": (row.get("status") or "").strip() or None,
                "notes": (row.get("notes") or "").strip() or None,
                "shift_id": (row.get("shift_id") or "").strip() or None,
            })
        except ValueError as exc:
            errors.append(f"row {line}: {exc}")

    if errors:
        raise BulkImportError(errors)

    applied = 0
    for row in parsed:
        upsert_attendance(
            db, org_id=org_id, actor=actor, employee_id=row["employee_id"],
            data={**row, "source": "csv"},
        )
        applied += 1

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="attendance_import",
        details={"rows": applied},
        org_id=org_id,
    )
    return {"rows_imported": applied}


def _week_off_weekdays(db: Session, org_id: UUID, on_date: date) -> set[int]:
    schedule = db.scalar(
        select(WorkSchedule)
        .where(
            WorkSchedule.org_id == org_id,
            WorkSchedule.active,
            WorkSchedule.effective_from <= on_date,
        )
        .order_by(WorkSchedule.effective_from.desc())
    )
    if schedule is None:
        # No explicit schedule: default Saturday + Sunday off.
        return {6, 7}
    if schedule.effective_to is not None and on_date > schedule.effective_to:
        return {6, 7}
    days = schedule.weekly_off or []
    return {int(d) for d in days}


def reconcile_attendance(
    db: Session, *, org_id: UUID, actor: User | None, on_date: date | None = None
) -> dict:
    """Fill gaps for a date; idempotent (existing records are kept)."""
    on_date = on_date or _utcnow().date()
    weekday = on_date.isoweekday()
    week_off = weekday in _week_off_weekdays(db, org_id, on_date)

    holiday = db.scalar(
        select(Holiday).where(Holiday.org_id == org_id, Holiday.holiday_date == on_date)
    )

    employees = db.scalars(
        select(Employee).where(
            Employee.org_id == org_id,
            Employee.status.in_(["active", "probation", "on_leave", "notice"]),
        )
    ).all()

    counts = {"ABSENT": 0, "LEAVE": 0, "HOLIDAY": 0, "WEEK_OFF": 0, "existing": 0}
    for employee in employees:
        existing = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee.id,
                AttendanceRecord.attendance_date == on_date,
            )
        )
        if existing is not None:
            counts["existing"] += 1
            continue

        if holiday is not None:
            status = AttendanceStatus.HOLIDAY
        elif week_off:
            status = AttendanceStatus.WEEK_OFF
        else:
            on_leave = db.scalar(
                select(LeaveRequest).where(
                    LeaveRequest.employee_id == employee.id,
                    LeaveRequest.status == LeaveStatus.APPROVED,
                    LeaveRequest.start_date <= on_date,
                    LeaveRequest.end_date >= on_date,
                )
            )
            status = AttendanceStatus.LEAVE if on_leave else AttendanceStatus.ABSENT

        db.add(AttendanceRecord(
            org_id=org_id,
            employee_id=employee.id,
            attendance_date=on_date,
            shift_id=None,
            check_in=None,
            check_out=None,
            worked_minutes=0,
            overtime_minutes=0,
            status=status,
            source="reconciliation",
            notes=None,
        ))
        counts[status.value.upper()] += 1

    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="attendance_reconciliation",
        details={"date": on_date.isoformat(), "counts": counts},
        org_id=org_id,
    )
    return {"date": on_date.isoformat(), "counts": counts}


def list_attendance(
    db: Session,
    *,
    org_id: UUID,
    user: User,
    start: date,
    end: date,
    employee_id: UUID | None = None,
) -> list[AttendanceRecord]:
    """Scoped list: admin/hr all; manager reports+self; others self only."""
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.org_id == org_id,
        AttendanceRecord.attendance_date >= start,
        AttendanceRecord.attendance_date <= end,
    )
    if employee_id is not None:
        stmt = stmt.where(AttendanceRecord.employee_id == employee_id)

    if user.role in {Role.ADMIN, Role.SUPER_ADMIN, Role.HR, Role.AUDITOR}:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        reports = select(Employee.id).where(
            (Employee.id == user.employee_id) | (Employee.manager_id == user.employee_id)
        )
        stmt = stmt.where(AttendanceRecord.employee_id.in_(reports))
    elif user.employee_id:
        stmt = stmt.where(AttendanceRecord.employee_id == user.employee_id)
    else:
        return []
    return list(db.scalars(stmt.order_by(AttendanceRecord.attendance_date.desc())))


def upsert_biometric_punches(
    db: Session,
    *,
    org_id: UUID,
    device_code: str,
    punches: list[dict],
) -> dict:
    """Apply punches received from an HMAC-verified device. No actor; machine origin."""
    applied = 0
    for punch in punches:
        employee_no = str(punch.get("employee_no") or "").strip()
        if not employee_no:
            raise ValidationError("punch missing employee_no")
        employee = db.scalar(
            select(Employee).where(
                Employee.org_id == org_id, Employee.employee_no == employee_no
            )
        )
        if employee is None:
            raise NotFoundError(f"employee {employee_no} not found")
        data = {
            "attendance_date": punch.get("attendance_date"),
            "check_in": punch.get("check_in"),
            "check_out": punch.get("check_out"),
            "shift_id": punch.get("shift_id"),
            "source": f"biometric:{device_code}",
        }
        upsert_attendance(
            db, org_id=org_id, actor=None, employee_id=employee.id, data=data
        )
        applied += 1

    audit.record(
        db,
        actor=None,
        action=AuditAction.CREATE,
        entity_type="biometric_punch",
        details={"device": device_code, "punches": applied},
        org_id=org_id,
    )
    return {"punches_applied": applied}

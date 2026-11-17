"""Team calendar merging leave, attendance, holidays and week-offs (R2)."""

from __future__ import annotations

import calendar
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import AttendanceStatus, LeaveStatus, Role
from ..models import (
    AttendanceRecord,
    Employee,
    Holiday,
    LeaveRequest,
    User,
)
from .attendance import _week_off_weekdays
from .errors import ValidationError


def _month_range(month: str) -> tuple[date, date]:
    try:
        year, mm = int(month[:4]), int(month[5:])
        if month[4] != "-" or not 1 <= mm <= 12:
            raise ValueError
    except (ValueError, IndexError):
        raise ValidationError("month must be YYYY-MM") from None
    first = date(year, mm, 1)
    last = date(year, mm, calendar.monthrange(year, mm)[1])
    return first, last


def _scoped_employees(db: Session, org_id: UUID, user: User) -> list[Employee]:
    stmt = select(Employee).where(Employee.org_id == org_id)
    if user.role in {Role.ADMIN, Role.HR, Role.AUDITOR}:
        return list(db.scalars(stmt))
    if user.role == Role.MANAGER and user.employee_id:
        return list(
            db.scalars(
                stmt.where(
                    (Employee.id == user.employee_id)
                    | (Employee.manager_id == user.employee_id)
                )
            )
        )
    if user.employee_id:
        return list(db.scalars(stmt.where(Employee.id == user.employee_id)))
    return []


def team_calendar(db: Session, *, org_id: UUID, user: User, month: str) -> dict:
    """Return a per-employee day grid for one calendar month.

    Each cell reports: holiday name, week-off, approved leave (type not
    resolved here — count of leave days), attendance status (if recorded),
    or nothing (an unreconciled working day renders as null and should be
    reconciled via the attendance reconciliation job).
    """
    first, last = _month_range(month)

    holidays = {
        h.holiday_date: h.name
        for h in db.scalars(
            select(Holiday).where(Holiday.org_id == org_id, Holiday.holiday_date >= first,
                                  Holiday.holiday_date <= last)
        )
    }

    employees = _scoped_employees(db, org_id, user)
    employee_ids = [e.id for e in employees]

    leave_rows = list(
        db.scalars(
            select(LeaveRequest).where(
                LeaveRequest.org_id == org_id,
                LeaveRequest.employee_id.in_(employee_ids or [UUID(int=0)]),
                LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.start_date <= last,
                LeaveRequest.end_date >= first,
            )
        )
    ) if employee_ids else []
    leave_by_employee: dict[UUID, list[LeaveRequest]] = {}
    for row in leave_rows:
        leave_by_employee.setdefault(row.employee_id, []).append(row)

    attendance_rows = list(
        db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.org_id == org_id,
                AttendanceRecord.employee_id.in_(employee_ids),
                AttendanceRecord.attendance_date >= first,
                AttendanceRecord.attendance_date <= last,
            )
        )
    ) if employee_ids else []
    attendance_by_key = {(a.employee_id, a.attendance_date): a for a in attendance_rows}

    week_off_cache: dict[date, bool] = {}

    def is_week_off(day: date) -> bool:
        if day not in week_off_cache:
            week_off_cache[day] = day.isoweekday() in _week_off_weekdays(db, org_id, day)
        return week_off_cache[day]

    grid: list[dict] = []
    day_count = (last - first).days + 1
    for employee in employees:
        days: list[dict] = []
        leaves = leave_by_employee.get(employee.id, [])
        for offset in range(day_count):
            day = first.fromordinal(first.toordinal() + offset)
            cell: dict = {"date": day.isoformat()}
            if day in holidays:
                cell["status"] = AttendanceStatus.HOLIDAY.value
                cell["detail"] = holidays[day]
            elif is_week_off(day):
                cell["status"] = AttendanceStatus.WEEK_OFF.value
            else:
                record = attendance_by_key.get((employee.id, day))
                on_leave = any(lr.start_date <= day <= lr.end_date for lr in leaves)
                if record is not None:
                    cell["status"] = record.status.value
                    cell["worked_minutes"] = record.worked_minutes
                    cell["overtime_minutes"] = record.overtime_minutes
                elif on_leave:
                    cell["status"] = AttendanceStatus.LEAVE.value
                else:
                    cell["status"] = None
            days.append(cell)
        grid.append({
            "employee_id": str(employee.id),
            "employee_no": employee.employee_no,
            "name": f"{employee.first_name} {employee.last_name}",
            "days": days,
        })

    return {"month": month, "from": first.isoformat(), "to": last.isoformat(), "employees": grid}

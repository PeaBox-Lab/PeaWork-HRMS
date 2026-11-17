from __future__ import annotations

import calendar as pycalendar
from datetime import date, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import EmployeeStatus, LeaveStatus, Role
from ..models import Employee, Holiday, LeaveRequest, LeaveType, User
from ..schemas import LeaveDecision, LeaveRequestCreate
from ..services import policy
from ..services.errors import DomainError
from ..services.leave import (
    balances_for,
    cancel_leave,
    decide_leave,
    list_leave_requests,
    pending_approvals,
    request_leave,
)
from ..templating import render
from .forms import FormError, parse_date, parse_uuid, text, validation_message

router = APIRouter(dependencies=[Depends(csrf_guard)])


@router.get("/leave")
def leave_page(
    request: Request,
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    page = max(1, page)
    leaves, total = list_leave_requests(
        db, actor=user, status=status or None, limit=30, offset=(page - 1) * 30
    )
    balances = (
        balances_for(db, org_id=user.org_id, employee_id=user.employee_id, year=date.today().year)
        if user.employee_id
        else []
    )
    leave_types = list(
        db.scalars(
            select(LeaveType).where(LeaveType.org_id == user.org_id, LeaveType.active.is_(True))
        ).all()
    )
    holidays = list(
        db.scalars(
            select(Holiday)
            .where(Holiday.org_id == user.org_id, Holiday.holiday_date >= date.today())
            .order_by(Holiday.holiday_date)
            .limit(6)
        ).all()
    )
    approvals = pending_approvals(db, user)
    employees = []
    if user.role in {Role.ADMIN, Role.HR}:
        employees = list(
            db.scalars(
                select(Employee)
                .where(Employee.org_id == user.org_id, Employee.status != EmployeeStatus.EXITED)
                .order_by(Employee.first_name)
            ).all()
        )
    return render(
        request,
        "leave.html",
        leaves=leaves,
        total=total,
        page=page,
        pages=max(1, (total + 29) // 30),
        status=status,
        balances=balances,
        leave_types=leave_types,
        holidays=holidays,
        approvals=approvals,
        employees=employees,
        can_approve=user.role in {Role.ADMIN, Role.HR, Role.MANAGER},
        today=date.today(),
    )


@router.post("/leave/request")
def leave_request_submit(
    request: Request,
    leave_type_id: str = Form(""),
    employee_id: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    reason: str = Form(""),
    half_day: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        data = LeaveRequestCreate(
            employee_id=parse_uuid(employee_id, "Employee"),
            leave_type_id=parse_uuid(leave_type_id, "Leave type") or UUID(int=0),
            start_date=parse_date(start_date, "Start date"),
            end_date=parse_date(end_date, "End date"),
            reason=text(reason),
            half_day=bool(half_day),
        )
        request_leave(db, actor=user, data=data, request=request)
    except (PydanticValidationError, FormError, DomainError, ValueError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return RedirectResponse(f"/leave?error={quote(message)}", status_code=303)
    return RedirectResponse("/leave?msg=Leave+request+submitted", status_code=303)


@router.post("/leave/{leave_id}/decision")
def leave_decision_submit(
    leave_id: UUID,
    request: Request,
    decision: str = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        decide_leave(
            db,
            actor=user,
            leave_id=leave_id,
            decision=LeaveDecision(decision=decision, comment=text(comment)),
            request=request,
        )
    except (PydanticValidationError, DomainError, ValueError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return RedirectResponse(f"/leave?error={quote(message)}", status_code=303)
    return RedirectResponse(f"/leave?msg=Leave+{decision}d", status_code=303)


@router.post("/leave/{leave_id}/cancel")
def leave_cancel(
    leave_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        cancel_leave(db, actor=user, leave_id=leave_id, request=request)
    except DomainError as exc:
        return RedirectResponse(f"/leave?error={quote(exc.message)}", status_code=303)
    return RedirectResponse("/leave?msg=Leave+cancelled", status_code=303)


@router.get("/leave/calendar")
def leave_calendar(
    request: Request,
    month: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    today = date.today()
    try:
        year, month_number = (
            (int(part) for part in month.split("-", 1)) if month else (today.year, today.month)
        )
        month_start = date(year, month_number, 1)
    except ValueError:
        month_start = date(today.year, today.month, 1)
    month_end = date(
        month_start.year, month_start.month, pycalendar.monthrange(month_start.year, month_start.month)[1]
    )

    stmt = select(LeaveRequest).where(
        LeaveRequest.org_id == user.org_id,
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= month_end,
        LeaveRequest.end_date >= month_start,
    )
    if not policy.is_privileged_reader(user):
        if user.role == Role.MANAGER and user.employee_id:
            team_ids = select(Employee.id).where(
                Employee.org_id == user.org_id,
                Employee.manager_id == user.employee_id,
            )
            stmt = stmt.where(LeaveRequest.employee_id.in_(team_ids))
        elif user.employee_id:
            stmt = stmt.where(LeaveRequest.employee_id == user.employee_id)
        else:
            stmt = stmt.where(false)
    leaves = list(db.scalars(stmt).all())
    events: dict[date, list[str]] = {}
    for leave in leaves:
        cursor = max(leave.start_date, month_start)
        last = min(leave.end_date, month_end)
        while cursor <= last:
            if cursor.weekday() < 5:
                events.setdefault(cursor, []).append(
                    f"{leave.employee.full_name} · {leave.leave_type.name}"
                )
            cursor = date.fromordinal(cursor.toordinal() + 1)

    holidays = {
        holiday.holiday_date: holiday.name
        for holiday in db.scalars(
            select(Holiday).where(
                Holiday.org_id == user.org_id,
                Holiday.holiday_date >= month_start,
                Holiday.holiday_date <= month_end,
            )
        ).all()
    }
    weeks = pycalendar.monthcalendar(month_start.year, month_start.month)
    prev_month = (month_start.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    next_month = (month_end + timedelta(days=1)).strftime("%Y-%m")
    return render(
        request,
        "calendar.html",
        month_start=month_start,
        weeks=weeks,
        events=events,
        holidays=holidays,
        prev_month=prev_month,
        next_month=next_month,
        today=today,
    )


__all__ = ["router"]

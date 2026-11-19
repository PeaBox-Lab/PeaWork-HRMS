"""Talent workspace (R4): check-ins and 360 feedback.

One page, role-aware: everyone sees the check-ins and feedback requests that
concern them, HR/admin additionally get the create forms. All writes go through
``peawork.services.talent`` so the API and the UI share the same rules.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import (
    EmployeeStatus,
    FeedbackRelationship,
    Role,
)
from ..models import Employee, User
from ..services import talent as talent_service
from ..services.errors import DomainError
from ..templating import render
from .forms import FormError, parse_date, text

router = APIRouter(dependencies=[Depends(csrf_guard)])

HR_ROLES = {Role.ADMIN, Role.SUPER_ADMIN, Role.HR}
TALENT_ROLES = {Role.ADMIN, Role.SUPER_ADMIN, Role.HR, Role.MANAGER, Role.EMPLOYEE}
READ_ONLY_ROLES = {Role.AUDITOR, Role.PAYROLL}

STATUS_LABELS = {
    "pending": "Pending",
    "submitted": "Submitted",
    "declined": "Declined",
    "skipped": "Skipped",
    "open": "Open",
    "closed": "Closed",
}

RELATIONSHIP_LABELS = {
    item.value: item.label for item in FeedbackRelationship
}


def _error_redirect(target: str, exc: Exception) -> RedirectResponse:
    message = getattr(exc, "message", None) or str(exc)
    return RedirectResponse(f"{target}?error={quote(message)}", status_code=303)


def _can_create(user: User) -> bool:
    return user.role in TALENT_ROLES


def _scoped_employees(db: Session, user: User) -> list[Employee]:
    stmt = select(Employee).where(
        Employee.org_id == user.org_id, Employee.status != EmployeeStatus.EXITED
    )
    if user.role in HR_ROLES:
        pass
    elif user.employee_id:
        stmt = stmt.where(Employee.id == user.employee_id)
    else:
        return []
    return list(
        db.scalars(stmt.order_by(Employee.first_name, Employee.last_name)).all()
    )


def _page_context(db: Session, user: User) -> dict:
    check_ins = talent_service.list_check_ins(db, org_id=user.org_id, user=user)
    inbox = talent_service.list_my_feedback_requests(
        db, org_id=user.org_id, actor=user
    )
    about_me: list[dict] = []
    if user.employee_id:
        try:
            about_me = talent_service.list_feedback_for_employee(
                db, org_id=user.org_id, actor=user, employee_id=str(user.employee_id)
            )
        except DomainError:
            about_me = []

    cycles: dict[str, dict] = {}
    for item in check_ins:
        if item["cycle_code"] and item["cycle_code"] not in cycles:
            cycles[item["cycle_code"]] = {
                "code": item["cycle_code"],
                "status": item["cycle_status"],
            }
    if user.role in HR_ROLES:
        for cycle in talent_service.list_cycles(db, org_id=user.org_id, user=user):
            cycles.setdefault(
                cycle["code"], {"code": cycle["code"], "status": cycle["status"]}
            )

    return {
        "check_ins": check_ins,
        "inbox": inbox,
        "about_me": about_me,
        "cycles": sorted(cycles.values(), key=lambda row: row["code"], reverse=True),
        "open_cycles": [row for row in cycles.values() if row["status"] != "closed"],
        "employees": _scoped_employees(db, user),
        "relationships": [
            (item.value, item.label) for item in FeedbackRelationship
        ],
        "status_labels": STATUS_LABELS,
        "relationship_labels": RELATIONSHIP_LABELS,
        "is_hr": user.role in HR_ROLES,
        "can_create": _can_create(user),
        "is_read_only": user.role in READ_ONLY_ROLES,
        "today": date.today().isoformat(),
        "employee_id": str(user.employee_id) if user.employee_id else None,
    }


@router.get("/talent")
def talent_index(
    request: Request,
    cycle: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    context = _page_context(db, user)
    check_ins = context["check_ins"]
    if cycle:
        check_ins = [row for row in check_ins if row["cycle_code"] == cycle]
    return render(
        request,
        "talent/index.html",
        check_ins=check_ins,
        cycle=cycle,
        **{key: value for key, value in context.items() if key != "check_ins"},
    )


@router.post("/talent/check-ins")
def talent_create_check_in(
    cycle_code: str = Form(""),
    employee_id: str = Form(""),
    due_on: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        if not cycle_code:
            raise FormError("Choose an open review cycle")
        if not employee_id:
            raise FormError("Choose an employee")
        talent_service.create_check_in(
            db,
            org_id=user.org_id,
            actor=user,
            cycle_code=cycle_code,
            employee_id=employee_id,
            due_on=parse_date(due_on, "Due date") if due_on else None,
        )
    except (FormError, DomainError, ValueError) as exc:
        return _error_redirect("/talent", exc)
    return RedirectResponse("/talent?msg=Check-in+opened", status_code=303)


@router.post("/talent/check-ins/{check_in_id}/submit")
def talent_submit_check_in(
    check_in_id: UUID,
    employee_notes: str = Form(""),
    manager_notes: str = Form(""),
    rating: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target = "/talent"
    try:
        talent_service.submit_check_in(
            db,
            org_id=user.org_id,
            actor=user,
            check_in_id=str(check_in_id),
            employee_notes=text(employee_notes),
            manager_notes=text(manager_notes),
            rating=rating or None,
        )
    except (DomainError, ValueError) as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Check-in+saved", status_code=303)


@router.post("/talent/feedback-requests")
def talent_create_feedback_request(
    subject_employee_id: str = Form(""),
    reviewer_employee_id: str = Form(""),
    cycle_code: str = Form(""),
    relationship: str = Form("peer"),
    anonymous: str = Form(""),
    due_on: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        if not subject_employee_id:
            raise FormError("Choose the employee the feedback is about")
        talent_service.create_feedback_request(
            db,
            org_id=user.org_id,
            actor=user,
            subject_employee_id=subject_employee_id,
            reviewer_employee_id=reviewer_employee_id or None,
            cycle_code=cycle_code or None,
            relationship=relationship or "peer",
            anonymous=anonymous.lower() in {"1", "on", "true", "yes"},
            due_on=parse_date(due_on, "Due date") if due_on else None,
        )
    except (FormError, DomainError, ValueError) as exc:
        return _error_redirect("/talent", exc)
    return RedirectResponse("/talent?msg=Feedback+requested", status_code=303)


@router.post("/talent/feedback/{request_id}/submit")
def talent_submit_feedback(
    request_id: UUID,
    strengths: str = Form(""),
    improvements: str = Form(""),
    comments: str = Form(""),
    rating: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        talent_service.submit_feedback(
            db,
            org_id=user.org_id,
            actor=user,
            request_id=str(request_id),
            strengths=text(strengths),
            improvements=text(improvements),
            comments=text(comments),
            rating=rating or None,
        )
    except (DomainError, ValueError) as exc:
        return _error_redirect("/talent", exc)
    return RedirectResponse("/talent?msg=Feedback+submitted", status_code=303)


@router.post("/talent/feedback/{request_id}/decline")
def talent_decline_feedback(
    request_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        talent_service.decline_feedback(
            db, org_id=user.org_id, actor=user, request_id=str(request_id)
        )
    except DomainError as exc:
        return _error_redirect("/talent", exc)
    return RedirectResponse("/talent?msg=Feedback+declined", status_code=303)


__all__ = ["router"]

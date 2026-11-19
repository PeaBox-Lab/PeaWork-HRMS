"""Job changes UI: event + reason, current vs proposed, manager → HR approval."""
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
from ..domain import EmployeeStatus, EmploymentType, JobChangeEvent, Role
from ..models import Department, Employee, Position
from ..services import job_change as job_change_service
from ..services.errors import DomainError
from ..templating import render
from .forms import FormError, text

router = APIRouter(dependencies=[Depends(csrf_guard)])

HR_ROLES = {Role.ADMIN, Role.SUPER_ADMIN, Role.HR}
READ_ONLY_ROLES = {Role.AUDITOR, Role.PAYROLL}

STATUS_LABELS = {
    "draft": "Draft",
    "pending_approval": "Awaiting approval",
    "approved": "Approved — takes effect on the effective date",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
    "completed": "Applied",
}

FIELD_LABELS = {
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


def _error_redirect(target: str, exc: Exception) -> RedirectResponse:
    message = getattr(exc, "message", None) or str(exc)
    return RedirectResponse(f"{target}?error={quote(message)}", status_code=303)


def _scoped_employees(db: Session, user) -> list[Employee]:
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


def _page_context(db: Session, user) -> dict:
    employees = _scoped_employees(db, user)
    return {
        "employees": employees,
        "employees_by_id": {str(emp.id): emp for emp in employees},
        "events": job_change_service.event_options(),
        "reason_labels": job_change_service.REASON_LABELS,
        "status_labels": STATUS_LABELS,
        "field_labels": FIELD_LABELS,
        "employment_types": [(item.value, item.value.replace("_", " ").title()) for item in EmploymentType],
        "departments": list(
            db.scalars(
                select(Department)
                .where(Department.org_id == user.org_id, Department.active.is_(True))
                .order_by(Department.name)
            ).all()
        ),
        "positions": list(
            db.scalars(
                select(Position)
                .where(Position.org_id == user.org_id, Position.active.is_(True))
                .order_by(Position.title)
            ).all()
        ),
        "is_hr": user.role in HR_ROLES,
        "is_read_only": user.role in READ_ONLY_ROLES,
        "today": date.today().isoformat(),
    }


def _form_values(employee: Employee | None) -> dict:
    if employee is None:
        return {}
    return {
        "department_id": str(employee.department_id or ""),
        "position_id": str(employee.position_id or ""),
        "manager_id": str(employee.manager_id or ""),
        "location": employee.location or "",
        "work_email": employee.work_email or "",
        "personal_email": employee.personal_email or "",
        "phone": employee.phone or "",
        "employment_type": employee.employment_type.value,
        "status": employee.status.value,
    }


@router.get("/job-changes")
def job_changes_index(
    request: Request,
    status: str = "",
    event: str = "",
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    rows = job_change_service.list_job_changes(
        db, org_id=user.org_id, user=user, status=status or None, event=event or None
    )
    all_rows = job_change_service.list_job_changes(db, org_id=user.org_id, user=user)
    counts = {key: 0 for key in STATUS_LABELS}
    for row in all_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return render(
        request,
        "job_changes/index.html",
        rows=rows,
        counts=counts,
        total=len(all_rows),
        status=status,
        event=event,
        **_page_context(db, user),
    )


@router.get("/job-changes/new")
def job_change_new(
    request: Request,
    event: str = JobChangeEvent.PROMOTION.value,
    employee_id: str = "",
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    if user.role in READ_ONLY_ROLES:
        return render(
            request,
            "error.html",
            status_code=403,
            code=403,
            message="Your role cannot raise job changes.",
        )
    try:
        event_enum = JobChangeEvent(event)
    except ValueError:
        event_enum = JobChangeEvent.PROMOTION
    employee = None
    if employee_id:
        try:
            employee = db.get(Employee, UUID(employee_id))
        except (ValueError, TypeError):
            employee = None
    if employee is not None and employee.org_id != user.org_id:
        employee = None
    fields = job_change_service.EVENT_FIELDS[event_enum]
    return render(
        request,
        "job_changes/new.html",
        selected_event=event_enum.value,
        reasons=job_change_service.reason_options(event_enum),
        fields=fields,
        values=_form_values(employee or (user.employee if getattr(user, "employee", None) else None)),
        selected_employee=str(employee.id) if employee else str(user.employee_id or ""),
        error=None,
        **_page_context(db, user),
    )


@router.post("/job-changes/new")
def job_change_create(
    request: Request,
    employee_id: str = Form(""),
    event: str = Form(""),
    reason_code: str = Form(""),
    effective_date: str = Form(""),
    notes: str = Form(""),
    department_id: str = Form(""),
    position_id: str = Form(""),
    manager_id: str = Form(""),
    location: str = Form(""),
    work_email: str = Form(""),
    personal_email: str = Form(""),
    phone: str = Form(""),
    employment_type: str = Form(""),
    status: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    proposed = {
        "department_id": department_id,
        "position_id": position_id,
        "manager_id": manager_id,
        "location": location,
        "work_email": work_email,
        "personal_email": personal_email,
        "phone": phone,
        "employment_type": employment_type,
        "status": status,
    }
    proposed = {key: value for key, value in proposed.items() if value not in ("", None)}
    try:
        if not employee_id:
            raise FormError("Choose the employee")
        if not effective_date:
            raise FormError("Choose an effective date")
        created = job_change_service.create_job_change(
            db,
            org_id=user.org_id,
            actor=user,
            employee_id=employee_id,
            event=event,
            reason_code=reason_code,
            effective_date=date.fromisoformat(effective_date),
            proposed=proposed,
            notes=text(notes),
        )
    except (FormError, DomainError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        try:
            event_enum = JobChangeEvent(event)
        except ValueError:
            event_enum = JobChangeEvent.PROMOTION
        return render(
            request,
            "job_changes/new.html",
            selected_event=event_enum.value,
            reasons=job_change_service.reason_options(event_enum),
            fields=job_change_service.EVENT_FIELDS[event_enum],
            values={"department_id": department_id, "position_id": position_id,
                    "manager_id": manager_id, "location": location,
                    "work_email": work_email, "personal_email": personal_email,
                    "phone": phone, "employment_type": employment_type,
                    "status": status},
            selected_employee=employee_id,
            error=message,
            **_page_context(db, user),
        )
    return RedirectResponse(
        f"/job-changes/{created['id']}?msg=Job+change+drafted", status_code=303
    )


@router.get("/job-changes/{cr_id}")
def job_change_detail(
    cr_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        row = job_change_service.get_job_change(
            db, org_id=user.org_id, actor=user, cr_id=cr_id
        )
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    employee = db.get(Employee, UUID(row["employee_id"])) if row["employee_id"] else None
    can_manage = user.role in HR_ROLES or bool(
        employee
        and user.employee_id
        and (
            employee.id == user.employee_id
            or (user.role == Role.MANAGER and employee.manager_id == user.employee_id)
        )
    )
    is_manager = bool(
        user.role == Role.MANAGER
        and user.employee_id
        and employee
        and employee.manager_id == user.employee_id
    )
    return render(
        request,
        "job_changes/detail.html",
        row=row,
        employee=employee,
        can_manage=can_manage,
        can_approve=row["status"] in {"draft", "pending_approval"}
        and (user.role in HR_ROLES or is_manager),
        **_page_context(db, user),
    )


@router.post("/job-changes/{cr_id}/submit")
def job_change_submit(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        job_change_service.submit_job_change(
            db, org_id=user.org_id, actor=user, cr_id=cr_id
        )
    except DomainError as exc:
        return _error_redirect(f"/job-changes/{cr_id}", exc)
    return RedirectResponse(
        f"/job-changes/{cr_id}?msg=Submitted+for+approval", status_code=303
    )


@router.post("/job-changes/{cr_id}/approve")
def job_change_approve(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        row = job_change_service.approve_job_change(
            db, org_id=user.org_id, actor=user, cr_id=cr_id
        )
    except DomainError as exc:
        return _error_redirect(f"/job-changes/{cr_id}", exc)
    message = "Applied" if row["status"] == "completed" else "Approved"
    return RedirectResponse(
        f"/job-changes/{cr_id}?msg={quote(message)}", status_code=303
    )


@router.post("/job-changes/{cr_id}/reject")
def job_change_reject(
    cr_id: UUID,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        job_change_service.reject_job_change(
            db, org_id=user.org_id, actor=user, cr_id=cr_id, reason=text(reason)
        )
    except DomainError as exc:
        return _error_redirect(f"/job-changes/{cr_id}", exc)
    return RedirectResponse(
        f"/job-changes/{cr_id}?msg=Job+change+rejected", status_code=303
    )


@router.post("/job-changes/{cr_id}/cancel")
def job_change_cancel(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        job_change_service.cancel_job_change(
            db, org_id=user.org_id, actor=user, cr_id=cr_id
        )
    except DomainError as exc:
        return _error_redirect(f"/job-changes/{cr_id}", exc)
    return RedirectResponse(
        f"/job-changes/{cr_id}?msg=Job+change+cancelled", status_code=303
    )


@router.post("/job-changes/apply-due")
def job_change_apply_due(
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    applied = job_change_service.apply_due_job_changes(
        db, org_id=user.org_id, actor=user
    )
    return RedirectResponse(
        f"/job-changes?msg={quote(f'Applied {len(applied)} job change(s)')}",
        status_code=303,
    )


__all__ = ["router"]

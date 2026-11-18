from __future__ import annotations

from datetime import date
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import (
    ChangeRequestStatus,
    ChangeRequestType,
    EmployeeStatus,
    EmploymentType,
    Role,
)
from ..models import ChangeRequest, Department, Employee, Position, User
from ..services.errors import DomainError
from ..services.lifecycle import (
    SELF_SERVICE_FIELDS,
    approve_change_request,
    cancel_change_request,
    complete_change_request,
    create_change_request,
    get_change_request,
    list_change_requests,
    reject_change_request,
    submit_change_request,
    update_change_request,
)
from ..templating import render
from .forms import FormError, parse_date, parse_uuid, text

router = APIRouter(dependencies=[Depends(csrf_guard)])

HR_ROLES = {Role.ADMIN, Role.HR}
READ_ONLY_ROLES = {Role.AUDITOR, Role.PAYROLL}
LIMIT = 200

STATUS_LABELS = {
    ChangeRequestStatus.DRAFT.value: "Draft",
    ChangeRequestStatus.PENDING_APPROVAL.value: "Awaiting approval",
    ChangeRequestStatus.APPROVED.value: "Approved",
    ChangeRequestStatus.REJECTED.value: "Rejected",
    ChangeRequestStatus.CANCELLED.value: "Cancelled",
    ChangeRequestStatus.COMPLETED.value: "Completed",
}

FIELD_MODELS = {
    "department_id": Department,
    "position_id": Position,
    "manager_id": Employee,
}

FIELD_LABELS = {
    "work_email": "Work email",
    "personal_email": "Personal email",
    "phone": "Phone",
    "location": "Location",
    "gender": "Gender",
    "date_of_joining": "Date of joining",
    "date_of_birth": "Date of birth",
    "employment_type": "Employment type",
    "status": "Employment status",
    "department_id": "Department",
    "position_id": "Position",
    "manager_id": "Manager",
}


def _scoped_employees(db: Session, user: User) -> list[Employee]:
    stmt = select(Employee).where(
        Employee.org_id == user.org_id, Employee.status != EmployeeStatus.EXITED
    )
    if user.role in HR_ROLES:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        stmt = stmt.where(
            or_(
                Employee.id == user.employee_id,
                Employee.manager_id == user.employee_id,
            )
        )
    elif user.employee_id:
        stmt = stmt.where(Employee.id == user.employee_id)
    else:
        stmt = stmt.where(false())
    return list(
        db.scalars(stmt.order_by(Employee.first_name, Employee.last_name)).all()
    )


def _next_code(db: Session, org_id: UUID) -> str:
    prefix = f"CR-{date.today().year}-"
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


def _describe_details(db: Session, details: dict | None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, value in (details or {}).items():
        label = FIELD_LABELS.get(key, str(key).replace("_", " ").title())
        model = FIELD_MODELS.get(key)
        if model is not None and value:
            target = db.get(model, UUID(str(value))) if _is_uuid(value) else None
            if target is not None:
                value = getattr(target, "name", None) or getattr(
                    target, "title", None
                ) or getattr(target, "full_name", None) or value
        rows.append((label, "—" if value in (None, "") else str(value)))
    return rows


def _is_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True


def _error_redirect(target: str, exc: Exception) -> RedirectResponse:
    message = getattr(exc, "message", None) or str(exc)
    return RedirectResponse(f"{target}?error={quote(message)}", status_code=303)


def _page_context(db: Session, user: User) -> dict:
    return {
        "employees": _scoped_employees(db, user),
        "change_types": list(ChangeRequestType),
        "change_type_options": [
            (item.value, item.value.replace("_", " ").title())
            for item in ChangeRequestType
        ],
        "status_options": [
            (item.value, STATUS_LABELS[item.value]) for item in ChangeRequestStatus
        ],
        "is_hr": user.role in HR_ROLES,
        "is_read_only": user.role in READ_ONLY_ROLES,
        "self_service_fields": sorted(SELF_SERVICE_FIELDS),
        "status_labels": STATUS_LABELS,
        "today": date.today(),
    }


@router.get("/lifecycle")
def lifecycle_index(
    request: Request,
    status: str = "",
    change_type: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    everything = list_change_requests(
        db, org_id=user.org_id, user=user, limit=LIMIT
    )
    counts = {value.value: 0 for value in ChangeRequestStatus}
    for item in everything:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    requests = [
        item
        for item in everything
        if (not status or item["status"] == status)
        and (not change_type or item["change_type"] == change_type)
    ]
    employee_ids = {item["employee_id"] for item in requests if item["employee_id"]}
    employees_by_id = {
        str(employee.id): employee
        for employee in db.scalars(
            select(Employee).where(
                Employee.org_id == user.org_id,
                Employee.id.in_([UUID(key) for key in employee_ids]),
            )
        ).all()
    } if employee_ids else {}

    pending = [
        item for item in everything if item["status"] == ChangeRequestStatus.PENDING_APPROVAL
    ] if user.role in HR_ROLES else []

    return render(
        request,
        "lifecycle/index.html",
        requests=requests,
        employees_by_id=employees_by_id,
        pending=pending,
        counts=counts,
        total=len(everything),
        status=status,
        change_type=change_type,
        can_create=user.role not in READ_ONLY_ROLES,
        can_approve=user.role in HR_ROLES,
        **_page_context(db, user),
    )


@router.get("/lifecycle/new")
def lifecycle_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    if user.role in READ_ONLY_ROLES:
        return render(
            request,
            "error.html",
            status_code=403,
            code=403,
            message="Your role cannot raise lifecycle change requests.",
        )
    return render(
        request,
        "lifecycle/new.html",
        values={
            "employee_id": str(user.employee_id or ""),
            "change_type": ChangeRequestType.LOCATION_CHANGE.value,
            "effective_date": date.today().isoformat(),
            "description": "",
            "location": "",
            "reason": "",
        },
        form_error=None,
        **_page_context(db, user),
    )


@router.post("/lifecycle/new")
def lifecycle_create_submit(
    request: Request,
    employee_id: str = Form(""),
    change_type: str = Form(""),
    effective_date: str = Form(""),
    description: str = Form(""),
    location: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    values = {
        "employee_id": employee_id,
        "change_type": change_type,
        "effective_date": effective_date,
        "description": description,
        "location": location,
        "reason": reason,
    }
    try:
        if not change_type:
            raise FormError("Choose a change type")
        location_value = text(location)
        created = create_change_request(
            db,
            org_id=user.org_id,
            actor=user,
            code=_next_code(db, user.org_id),
            change_type=change_type,
            employee_id=parse_uuid(employee_id, "Employee"),
            effective_date=parse_date(effective_date, "Effective date"),
            description=text(description),
            details={"location": location_value} if location_value else None,
            notes=text(reason),
        )
    except (FormError, DomainError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return render(
            request,
            "lifecycle/new.html",
            values=values,
            form_error=message,
            **_page_context(db, user),
        )
    return RedirectResponse(
        f"/lifecycle/{created['id']}?msg=Change+request+created", status_code=303
    )


@router.get("/lifecycle/{cr_id}")
def lifecycle_detail(
    cr_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        cr = get_change_request(db, org_id=user.org_id, cr_id=str(cr_id), actor=user)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )

    employee = db.get(Employee, UUID(cr["employee_id"])) if cr["employee_id"] else None
    is_hr = user.role in HR_ROLES
    can_manage = is_hr or bool(
        employee
        and user.employee_id
        and (
            employee.id == user.employee_id
            or (user.role == Role.MANAGER and employee.manager_id == user.employee_id)
        )
    )
    return render(
        request,
        "lifecycle/detail.html",
        cr=cr,
        employee=employee,
        details=_describe_details(db, cr["details"]),
        can_manage=can_manage,
        can_approve=is_hr and cr["status"] in {"draft", "pending_approval"},
        can_complete=is_hr and cr["status"] == "approved",
        can_edit=can_manage and cr["status"] == "draft",
        employee_history=(
            list(
                db.scalars(
                    select(ChangeRequest)
                    .where(
                        ChangeRequest.org_id == user.org_id,
                        ChangeRequest.employee_id == employee.id,
                    )
                    .order_by(ChangeRequest.created_at.desc())
                    .limit(10)
                ).all()
            )
            if employee
            else []
        ),
        departments=list(
            db.scalars(
                select(Department)
                .where(Department.org_id == user.org_id, Department.active.is_(True))
                .order_by(Department.name)
            ).all()
        ),
        positions=list(
            db.scalars(
                select(Position)
                .where(Position.org_id == user.org_id, Position.active.is_(True))
                .order_by(Position.title)
            ).all()
        ),
        managers=_scoped_employees(db, user) if is_hr else [],
        employment_types=list(EmploymentType),
        employee_statuses=list(EmployeeStatus),
        **_page_context(db, user),
    )


@router.post("/lifecycle/{cr_id}/submit")
def lifecycle_submit(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target = f"/lifecycle/{cr_id}"
    try:
        submit_change_request(db, org_id=user.org_id, actor=user, cr_id=str(cr_id))
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(
        f"{target}?msg=Submitted+for+approval", status_code=303
    )


@router.post("/lifecycle/{cr_id}/approve")
def lifecycle_approve(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    target = f"/lifecycle/{cr_id}"
    try:
        approve_change_request(db, org_id=user.org_id, actor=user, cr_id=str(cr_id))
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Change+request+approved", status_code=303)


@router.post("/lifecycle/{cr_id}/reject")
def lifecycle_reject(
    cr_id: UUID,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    target = f"/lifecycle/{cr_id}"
    try:
        reject_change_request(
            db,
            org_id=user.org_id,
            actor=user,
            cr_id=str(cr_id),
            reason=text(reason),
        )
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Change+request+rejected", status_code=303)


@router.post("/lifecycle/{cr_id}/cancel")
def lifecycle_cancel(
    cr_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target = f"/lifecycle/{cr_id}"
    try:
        cancel_change_request(db, org_id=user.org_id, actor=user, cr_id=str(cr_id))
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Change+request+cancelled", status_code=303)


@router.get("/lifecycle/{cr_id}/edit")
def lifecycle_edit(
    cr_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        cr = get_change_request(db, org_id=user.org_id, cr_id=str(cr_id), actor=user)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    return render(
        request,
        "lifecycle/form.html",
        cr=cr,
        values={
            "employee_id": cr["employee_id"] or "",
            "effective_date": cr["effective_date"] or "",
            "description": cr["description"] or "",
            "old_value": cr["old_value"] or "",
            "new_value": cr["new_value"] or "",
            "reason": cr["reason"] or "",
            "notes": cr["notes"] or "",
            "location": (cr["details"] or {}).get("location", ""),
        },
        form_error=None,
        **_page_context(db, user),
    )


@router.post("/lifecycle/{cr_id}/edit")
def lifecycle_edit_submit(
    cr_id: UUID,
    request: Request,
    employee_id: str = Form(""),
    effective_date: str = Form(""),
    description: str = Form(""),
    old_value: str = Form(""),
    new_value: str = Form(""),
    reason: str = Form(""),
    notes: str = Form(""),
    location: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target = f"/lifecycle/{cr_id}"
    try:
        current = get_change_request(
            db, org_id=user.org_id, cr_id=str(cr_id), actor=user
        )
        details = dict(current["details"] or {})
        location_value = text(location)
        if location_value:
            details["location"] = location_value
        else:
            details.pop("location", None)

        changes: dict = {
            "effective_date": parse_date(effective_date, "Effective date"),
            "description": text(description),
            "old_value": text(old_value),
            "new_value": text(new_value),
            "reason": text(reason),
            "notes": text(notes),
            "details": details or None,
        }
        if user.role in HR_ROLES:
            changes["employee_id"] = parse_uuid(employee_id, "Employee")
        update_change_request(
            db, org_id=user.org_id, actor=user, cr_id=str(cr_id), changes=changes
        )
    except (FormError, DomainError, ValueError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        current = {
            "id": str(cr_id),
            "employee_id": employee_id or "",
            "effective_date": effective_date,
            "description": description,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "notes": notes,
            "details": {"location": location} if location else {},
            "change_type": "",
            "status": "draft",
            "code": "",
            "created_at": None,
            "approved_at": None,
        }
        return render(
            request,
            "lifecycle/form.html",
            cr=current,
            values={
                "employee_id": employee_id,
                "effective_date": effective_date,
                "description": description,
                "old_value": old_value,
                "new_value": new_value,
                "reason": reason,
                "notes": notes,
                "location": location,
            },
            form_error=message,
            **_page_context(db, user),
        )
    return RedirectResponse(f"{target}?msg=Change+request+updated", status_code=303)


@router.post("/lifecycle/{cr_id}/complete")
def lifecycle_complete(
    cr_id: UUID,
    location: str = Form(""),
    department_id: str = Form(""),
    position_id: str = Form(""),
    manager_id: str = Form(""),
    employment_type: str = Form(""),
    status: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    target = f"/lifecycle/{cr_id}"
    try:
        overrides: dict = {}
        for field, raw in (
            ("location", location),
            ("employment_type", employment_type),
            ("status", status),
        ):
            value = text(raw)
            if value:
                overrides[field] = value
        for field, raw in (
            ("department_id", department_id),
            ("position_id", position_id),
            ("manager_id", manager_id),
        ):
            value = parse_uuid(raw, FIELD_LABELS.get(field, field))
            if value:
                overrides[field] = str(value)

        complete_change_request(
            db,
            org_id=user.org_id,
            actor=user,
            cr_id=str(cr_id),
            details=overrides or None,
        )
    except (FormError, DomainError, ValueError) as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(
        f"{target}?msg=Change+request+completed", status_code=303
    )


__all__ = ["router"]

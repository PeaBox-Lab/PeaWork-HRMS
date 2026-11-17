from __future__ import annotations

from datetime import date
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import AuditAction, DocumentCategory, EmployeeStatus, EmploymentType, Role
from ..models import Department, Employee, LeaveRequest, Position, User
from ..schemas import EmployeeCreate, EmployeeUpdate
from ..services import audit, policy
from ..services.documents import (
    acknowledge_document,
    delete_document,
    file_path,
    get_document,
    list_documents,
    save_document,
)
from ..services.employees import (
    create_employee,
    get_employee_for,
    history_for,
    list_employees,
    update_employee,
)
from ..services.errors import DomainError
from ..services.leave import balances_for
from ..templating import render
from .forms import FormError, parse_date, parse_uuid, text, validation_message

router = APIRouter(dependencies=[Depends(csrf_guard)])

LIMIT = 20


def _departments(db: Session, user: User) -> list[Department]:
    return list(
        db.scalars(
            select(Department)
            .where(Department.org_id == user.org_id, Department.active.is_(True))
            .order_by(Department.name)
        ).all()
    )


def _positions(db: Session, user: User) -> list[Position]:
    return list(
        db.scalars(
            select(Position)
            .where(Position.org_id == user.org_id, Position.active.is_(True))
            .order_by(Position.title)
        ).all()
    )


def _managers(db: Session, user: User) -> list[Employee]:
    return list(
        db.scalars(
            select(Employee)
            .where(Employee.org_id == user.org_id, Employee.status != EmployeeStatus.EXITED)
            .order_by(Employee.first_name, Employee.last_name)
        ).all()
    )


def _form_context(db: Session, user: User) -> dict:
    return {
        "departments": _departments(db, user),
        "positions": _positions(db, user),
        "managers": _managers(db, user),
        "employment_types": list(EmploymentType),
        "statuses": list(EmployeeStatus),
    }


FORM_FIELDS = (
    "first_name",
    "last_name",
    "employee_no",
    "work_email",
    "personal_email",
    "phone",
    "date_of_joining",
    "date_of_birth",
    "gender",
    "employment_type",
    "status",
    "department_id",
    "position_id",
    "manager_id",
    "location",
    "pan_masked",
    "bank_masked",
)


def _raw_form(**values: str) -> dict[str, str]:
    return {field: (values.get(field) or "") for field in FORM_FIELDS}


def _payload(values: dict[str, str]) -> EmployeeCreate:
    return EmployeeCreate(
        employee_no=text(values.get("employee_no")),
        first_name=(values.get("first_name") or "").strip(),
        last_name=(values.get("last_name") or "").strip(),
        work_email=text(values.get("work_email")),
        personal_email=text(values.get("personal_email")),
        phone=text(values.get("phone")),
        date_of_joining=parse_date(values.get("date_of_joining"), "Date of joining"),
        date_of_birth=parse_date(values.get("date_of_birth"), "Date of birth"),
        gender=text(values.get("gender")),
        employment_type=values.get("employment_type") or EmploymentType.FULL_TIME.value,
        status=values.get("status") or EmployeeStatus.ACTIVE.value,
        department_id=parse_uuid(values.get("department_id"), "Department"),
        position_id=parse_uuid(values.get("position_id"), "Position"),
        manager_id=parse_uuid(values.get("manager_id"), "Manager"),
        location=text(values.get("location")),
        pan_masked=text(values.get("pan_masked")),
        bank_masked=text(values.get("bank_masked")),
    )


def _update_payload(values: dict[str, str]) -> EmployeeUpdate:
    return EmployeeUpdate(
        first_name=(values.get("first_name") or "").strip() or None,
        last_name=(values.get("last_name") or "").strip() or None,
        work_email=text(values.get("work_email")),
        personal_email=text(values.get("personal_email")),
        phone=text(values.get("phone")),
        date_of_joining=parse_date(values.get("date_of_joining"), "Date of joining"),
        date_of_birth=parse_date(values.get("date_of_birth"), "Date of birth"),
        gender=text(values.get("gender")),
        employment_type=values.get("employment_type") or None,
        status=values.get("status") or None,
        department_id=parse_uuid(values.get("department_id"), "Department"),
        position_id=parse_uuid(values.get("position_id"), "Position"),
        manager_id=parse_uuid(values.get("manager_id"), "Manager"),
        location=text(values.get("location")),
        pan_masked=text(values.get("pan_masked")),
        bank_masked=text(values.get("bank_masked")),
    )


@router.get("/employees")
def employees_list(
    request: Request,
    search: str = "",
    department: str = "",
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    page = max(1, page)
    employees, total = list_employees(
        db,
        user=user,
        search=search or None,
        department_id=parse_uuid(department, "Department") if department else None,
        status=status or None,
        limit=LIMIT,
        offset=(page - 1) * LIMIT,
    )
    context = {
        "employees": employees,
        "total": total,
        "page": page,
        "pages": max(1, (total + LIMIT - 1) // LIMIT),
        "search": search,
        "department": department,
        "status": status,
        "departments": _departments(db, user),
        "can_create": user.role in {Role.ADMIN, Role.HR},
        "statuses": list(EmployeeStatus),
    }
    if request.headers.get("hx-request") == "true":
        return render(request, "employees/_table.html", **context)
    return render(request, "employees/list.html", **context)


@router.get("/employees/new")
def employee_new(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    return render(
        request,
        "employees/form.html",
        employee=None,
        values={},
        form_error=None,
        **_form_context(db, user),
    )


@router.post("/employees/new")
def employee_create_submit(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    employee_no: str = Form(""),
    work_email: str = Form(""),
    personal_email: str = Form(""),
    phone: str = Form(""),
    date_of_joining: str = Form(""),
    date_of_birth: str = Form(""),
    gender: str = Form(""),
    employment_type: str = Form("full_time"),
    status: str = Form("active"),
    department_id: str = Form(""),
    position_id: str = Form(""),
    manager_id: str = Form(""),
    location: str = Form(""),
    pan_masked: str = Form(""),
    bank_masked: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    values = _raw_form(**locals())
    try:
        employee = create_employee(
            db, org_id=user.org_id, actor=user, data=_payload(values), request=request
        )
    except (PydanticValidationError, FormError, DomainError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return render(
            request,
            "employees/form.html",
            employee=None,
            values=values,
            form_error=message,
            **_form_context(db, user),
        )
    return RedirectResponse(f"/employees/{employee.id}?msg=Employee+created", status_code=303)


@router.get("/employees/{employee_id}")
def employee_detail(
    employee_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        employee = get_employee_for(db, user, employee_id)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    documents = list_documents(db, actor=user, employee_id=employee.id)
    leaves = list(
        db.scalars(
            select(LeaveRequest)
            .where(LeaveRequest.employee_id == employee.id)
            .order_by(LeaveRequest.created_at.desc())
            .limit(20)
        ).all()
    )
    balances = balances_for(
        db, org_id=user.org_id, employee_id=employee.id, year=date.today().year
    )
    return render(
        request,
        "employees/detail.html",
        employee=employee,
        history=history_for(db, employee.id),
        documents=documents,
        leaves=leaves,
        balances=balances,
        can_edit=policy.can_manage_employee(user, employee),
        can_upload=policy.can_upload_document_for(user, employee),
        categories=list(DocumentCategory),
        today=date.today(),
    )


@router.get("/employees/{employee_id}/edit")
def employee_edit(
    employee_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        employee = get_employee_for(db, user, employee_id)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    values = {
        "employee_no": employee.employee_no,
        "first_name": employee.first_name,
        "last_name": employee.last_name,
        "work_email": employee.work_email or "",
        "personal_email": employee.personal_email or "",
        "phone": employee.phone or "",
        "date_of_joining": employee.date_of_joining.isoformat() if employee.date_of_joining else "",
        "date_of_birth": employee.date_of_birth.isoformat() if employee.date_of_birth else "",
        "gender": employee.gender or "",
        "employment_type": employee.employment_type.value,
        "status": employee.status.value,
        "department_id": str(employee.department_id or ""),
        "position_id": str(employee.position_id or ""),
        "manager_id": str(employee.manager_id or ""),
        "location": employee.location or "",
        "pan_masked": employee.pan_masked or "",
        "bank_masked": employee.bank_masked or "",
    }
    return render(
        request,
        "employees/form.html",
        employee=employee,
        values=values,
        form_error=None,
        **_form_context(db, user),
    )


@router.post("/employees/{employee_id}/edit")
def employee_edit_submit(
    employee_id: UUID,
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    employee_no: str = Form(""),
    work_email: str = Form(""),
    personal_email: str = Form(""),
    phone: str = Form(""),
    date_of_joining: str = Form(""),
    date_of_birth: str = Form(""),
    gender: str = Form(""),
    employment_type: str = Form("full_time"),
    status: str = Form("active"),
    department_id: str = Form(""),
    position_id: str = Form(""),
    manager_id: str = Form(""),
    location: str = Form(""),
    pan_masked: str = Form(""),
    bank_masked: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        employee = get_employee_for(db, user, employee_id)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    values = _raw_form(**locals())
    try:
        update_employee(
            db, actor=user, employee=employee, data=_update_payload(values), request=request
        )
    except (PydanticValidationError, FormError, DomainError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return render(
            request,
            "employees/form.html",
            employee=employee,
            values=values,
            form_error=message,
            **_form_context(db, user),
        )
    return RedirectResponse(f"/employees/{employee.id}?msg=Employee+updated", status_code=303)


@router.post("/employees/{employee_id}/documents")
def document_upload(
    employee_id: UUID,
    request: Request,
    title: str = Form(...),
    category: str = Form("other"),
    expires_on: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        employee = get_employee_for(db, user, employee_id)
        category_value = DocumentCategory(category)
        save_document(
            db,
            actor=user,
            employee_id=employee.id,
            title=title,
            category=category_value,
            upload=file,
            expires_on=parse_date(expires_on, "Expiry date"),
            request=request,
        )
    except (DomainError, ValueError, FormError) as exc:
        message = getattr(exc, "message", str(exc))
        return RedirectResponse(
            f"/employees/{employee_id}?error={quote(message)}", status_code=303
        )
    return RedirectResponse(
        f"/employees/{employee_id}?msg=Document+uploaded", status_code=303
    )


@router.get("/documents")
def documents_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    documents = list_documents(db, actor=user)
    return render(
        request,
        "documents.html",
        documents=documents,
        can_delete=user.role in {Role.ADMIN, Role.HR},
        categories=list(DocumentCategory),
    )


@router.get("/documents/{document_id}/download")
def document_download(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        document = get_document(db, actor=user, document_id=document_id)
        path = file_path(document)
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    audit.record(
        db,
        actor=user,
        action=AuditAction.DOWNLOAD,
        entity_type="document",
        entity_id=document.id,
        details={"filename": document.filename},
        request=request,
    )
    return FileResponse(
        path,
        media_type=document.content_type,
        filename=document.filename,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/documents/{document_id}/acknowledge")
def document_acknowledge(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    try:
        acknowledge_document(db, actor=user, document_id=document_id, request=request)
    except DomainError as exc:
        return RedirectResponse(
            f"/documents?error={quote(exc.message)}", status_code=303
        )
    return RedirectResponse("/documents?msg=Document+acknowledged", status_code=303)


@router.post("/documents/{document_id}/delete")
def document_delete(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        delete_document(db, actor=user, document_id=document_id, request=request)
    except DomainError as exc:
        return RedirectResponse(f"/documents?error={quote(exc.message)}", status_code=303)
    return RedirectResponse("/documents?msg=Document+deleted", status_code=303)


__all__ = ["router"]

from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import AuditAction, Role
from ..models import AuditEvent, Department, Employee, Organization, Position, User
from ..schemas import UserCreate
from ..security import generate_password, hash_password, password_problems
from ..services import audit
from ..services.auth import revoke_all_sessions
from ..services.bulk_import import (
    RowError,
    import_employees,
    template_csv,
    template_workbook,
)
from ..services.errors import BulkImportError, ConflictError, ValidationError
from ..services.reports import employees_workbook, full_workbook
from ..services.sample_data import clean_sample, sample_employee_count, seed_sample
from ..templating import render
from .forms import parse_uuid, validation_message

router = APIRouter(dependencies=[Depends(csrf_guard)])


@router.get("/i18n")
def i18n_switch(request: Request, lang: str = ""):
    """Set the locale cookie and redirect back to the referring page."""
    from ..i18n import SUPPORTED

    if lang in SUPPORTED:
        response = RedirectResponse(
            url=request.headers.get("referer", "/"), status_code=303
        )
        response.set_cookie(
            key="pw_lang", value=lang, httponly=False, samesite="lax", max_age=60 * 60 * 24 * 365
        )
        return response
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=303)


AUDIT_LIMIT = 50
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _data_counts(db: Session, org_id) -> dict:
    def count(stmt) -> int:
        return int(db.scalar(stmt) or 0)

    return {
        "employee_count": count(
            select(func.count(Employee.id)).where(Employee.org_id == org_id)
        ),
        "sample_count": sample_employee_count(db, org_id),
        "department_count": count(
            select(func.count(Department.id)).where(Department.org_id == org_id)
        ),
        "position_count": count(
            select(func.count(Position.id)).where(Position.org_id == org_id)
        ),
    }


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _users_context(db: Session, user: User) -> dict:
    users = list(db.scalars(select(User).where(User.org_id == user.org_id).order_by(User.full_name)).all())
    employees = list(
        db.scalars(
            select(Employee)
            .where(Employee.org_id == user.org_id)
            .order_by(Employee.first_name, Employee.last_name)
        ).all()
    )
    return {"users": users, "employees": employees, "roles": list(Role)}


@router.get("/admin/users")
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    return render(
        request,
        "admin/users.html",
        created_password=None,
        created_email=None,
        **_users_context(db, user),
    )


@router.post("/admin/users")
def admin_user_create(
    request: Request,
    email: str = Form(""),
    full_name: str = Form(""),
    role: str = Form("employee"),
    employee_id: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    generated: str | None = None
    try:
        raw_password = password or generate_password()
        problems = password_problems(raw_password)
        if problems:
            raise ValidationError("Password " + "; ".join(problems))
        data = UserCreate(
            email=email,
            full_name=full_name,
            role=role,
            employee_id=parse_uuid(employee_id, "Employee"),
            password=raw_password,
        )
        existing = db.scalar(select(User.id).where(User.email == str(data.email).lower()))
        if existing:
            raise ConflictError("A user with this email already exists")
        if data.employee_id:
            employee = db.get(Employee, data.employee_id)
            if employee is None or employee.org_id != user.org_id:
                raise ValidationError("Employee not found in this organisation")
            linked = db.scalar(
                select(User.id).where(
                    User.org_id == user.org_id,
                    User.employee_id == data.employee_id,
                    User.active.is_(True),
                )
            )
            if linked:
                raise ConflictError("This employee is already linked to a user")
        created = User(
            org_id=user.org_id,
            email=str(data.email).lower(),
            full_name=data.full_name.strip(),
            password_hash=hash_password(raw_password),
            role=data.role,
            employee_id=data.employee_id,
            must_change_password=not password,
        )
        db.add(created)
        db.flush()
        if not password:
            generated = raw_password
        audit.record(
            db,
            actor=user,
            action=AuditAction.CREATE,
            entity_type="user",
            entity_id=created.id,
            details={"email": created.email, "role": created.role.value},
            request=request,
        )
    except (PydanticValidationError, ValidationError, ConflictError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return RedirectResponse(f"/admin/users?error={quote(message)}", status_code=303)

    return render(
        request,
        "admin/users.html",
        created_password=generated,
        created_email=created.email,
        **_users_context(db, user),
    )


@router.post("/admin/users/{user_id}/role")
def admin_user_role(
    user_id: UUID,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    target = db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        return RedirectResponse("/admin/users?error=User+not+found", status_code=303)
    new_role = Role(role)
    if target.role == Role.ADMIN and new_role != Role.ADMIN:
        active_admins = int(
            db.scalar(
                select(func.count(User.id)).where(
                    User.org_id == user.org_id, User.role == Role.ADMIN, User.active.is_(True)
                )
            )
            or 0
        )
        if active_admins <= 1:
            return RedirectResponse(
                "/admin/users?error=The+last+active+administrator+cannot+be+demoted", status_code=303
            )
    target.role = new_role
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=target.id,
        details={"role": new_role.value},
        request=request,
    )
    return RedirectResponse("/admin/users?msg=Role+updated", status_code=303)


@router.post("/admin/users/{user_id}/active")
def admin_user_active(
    user_id: UUID,
    request: Request,
    active: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    target = db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        return RedirectResponse("/admin/users?error=User+not+found", status_code=303)
    if target.id == user.id and active != "1":
        return RedirectResponse("/admin/users?error=You+cannot+deactivate+yourself", status_code=303)
    target.active = active == "1"
    if not target.active:
        revoke_all_sessions(db, target.id)
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=target.id,
        details={"active": target.active},
        request=request,
    )
    return RedirectResponse("/admin/users?msg=User+status+updated", status_code=303)


@router.post("/admin/users/{user_id}/reset-password")
def admin_user_reset_password(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    target = db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        return RedirectResponse("/admin/users?error=User+not+found", status_code=303)
    new_password = generate_password()
    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    revoke_all_sessions(db, target.id)
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=target.id,
        details={"password_reset": True},
        request=request,
    )
    return render(
        request,
        "admin/users.html",
        created_password=new_password,
        created_email=target.email,
        **_users_context(db, user),
    )


@router.get("/admin/audit")
def admin_audit(
    request: Request,
    action: str = "",
    entity: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.AUDITOR)),
):
    page = max(1, page)
    stmt = select(AuditEvent).where(AuditEvent.org_id == user.org_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if entity:
        stmt = stmt.where(AuditEvent.entity_type == entity)
    total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    events = list(
        db.scalars(
            stmt.order_by(AuditEvent.created_at.desc())
            .limit(AUDIT_LIMIT)
            .offset((page - 1) * AUDIT_LIMIT)
        ).all()
    )
    entity_types = list(
        db.scalars(
            select(AuditEvent.entity_type)
            .where(AuditEvent.org_id == user.org_id)
            .distinct()
            .order_by(AuditEvent.entity_type)
        ).all()
    )
    return render(
        request,
        "admin/audit.html",
        events=events,
        total=total,
        page=page,
        pages=max(1, (total + AUDIT_LIMIT - 1) // AUDIT_LIMIT),
        action=action,
        entity=entity,
        actions=list(AuditAction),
        entity_types=entity_types,
    )


@router.get("/admin/settings")
def admin_settings(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    org = db.get(Organization, user.org_id)
    return render(
        request,
        "admin/settings.html",
        organization=org,
        **_data_counts(db, user.org_id),
    )


@router.post("/admin/settings")
def admin_settings_update(
    request: Request,
    name: str = Form(...),
    legal_name: str = Form(""),
    timezone: str = Form("Asia/Kolkata"),
    currency: str = Form("INR"),
    address: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    org = db.get(Organization, user.org_id)
    if org is None:
        return RedirectResponse("/admin/settings?error=Organisation+not+found", status_code=303)
    org.name = name.strip()
    org.legal_name = legal_name.strip() or None
    org.timezone = timezone.strip() or "Asia/Kolkata"
    org.currency = currency.strip().upper()[:3] or "INR"
    org.address = address.strip() or None
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="organization",
        entity_id=org.id,
        details={"name": org.name, "timezone": org.timezone, "currency": org.currency},
        request=request,
    )
    return RedirectResponse("/admin/settings?msg=Settings+saved", status_code=303)


@router.post("/admin/sample-data")
def admin_sample_seed(
    request: Request,
    count: int = Form(100),
    replace: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    if count < 10 or count > 1000:
        return RedirectResponse(
            "/admin/settings?error=Sample+headcount+must+be+between+10+and+1000",
            status_code=303,
        )
    try:
        result = seed_sample(
            db,
            org_id=user.org_id,
            actor=user,
            count=count,
            replace=replace == "1",
            request=request,
        )
    except (ValueError, ConflictError, ValidationError) as exc:
        return RedirectResponse(
            f"/admin/settings?error={quote(str(exc))}", status_code=303
        )
    message = (
        f"Sample data ready: {result.employees} employees, "
        f"{result.departments} departments, {result.positions} positions."
    )
    return RedirectResponse(f"/admin/settings?msg={quote(message)}", status_code=303)


@router.post("/admin/sample-data/clean")
def admin_sample_clean(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN)),
):
    result = clean_sample(db, org_id=user.org_id, actor=user, request=request)
    message = (
        f"Sample data removed: {result.employees} employees, "
        f"{result.departments} departments, {result.positions} positions. "
        "Real records were preserved."
    )
    return RedirectResponse(f"/admin/settings?msg={quote(message)}", status_code=303)


@router.get("/admin/import")
def admin_import(
    request: Request,
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    return render(
        request,
        "admin/import.html",
        result=None,
        errors=[],
        auto_create=True,
        filename=None,
    )


@router.get("/admin/import/template.csv")
def admin_import_template_csv(
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="peawork-employee-import-template.csv"'
        },
    )


@router.get("/admin/import/template.xlsx")
def admin_import_template_xlsx(
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    return _xlsx_response(template_workbook(), "peawork-employee-import-template.xlsx")


@router.post("/admin/import")
async def admin_import_upload(
    request: Request,
    file: UploadFile = File(...),
    auto_create: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    content = await file.read()
    filename = file.filename or ""
    try:
        result = import_employees(
            db,
            org_id=user.org_id,
            actor=user,
            filename=filename,
            content=content,
            auto_create=auto_create == "1",
            request=request,
        )
    except BulkImportError as exc:
        return render(
            request,
            "admin/import.html",
            status_code=422,
            result=None,
            errors=exc.errors,
            auto_create=auto_create == "1",
            filename=filename,
        )
    except ValidationError as exc:
        return render(
            request,
            "admin/import.html",
            status_code=422,
            result=None,
            errors=[RowError(0, str(exc))],
            auto_create=auto_create == "1",
            filename=filename,
        )
    return render(
        request,
        "admin/import.html",
        result=result,
        errors=[],
        auto_create=auto_create == "1",
        filename=filename,
    )


@router.get("/admin/reports")
def admin_reports(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.AUDITOR)),
):
    org = db.get(Organization, user.org_id)
    return render(
        request,
        "admin/reports.html",
        organization=org,
        **_data_counts(db, user.org_id),
    )


@router.get("/admin/reports/employees.xlsx")
def admin_report_employees(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.AUDITOR)),
):
    org = db.get(Organization, user.org_id)
    content = employees_workbook(db, org=org)
    audit.record(
        db,
        actor=user,
        action=AuditAction.DOWNLOAD,
        entity_type="report",
        entity_id="employees",
        details={"format": "xlsx"},
        request=request,
    )
    return _xlsx_response(content, "peawork-employees.xlsx")


@router.get("/admin/reports/full.xlsx")
def admin_report_full(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.AUDITOR)),
):
    org = db.get(Organization, user.org_id)
    content = full_workbook(db, org=org)
    audit.record(
        db,
        actor=user,
        action=AuditAction.DOWNLOAD,
        entity_type="report",
        entity_id="full",
        details={"format": "xlsx"},
        request=request,
    )
    return _xlsx_response(content, "peawork-full-report.xlsx")


__all__ = ["router"]

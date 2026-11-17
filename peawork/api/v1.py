from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import AuditAction, DocumentCategory, Role
from ..models import AuditEvent, Department, LeaveType, Position, User
from ..schemas import (
    AuditEventOut,
    DepartmentCreate,
    DepartmentOut,
    DocumentOut,
    EmployeeCreate,
    EmployeeOut,
    EmployeeUpdate,
    LeaveBalanceOut,
    LeaveDecision,
    LeaveRequestCreate,
    LeaveRequestOut,
    LeaveTypeOut,
    MeOut,
    OrganizationOut,
    Page,
    PasswordChange,
    PositionCreate,
    PositionOut,
    UserOut,
)
from ..services import audit
from ..services.auth import change_password
from ..services.documents import file_path, get_document, list_documents, save_document
from ..services.employees import (
    create_employee,
    get_employee_for,
    list_employees,
    update_employee,
)
from ..services.errors import ConflictError, ValidationError
from ..services.leave import (
    balances_for,
    cancel_leave,
    decide_leave,
    get_leave,
    list_leave_requests,
    request_leave,
)
from ..security import password_problems, verify_password

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(csrf_guard)])


@router.get("/health", tags=["meta"])
def api_health():
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
def me(
    request: Request,
    user: User = Depends(require_user(allow_password_change=True)),
):
    org = getattr(request.state, "org", None)
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        organization=OrganizationOut.model_validate(org) if org else None,
    )


@router.post("/me/password", status_code=204)
def change_my_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(allow_password_change=True)),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise ValidationError("Current password is incorrect")
    problems = password_problems(payload.new_password)
    if problems:
        raise ValidationError("Password " + "; ".join(problems))
    change_password(db, user, payload.new_password)
    return None


@router.get("/employees", response_model=Page[EmployeeOut])
def api_employees(
    search: str = "",
    department_id: UUID | None = None,
    status: str = "",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    employees, total = list_employees(
        db,
        user=user,
        search=search or None,
        department_id=department_id,
        status=status or None,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[EmployeeOut.model_validate(e) for e in employees],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def api_employee_create(
    payload: EmployeeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    employee = create_employee(
        db, org_id=user.org_id, actor=user, data=payload, request=request
    )
    return EmployeeOut.model_validate(employee)


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
def api_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return EmployeeOut.model_validate(get_employee_for(db, user, employee_id))


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
def api_employee_update(
    employee_id: UUID,
    payload: EmployeeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    employee = get_employee_for(db, user, employee_id)
    update_employee(db, actor=user, employee=employee, data=payload, request=request)
    return EmployeeOut.model_validate(employee)


@router.get("/departments", response_model=list[DepartmentOut])
def api_departments(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(Department)
            .where(Department.org_id == user.org_id, Department.active.is_(True))
            .order_by(Department.name)
        ).all()
    )


@router.post("/departments", response_model=DepartmentOut, status_code=201)
def api_department_create(
    payload: DepartmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    if db.scalar(
        select(Department.id).where(
            Department.org_id == user.org_id, Department.code == payload.code
        )
    ):
        raise ConflictError(f"Department code {payload.code} already exists")
    department = Department(
        org_id=user.org_id,
        name=payload.name,
        code=payload.code,
        parent_id=payload.parent_id,
        manager_employee_id=payload.manager_employee_id,
        cost_center=payload.cost_center,
    )
    db.add(department)
    db.flush()
    audit.record(
        db,
        actor=user,
        action=AuditAction.CREATE,
        entity_type="department",
        entity_id=department.id,
        details={"code": department.code},
        request=request,
    )
    return DepartmentOut.model_validate(department)


@router.get("/positions", response_model=list[PositionOut])
def api_positions(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(Position)
            .where(Position.org_id == user.org_id, Position.active.is_(True))
            .order_by(Position.title)
        ).all()
    )


@router.post("/positions", response_model=PositionOut, status_code=201)
def api_position_create(
    payload: PositionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    position = Position(
        org_id=user.org_id,
        title=payload.title,
        code=payload.code,
        department_id=payload.department_id,
        grade=payload.grade,
        level=payload.level,
    )
    db.add(position)
    db.flush()
    audit.record(
        db,
        actor=user,
        action=AuditAction.CREATE,
        entity_type="position",
        entity_id=position.id,
        details={"code": position.code},
        request=request,
    )
    return PositionOut.model_validate(position)


@router.get("/leave-types", response_model=list[LeaveTypeOut])
def api_leave_types(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(LeaveType).where(LeaveType.org_id == user.org_id, LeaveType.active.is_(True))
        ).all()
    )


@router.get("/leave/balances", response_model=list[LeaveBalanceOut])
def api_leave_balances(
    employee_id: UUID | None = None,
    year: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target_id = employee_id or user.employee_id
    if target_id is None:
        raise ValidationError("No employee record is linked to this account")
    if employee_id and employee_id != user.employee_id:
        get_employee_for(db, user, employee_id)
    balances = balances_for(
        db, org_id=user.org_id, employee_id=target_id, year=year or date.today().year
    )
    return [LeaveBalanceOut.model_validate(balance) for balance in balances]


@router.get("/leave-requests", response_model=Page[LeaveRequestOut])
def api_leave_requests(
    status: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    leaves, total = list_leave_requests(
        db, actor=user, status=status or None, limit=limit, offset=offset
    )
    return Page(
        items=[LeaveRequestOut.model_validate(leave) for leave in leaves],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
def api_leave_create(
    payload: LeaveRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    leave = request_leave(db, actor=user, data=payload, request=request)
    return LeaveRequestOut.model_validate(leave)


@router.get("/leave-requests/{leave_id}", response_model=LeaveRequestOut)
def api_leave(
    leave_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return LeaveRequestOut.model_validate(get_leave(db, user, leave_id))


@router.post("/leave-requests/{leave_id}/decision", response_model=LeaveRequestOut)
def api_leave_decision(
    leave_id: UUID,
    payload: LeaveDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.MANAGER)),
):
    leave = decide_leave(db, actor=user, leave_id=leave_id, decision=payload, request=request)
    return LeaveRequestOut.model_validate(leave)


@router.post("/leave-requests/{leave_id}/cancel", response_model=LeaveRequestOut)
def api_leave_cancel(
    leave_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return LeaveRequestOut.model_validate(
        cancel_leave(db, actor=user, leave_id=leave_id, request=request)
    )


@router.get("/documents", response_model=list[DocumentOut])
def api_documents(
    employee_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list_documents(db, actor=user, employee_id=employee_id)


@router.post("/documents", response_model=DocumentOut, status_code=201)
def api_document_upload(
    request: Request,
    employee_id: UUID = Form(...),
    title: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.OTHER),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    document = save_document(
        db,
        actor=user,
        employee_id=employee_id,
        title=title,
        category=category,
        upload=file,
        request=request,
    )
    return DocumentOut.model_validate(document)


@router.get("/documents/{document_id}/download")
def api_document_download(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    document = get_document(db, actor=user, document_id=document_id)
    path = file_path(document)
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
        path, media_type=document.content_type, filename=document.filename
    )


@router.get("/audit", response_model=Page[AuditEventOut])
def api_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.AUDITOR)),
):
    from sqlalchemy import func

    stmt = select(AuditEvent).where(AuditEvent.org_id == user.org_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = list(
        db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    )
    return Page(
        items=[AuditEventOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = ["router"]

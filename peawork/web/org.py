from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import AuditAction, Role
from ..models import Department, Employee, Position, User
from ..schemas import DepartmentCreate, PositionCreate
from ..services import audit
from ..services.errors import ConflictError
from ..templating import render
from .forms import FormError, parse_uuid, text, validation_message

router = APIRouter(dependencies=[Depends(csrf_guard)])


def _org_context(db: Session, user: User) -> dict:
    departments = list(
        db.scalars(
            select(Department)
            .where(Department.org_id == user.org_id)
            .order_by(Department.name)
        ).all()
    )
    positions = list(
        db.scalars(
            select(Position)
            .where(Position.org_id == user.org_id)
            .order_by(Position.title)
        ).all()
    )
    employees = list(
        db.scalars(
            select(Employee)
            .where(Employee.org_id == user.org_id)
            .order_by(Employee.first_name, Employee.last_name)
        ).all()
    )
    headcount: dict[UUID, int] = {}
    for employee in employees:
        if employee.status.value in {"active", "probation", "on_leave", "notice"} and employee.department_id:
            headcount[employee.department_id] = headcount.get(employee.department_id, 0) + 1
    return {
        "departments": departments,
        "positions": positions,
        "employees": employees,
        "headcount": headcount,
    }


@router.get("/organization")
def organization(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return render(
        request,
        "organization.html",
        can_manage=user.role in {Role.ADMIN, Role.SUPER_ADMIN, Role.HR},
        **_org_context(db, user),
    )


@router.post("/organization/departments")
def department_create(
    request: Request,
    name: str = Form(""),
    code: str = Form(""),
    parent_id: str = Form(""),
    manager_employee_id: str = Form(""),
    cost_center: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        data = DepartmentCreate(
            name=name,
            code=code,
            parent_id=parse_uuid(parent_id, "Parent department"),
            manager_employee_id=parse_uuid(manager_employee_id, "Manager"),
            cost_center=text(cost_center),
        )
        existing = db.scalar(
            select(Department.id).where(
                Department.org_id == user.org_id, Department.code == data.code
            )
        )
        if existing:
            raise ConflictError(f"Department code {data.code} already exists")
        department = Department(
            org_id=user.org_id,
            name=data.name,
            code=data.code,
            parent_id=data.parent_id,
            manager_employee_id=data.manager_employee_id,
            cost_center=data.cost_center,
        )
        db.add(department)
        db.flush()
        audit.record(
            db,
            actor=user,
            action=AuditAction.CREATE,
            entity_type="department",
            entity_id=department.id,
            details={"code": department.code, "name": department.name},
            request=request,
        )
    except (PydanticValidationError, FormError, ConflictError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return RedirectResponse(f"/organization?error={quote(message)}", status_code=303)
    return RedirectResponse("/organization?msg=Department+created", status_code=303)


@router.post("/organization/positions")
def position_create(
    request: Request,
    title: str = Form(""),
    code: str = Form(""),
    department_id: str = Form(""),
    grade: str = Form(""),
    level: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        data = PositionCreate(
            title=title,
            code=code,
            department_id=parse_uuid(department_id, "Department"),
            grade=text(grade),
            level=text(level),
        )
        existing = db.scalar(
            select(Position.id).where(Position.org_id == user.org_id, Position.code == data.code)
        )
        if existing:
            raise ConflictError(f"Position code {data.code} already exists")
        position = Position(
            org_id=user.org_id,
            title=data.title,
            code=data.code,
            department_id=data.department_id,
            grade=data.grade,
            level=data.level,
        )
        db.add(position)
        db.flush()
        audit.record(
            db,
            actor=user,
            action=AuditAction.CREATE,
            entity_type="position",
            entity_id=position.id,
            details={"code": position.code, "title": position.title},
            request=request,
        )
    except (PydanticValidationError, FormError, ConflictError) as exc:
        message = validation_message(exc) if isinstance(exc, PydanticValidationError) else str(exc)
        return RedirectResponse(f"/organization?error={quote(message)}", status_code=303)
    return RedirectResponse("/organization?msg=Position+created", status_code=303)


@router.post("/organization/departments/{department_id}/toggle")
def department_toggle(
    department_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    department = db.get(Department, department_id)
    if department and department.org_id == user.org_id:
        department.active = not department.active
        audit.record(
            db,
            actor=user,
            action=AuditAction.UPDATE,
            entity_type="department",
            entity_id=department.id,
            details={"active": department.active},
            request=request,
        )
    return RedirectResponse("/organization?msg=Department+updated", status_code=303)


@router.post("/organization/positions/{position_id}/toggle")
def position_toggle(
    position_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    position = db.get(Position, position_id)
    if position and position.org_id == user.org_id:
        position.active = not position.active
        audit.record(
            db,
            actor=user,
            action=AuditAction.UPDATE,
            entity_type="position",
            entity_id=position.id,
            details={"active": position.active},
            request=request,
        )
    return RedirectResponse("/organization?msg=Position+updated", status_code=303)


__all__ = ["router"]

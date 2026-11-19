from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Department, Employee, EmployeeHistory, Position, User
from ..schemas import EmployeeCreate, EmployeeUpdate
from ..security import utcnow
from ..domain import AuditAction
from . import audit, policy
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError


def get_employee(db: Session, org_id: UUID, employee_id: UUID) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None or employee.org_id != org_id:
        raise NotFoundError("Employee not found")
    return employee


def get_employee_for(db: Session, user: User, employee_id: UUID) -> Employee:
    employee = get_employee(db, user.org_id, employee_id)
    if not policy.can_view_employee(user, employee):
        raise ForbiddenError("You do not have access to this employee")
    return employee


def next_employee_no(db: Session, org_id: UUID) -> str:
    numbers = db.scalars(
        select(Employee.employee_no).where(Employee.org_id == org_id)
    ).all()
    highest = 0
    for value in numbers:
        tail = value.rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"EMP-{highest + 1:04d}"


def _validate_refs(
    db: Session,
    org_id: UUID,
    *,
    department_id: UUID | None,
    position_id: UUID | None,
    manager_id: UUID | None,
    employee_id: UUID | None = None,
) -> None:
    if department_id:
        dept = db.get(Department, department_id)
        if dept is None or dept.org_id != org_id:
            raise ValidationError("Department not found in this organisation")
    if position_id:
        position = db.get(Position, position_id)
        if position is None or position.org_id != org_id:
            raise ValidationError("Position not found in this organisation")
    if manager_id:
        if employee_id and manager_id == employee_id:
            raise ValidationError("An employee cannot be their own manager")
        manager = db.get(Employee, manager_id)
        if manager is None or manager.org_id != org_id:
            raise ValidationError("Manager not found in this organisation")


def _assert_unique(
    db: Session,
    org_id: UUID,
    *,
    employee_no: str | None,
    work_email: str | None,
    exclude_id: UUID | None = None,
) -> None:
    if employee_no:
        stmt = select(Employee.id).where(
            Employee.org_id == org_id, Employee.employee_no == employee_no
        )
        if exclude_id:
            stmt = stmt.where(Employee.id != exclude_id)
        if db.scalar(stmt):
            raise ConflictError(f"Employee number {employee_no} already exists")
    if work_email:
        stmt = select(Employee.id).where(
            Employee.org_id == org_id, func.lower(Employee.work_email) == work_email.lower()
        )
        if exclude_id:
            stmt = stmt.where(Employee.id != exclude_id)
        if db.scalar(stmt):
            raise ConflictError(f"Work email {work_email} already exists")


def create_employee(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    data: EmployeeCreate,
    request=None,
) -> Employee:
    if actor.role.value not in {"admin", "hr"}:
        raise ForbiddenError("Only HR or administrators may create employees")

    employee_no = (data.employee_no or "").strip() or next_employee_no(db, org_id)
    work_email = str(data.work_email).lower() if data.work_email else None
    _assert_unique(db, org_id, employee_no=employee_no, work_email=work_email)
    _validate_refs(
        db,
        org_id,
        department_id=data.department_id,
        position_id=data.position_id,
        manager_id=data.manager_id,
    )

    employee = Employee(
        org_id=org_id,
        employee_no=employee_no,
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        work_email=work_email,
        personal_email=str(data.personal_email).lower() if data.personal_email else None,
        phone=data.phone,
        date_of_joining=data.date_of_joining,
        date_of_birth=data.date_of_birth,
        gender=data.gender,
        employment_type=data.employment_type,
        status=data.status,
        department_id=data.department_id,
        position_id=data.position_id,
        manager_id=data.manager_id,
        location=data.location,
        country_code=data.country_code,
        pan_masked=data.pan_masked,
        bank_masked=data.bank_masked,
    )
    db.add(employee)
    db.flush()

    db.add(
        EmployeeHistory(
            employee_id=employee.id,
            effective_date=data.date_of_joining or utcnow().date(),
            change_type="hire",
            new_value="Employee created",
            actor_id=actor.id,
        )
    )
    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="employee",
        entity_id=employee.id,
        details={
            "employee_no": employee.employee_no,
            "department_id": str(employee.department_id) if employee.department_id else None,
            "manager_id": str(employee.manager_id) if employee.manager_id else None,
            "status": employee.status.value,
        },
        request=request,
        org_id=org_id,
    )
    return employee


def update_employee(
    db: Session,
    *,
    actor: User,
    employee: Employee,
    data: EmployeeUpdate,
    request=None,
) -> Employee:
    if not policy.can_manage_employee(actor, employee):
        raise ForbiddenError("You may not edit this employee")

    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return employee

    if "manager_id" in changes:
        _validate_refs(
            db,
            employee.org_id,
            department_id=changes.get("department_id", employee.department_id),
            position_id=changes.get("position_id", employee.position_id),
            manager_id=changes["manager_id"],
            employee_id=employee.id,
        )
        # Employees may not create a reporting cycle.
        if changes["manager_id"]:
            proposed = db.get(Employee, changes["manager_id"])
            cursor = proposed
            seen: set[UUID] = set()
            while cursor and cursor.manager_id and cursor.id not in seen:
                seen.add(cursor.id)
                if cursor.manager_id == employee.id:
                    raise ValidationError("This manager assignment creates a reporting cycle")
                cursor = db.get(Employee, cursor.manager_id)
    else:
        _validate_refs(
            db,
            employee.org_id,
            department_id=changes.get("department_id"),
            position_id=changes.get("position_id"),
            manager_id=None,
            employee_id=employee.id,
        )

    _assert_unique(
        db,
        employee.org_id,
        employee_no=None,
        work_email=str(changes["work_email"]).lower() if changes.get("work_email") else None,
        exclude_id=employee.id,
    )

    changed_fields: list[str] = []
    for field, value in changes.items():
        if field.endswith("email") and value:
            value = str(value).lower()
        current = getattr(employee, field)
        if current != value:
            setattr(employee, field, value)
            changed_fields.append(field)

    if changed_fields:
        db.add(
            EmployeeHistory(
                employee_id=employee.id,
                effective_date=utcnow().date(),
                change_type="update",
                new_value=", ".join(changed_fields),
                actor_id=actor.id,
            )
        )
        audit.record(
            db,
            actor=actor,
            action=AuditAction.UPDATE,
            entity_type="employee",
            entity_id=employee.id,
            details={"changed": changed_fields},
            request=request,
            org_id=employee.org_id,
        )
        db.flush()
    return employee


def list_employees(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    department_id: UUID | None = None,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Employee], int]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    scope = policy.employee_scope(user, user.org_id)
    conditions = []
    if search:
        # Tokenise so that a full-name query such as "Form Tester" matches
        # separate first/last name columns while still requiring every term.
        terms = [term for term in search.strip().lower().split() if term][:5]
        for term in terms:
            needle = f"%{term}%"
            conditions.append(
                or_(
                    func.lower(Employee.first_name).like(needle),
                    func.lower(Employee.last_name).like(needle),
                    func.lower(Employee.employee_no).like(needle),
                    func.lower(func.coalesce(Employee.work_email, "")).like(needle),
                )
            )
    if department_id:
        conditions.append(Employee.department_id == department_id)
    if status:
        conditions.append(Employee.status == status)

    if conditions:
        scope = scope.where(*conditions)

    total = int(
        db.scalar(select(func.count()).select_from(scope.order_by(None).subquery())) or 0
    )
    employees = list(
        db.scalars(scope.order_by(Employee.employee_no).limit(limit).offset(offset)).all()
    )
    return employees, total


def history_for(db: Session, employee_id: UUID) -> list[EmployeeHistory]:
    return list(
        db.scalars(
            select(EmployeeHistory)
            .where(EmployeeHistory.employee_id == employee_id)
            .order_by(EmployeeHistory.effective_date.desc(), EmployeeHistory.created_at.desc())
        ).all()
    )

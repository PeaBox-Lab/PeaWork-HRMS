from __future__ import annotations

from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.sql import Select

from ..domain import Role
from ..models import Document, Employee, LeaveRequest, User

# Roles that may read organisation-wide people data.
ORG_READ_ROLES = {Role.ADMIN, Role.HR, Role.AUDITOR}
ORG_WRITE_ROLES = {Role.ADMIN, Role.HR}
PAYROLL_ROLES = {Role.ADMIN, Role.PAYROLL}
APPROVER_ROLES = {Role.ADMIN, Role.HR, Role.MANAGER}


def is_admin(user: User) -> bool:
    return user.role == Role.ADMIN


def is_privileged_reader(user: User) -> bool:
    return user.role in ORG_READ_ROLES or user.role == Role.PAYROLL


def is_manager_of(user: User, employee: Employee | None) -> bool:
    return bool(user.employee_id and employee and employee.manager_id == user.employee_id)


def is_self(user: User, employee: Employee | None) -> bool:
    return bool(user.employee_id and employee and employee.id == user.employee_id)


def employee_scope(user: User, org_id: UUID) -> Select[tuple[Employee]]:
    """Deny-by-default employee query scope for list/search endpoints."""
    stmt = select(Employee).where(Employee.org_id == org_id)
    if is_privileged_reader(user):
        return stmt
    if user.role == Role.MANAGER and user.employee_id:
        return stmt.where(
            or_(Employee.id == user.employee_id, Employee.manager_id == user.employee_id)
        )
    if user.employee_id:
        return stmt.where(Employee.id == user.employee_id)
    return stmt.where(false())


def can_view_employee(user: User, employee: Employee) -> bool:
    if is_privileged_reader(user):
        return True
    return is_self(user, employee) or is_manager_of(user, employee)


def can_manage_employee(user: User, employee: Employee | None = None) -> bool:
    if user.role in ORG_WRITE_ROLES:
        return True
    # Employees may edit their own contact profile through a restricted path.
    return bool(employee and is_self(user, employee))


def can_view_leave(user: User, leave: LeaveRequest) -> bool:
    if is_privileged_reader(user):
        return True
    return is_self(user, leave.employee) or is_manager_of(user, leave.employee)


def can_approve_leave(user: User, leave: LeaveRequest) -> bool:
    if user.role in {Role.ADMIN, Role.HR}:
        return True
    if user.role == Role.MANAGER:
        return is_manager_of(user, leave.employee) and not is_self(user, leave.employee)
    return False


def can_request_leave_for(user: User, employee_id: UUID) -> bool:
    if user.role in ORG_WRITE_ROLES:
        return True
    return user.employee_id == employee_id


def can_view_document(user: User, document: Document) -> bool:
    if is_privileged_reader(user):
        return True
    employee = document.employee
    return bool(employee and (is_self(user, employee) or is_manager_of(user, employee)))


def can_upload_document_for(user: User, employee: Employee) -> bool:
    if user.role in ORG_WRITE_ROLES:
        return True
    return is_self(user, employee)


def can_view_payroll(user: User) -> bool:
    return user.role in PAYROLL_ROLES

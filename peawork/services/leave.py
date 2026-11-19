from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..domain import AuditAction, LeaveStatus, NotificationKind
from ..models import Employee, Holiday, LeaveBalance, LeaveRequest, LeaveType, Notification, User
from ..schemas import LeaveDecision, LeaveRequestCreate
from ..security import utcnow
from . import audit, policy
from .errors import ConflictError, ForbiddenError, NotFoundError, ValidationError

HALF_DAY = Decimal("0.5")
MAX_RANGE_DAYS = 90


def _holiday_dates(db: Session, org_id: UUID, start: date, end: date) -> set[date]:
    return set(
        db.scalars(
            select(Holiday.holiday_date).where(
                Holiday.org_id == org_id,
                Holiday.holiday_date >= start,
                Holiday.holiday_date <= end,
                Holiday.optional.is_(False),
            )
        ).all()
    )


def working_days(db: Session, org_id: UUID, start: date, end: date) -> Decimal:
    if end < start:
        raise ValidationError("End date must be on or after the start date")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValidationError(f"Leave requests may not exceed {MAX_RANGE_DAYS} days")
    holidays = _holiday_dates(db, org_id, start, end)
    days = Decimal("0")
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            days += Decimal("1")
        cursor += timedelta(days=1)
    return days


def get_leave_type(db: Session, org_id: UUID, leave_type_id: UUID) -> LeaveType:
    leave_type = db.get(LeaveType, leave_type_id)
    if leave_type is None or leave_type.org_id != org_id or not leave_type.active:
        raise ValidationError("Leave type is not available")
    return leave_type


def get_balance(
    db: Session,
    *,
    org_id: UUID,
    employee_id: UUID,
    leave_type: LeaveType,
    year: int,
) -> LeaveBalance:
    balance = db.scalar(
        select(LeaveBalance).where(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type_id == leave_type.id,
            LeaveBalance.year == year,
        )
    )
    if balance:
        return balance
    balance = LeaveBalance(
        org_id=org_id,
        employee_id=employee_id,
        leave_type_id=leave_type.id,
        year=year,
        opening=leave_type.annual_entitlement,
        accrued=Decimal("0"),
        used=Decimal("0"),
    )
    db.add(balance)
    db.flush()
    return balance


def balances_for(
    db: Session, *, org_id: UUID, employee_id: UUID, year: int
) -> list[LeaveBalance]:
    types = list(
        db.scalars(
            select(LeaveType).where(LeaveType.org_id == org_id, LeaveType.active.is_(True))
        ).all()
    )
    return [
        get_balance(db, org_id=org_id, employee_id=employee_id, leave_type=leave_type, year=year)
        for leave_type in types
    ]


def _overlapping(
    db: Session,
    *,
    employee_id: UUID,
    start: date,
    end: date,
    statuses: tuple[LeaveStatus, ...],
    exclude_id: UUID | None = None,
) -> LeaveRequest | None:
    stmt = select(LeaveRequest).where(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
        LeaveRequest.status.in_(statuses),
    )
    if exclude_id:
        stmt = stmt.where(LeaveRequest.id != exclude_id)
    return db.scalar(stmt)


def _notify_user(db: Session, *, org_id: UUID, user: User | None, title: str, body: str, link: str, kind: NotificationKind) -> None:
    if user is None:
        return
    db.add(
        Notification(org_id=org_id, user_id=user.id, kind=kind, title=title, body=body, link=link)
    )


def _user_for_employee(db: Session, org_id: UUID, employee_id: UUID | None) -> User | None:
    if not employee_id:
        return None
    return db.scalar(
        select(User).where(
            User.org_id == org_id, User.employee_id == employee_id, User.active.is_(True)
        )
    )


def request_leave(
    db: Session,
    *,
    actor: User,
    data: LeaveRequestCreate,
    request=None,
) -> LeaveRequest:
    employee_id = data.employee_id or actor.employee_id
    if employee_id is None:
        raise ValidationError("Your account is not linked to an employee record")
    if not policy.can_request_leave_for(actor, employee_id):
        raise ForbiddenError("You may only request leave for yourself")

    employee = db.get(Employee, employee_id)
    if employee is None or employee.org_id != actor.org_id:
        raise NotFoundError("Employee not found")

    leave_type = get_leave_type(db, actor.org_id, data.leave_type_id)
    if data.half_day:
        if data.start_date != data.end_date:
            raise ValidationError("Half-day leave must start and end on the same date")
        days = HALF_DAY
    else:
        days = working_days(db, actor.org_id, data.start_date, data.end_date)

    if days <= 0:
        raise ValidationError("The selected range contains no working days")
    if data.start_date.year != data.end_date.year:
        raise ValidationError("Leave requests cannot span calendar years")

    if _overlapping(
        db,
        employee_id=employee_id,
        start=data.start_date,
        end=data.end_date,
        statuses=(LeaveStatus.PENDING, LeaveStatus.APPROVED),
    ):
        raise ConflictError("An overlapping leave request already exists")

    if leave_type.annual_entitlement > 0:
        balance = get_balance(
            db,
            org_id=actor.org_id,
            employee_id=employee_id,
            leave_type=leave_type,
            year=data.start_date.year,
        )
        if balance.available < days:
            raise ConflictError(
                f"Insufficient {leave_type.name} balance: {balance.available} day(s) available"
            )

    leave = LeaveRequest(
        org_id=actor.org_id,
        employee_id=employee_id,
        leave_type_id=leave_type.id,
        start_date=data.start_date,
        end_date=data.end_date,
        days=days,
        reason=data.reason,
        status=LeaveStatus.PENDING,
    )
    db.add(leave)
    db.flush()

    manager_user = _user_for_employee(db, actor.org_id, employee.manager_id)
    if manager_user and manager_user.id != actor.id:
        _notify_user(
            db,
            org_id=actor.org_id,
            user=manager_user,
            title="Leave request awaiting approval",
            body=f"{employee.full_name} requested {days} day(s) of {leave_type.name}",
            link="/leave",
            kind=NotificationKind.APPROVAL,
        )

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="leave_request",
        entity_id=leave.id,
        details={
            "employee_id": str(employee_id),
            "leave_type": leave_type.code,
            "days": str(days),
            "start_date": data.start_date.isoformat(),
            "end_date": data.end_date.isoformat(),
        },
        request=request,
        org_id=actor.org_id,
    )
    return leave


def get_leave(db: Session, actor: User, leave_id: UUID) -> LeaveRequest:
    leave = db.get(LeaveRequest, leave_id)
    if leave is None or leave.org_id != actor.org_id:
        raise NotFoundError("Leave request not found")
    if not policy.can_view_leave(actor, leave):
        raise ForbiddenError("You do not have access to this leave request")
    return leave


def decide_leave(
    db: Session,
    *,
    actor: User,
    leave_id: UUID,
    decision: LeaveDecision,
    request=None,
) -> LeaveRequest:
    leave = get_leave(db, actor, leave_id)
    if leave.employee_id == actor.employee_id and actor.employee_id is not None:
        raise ForbiddenError("You may not approve your own leave request")
    if not policy.can_approve_leave(actor, leave):
        raise ForbiddenError("You may not decide this leave request")
    if leave.status != LeaveStatus.PENDING:
        raise ConflictError("This leave request has already been decided")

    leave_type = get_leave_type(db, actor.org_id, leave.leave_type_id)

    if decision.decision == "approve":
        if _overlapping(
            db,
            employee_id=leave.employee_id,
            start=leave.start_date,
            end=leave.end_date,
            statuses=(LeaveStatus.APPROVED,),
            exclude_id=leave.id,
        ):
            raise ConflictError("Another approved leave request overlaps this range")
        if leave_type.annual_entitlement > 0:
            balance = get_balance(
                db,
                org_id=actor.org_id,
                employee_id=leave.employee_id,
                leave_type=leave_type,
                year=leave.start_date.year,
            )
            if balance.available < leave.days:
                raise ConflictError("Insufficient leave balance at approval time")
            balance.used = (balance.used or Decimal("0")) + leave.days
        leave.status = LeaveStatus.APPROVED
        action = AuditAction.APPROVE
    else:
        leave.status = LeaveStatus.REJECTED
        action = AuditAction.REJECT

    leave.approver_id = actor.id
    leave.approver_comment = decision.comment
    leave.decided_at = utcnow()

    employee_user = _user_for_employee(db, actor.org_id, leave.employee_id)
    _notify_user(
        db,
        org_id=actor.org_id,
        user=employee_user,
        title=f"Leave request {leave.status.value}",
        body=f"Your {leave.days} day {leave_type.name} request was {leave.status.value}",
        link="/leave",
        kind=NotificationKind.INFO,
    )
    audit.record(
        db,
        actor=actor,
        action=action,
        entity_type="leave_request",
        entity_id=leave.id,
        details={"days": str(leave.days), "comment": decision.comment},
        request=request,
        org_id=actor.org_id,
    )
    db.flush()
    return leave


def cancel_leave(db: Session, *, actor: User, leave_id: UUID, request=None) -> LeaveRequest:
    leave = get_leave(db, actor, leave_id)
    if leave.employee_id != actor.employee_id and actor.role.value not in {"admin", "super_admin", "hr"}:
        raise ForbiddenError("You may only cancel your own leave")
    if leave.status in {LeaveStatus.CANCELLED, LeaveStatus.REJECTED}:
        raise ConflictError("This leave request can no longer be cancelled")
    if leave.status == LeaveStatus.APPROVED:
        leave_type = get_leave_type(db, actor.org_id, leave.leave_type_id)
        if leave_type.annual_entitlement > 0:
            balance = get_balance(
                db,
                org_id=actor.org_id,
                employee_id=leave.employee_id,
                leave_type=leave_type,
                year=leave.start_date.year,
            )
            balance.used = max(Decimal("0"), (balance.used or Decimal("0")) - leave.days)
    leave.status = LeaveStatus.CANCELLED
    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="leave_request",
        entity_id=leave.id,
        details={"status": "cancelled"},
        request=request,
        org_id=actor.org_id,
    )
    db.flush()
    return leave


def list_leave_requests(
    db: Session,
    *,
    actor: User,
    status: str | None = None,
    pending_for_me: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[LeaveRequest], int]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    stmt = select(LeaveRequest).where(LeaveRequest.org_id == actor.org_id)

    if not policy.is_privileged_reader(actor):
        if actor.role.value == "manager" and actor.employee_id:
            team_ids = select(Employee.id).where(
                Employee.org_id == actor.org_id,
                or_(Employee.id == actor.employee_id, Employee.manager_id == actor.employee_id),
            )
            stmt = stmt.where(LeaveRequest.employee_id.in_(team_ids))
        elif actor.employee_id:
            stmt = stmt.where(LeaveRequest.employee_id == actor.employee_id)
        else:
            return [], 0

    if status:
        stmt = stmt.where(LeaveRequest.status == status)
    if pending_for_me and actor.role.value == "manager" and actor.employee_id:
        report_ids = select(Employee.id).where(
            Employee.org_id == actor.org_id, Employee.manager_id == actor.employee_id
        )
        stmt = stmt.where(
            LeaveRequest.employee_id.in_(report_ids), LeaveRequest.status == LeaveStatus.PENDING
        )

    total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(LeaveRequest.created_at.desc()).limit(limit).offset(offset)
        ).all()
    )
    return rows, total


def pending_approvals(db: Session, actor: User, limit: int = 50) -> list[LeaveRequest]:
    if actor.role.value not in {"admin", "super_admin", "hr", "manager"}:
        return []
    stmt = select(LeaveRequest).where(
        LeaveRequest.org_id == actor.org_id, LeaveRequest.status == LeaveStatus.PENDING
    )
    if actor.role.value == "manager" and actor.employee_id:
        report_ids = select(Employee.id).where(
            Employee.org_id == actor.org_id, Employee.manager_id == actor.employee_id
        )
        stmt = stmt.where(LeaveRequest.employee_id.in_(report_ids))
    return list(db.scalars(stmt.order_by(LeaveRequest.created_at.asc()).limit(limit)).all())


__all__ = [
    "balances_for",
    "cancel_leave",
    "decide_leave",
    "get_leave",
    "get_leave_type",
    "list_leave_requests",
    "pending_approvals",
    "request_leave",
    "working_days",
]

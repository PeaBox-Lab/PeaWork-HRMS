from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..models import Department, Document, Holiday, Notification, User
from ..services import policy
from ..services.leave import balances_for, list_leave_requests, pending_approvals
from ..templating import render

router = APIRouter(dependencies=[Depends(csrf_guard)])


@router.get("/")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    today = date.today()
    scope = policy.employee_scope(user, user.org_id)
    employee_count = int(
        db.scalar(select(func.count()).select_from(scope.order_by(None).subquery())) or 0
    )

    pending = pending_approvals(db, user, limit=5) if user.role.value in {"admin", "super_admin", "hr", "manager"} else []
    leaves, leave_count = list_leave_requests(db, actor=user, limit=6)
    _, pending_count = list_leave_requests(db, actor=user, status="pending", limit=1)

    document_count = int(
        db.scalar(
            select(func.count(Document.id)).where(Document.org_id == user.org_id)
        )
        or 0
    )
    if not policy.is_privileged_reader(user) and user.employee_id:
        document_count = int(
            db.scalar(
                select(func.count(Document.id)).where(
                    Document.org_id == user.org_id, Document.employee_id == user.employee_id
                )
            )
            or 0
        )

    department_count = int(
        db.scalar(
            select(func.count(Department.id)).where(
                Department.org_id == user.org_id, Department.active.is_(True)
            )
        )
        or 0
    )
    upcoming_holidays = list(
        db.scalars(
            select(Holiday)
            .where(Holiday.org_id == user.org_id, Holiday.holiday_date >= today)
            .order_by(Holiday.holiday_date)
            .limit(4)
        ).all()
    )
    unread_notifications = int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
        or 0
    )
    notifications = list(
        db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(5)
        ).all()
    )
    balances = (
        balances_for(db, org_id=user.org_id, employee_id=user.employee_id, year=today.year)
        if user.employee_id
        else []
    )

    return render(
        request,
        "dashboard.html",
        employee_count=employee_count,
        pending=pending,
        pending_count=pending_count,
        document_count=document_count,
        department_count=department_count,
        recent_leaves=leaves,
        leave_count=leave_count,
        upcoming_holidays=upcoming_holidays,
        unread_notifications=unread_notifications,
        notifications=notifications,
        balances=balances,
        today=today,
        next_week=today + timedelta(days=7),
    )


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    notification = db.get(Notification, notification_id)
    if notification and notification.user_id == user.id:
        from ..security import utcnow

        notification.read_at = utcnow()
    return RedirectResponse("/", status_code=303)


__all__ = ["router"]

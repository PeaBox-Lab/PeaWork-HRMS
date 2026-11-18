"""Onboarding and offboarding screens backed by lifecycle journeys."""
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
from ..domain import (
    EmployeeStatus,
    ExitType,
    JourneyType,
    Role,
    SeparationStatus,
)
from ..models import Employee
from ..services import journeys as journey_service
from ..services.errors import DomainError
from ..templating import render
from .forms import FormError, text

router = APIRouter(dependencies=[Depends(csrf_guard)])

HR_ROLES = {Role.ADMIN, Role.HR}

EXIT_TYPE_LABELS = {item.value: item.label for item in ExitType}
NEXT_STATUS = {
    SeparationStatus.NOTICE.value: SeparationStatus.CLEARANCE,
    SeparationStatus.CLEARANCE.value: SeparationStatus.FINAL_PAY,
    SeparationStatus.FINAL_PAY.value: SeparationStatus.EXITED,
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


def _common_context(db: Session, user, journey_type: JourneyType) -> dict:
    rows = journey_service.list_journeys(
        db, org_id=user.org_id, user=user, journey_type=journey_type.value
    )
    return {
        "rows": rows,
        "employees": _scoped_employees(db, user),
        "journey_type": journey_type.value,
        "journey_type_label": journey_type.label,
        "is_hr": user.role in HR_ROLES,
        "today": date.today().isoformat(),
        "open_count": sum(1 for row in rows if row["status"] == "in_progress"),
        "overdue_count": sum(len(row["overdue_tasks"]) for row in rows),
        "done_count": sum(1 for row in rows if row["status"] == "completed"),
    }


@router.get("/onboarding")
def onboarding_index(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    return render(
        request,
        "journeys/index.html",
        type_label="Onboarding",
        **_common_context(db, user, JourneyType.ONBOARDING),
    )


@router.post("/onboarding/start")
def onboarding_start(
    employee_id: str = Form(""),
    anchor_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        if not employee_id:
            raise FormError("Choose the employee")
        if not anchor_date:
            raise FormError("Choose the start date")
        journey_service.start_journey(
            db,
            org_id=user.org_id,
            actor=user,
            employee_id=employee_id,
            journey_type=JourneyType.ONBOARDING,
            anchor_date=date.fromisoformat(anchor_date),
            notes=text(notes),
        )
    except (FormError, DomainError, ValueError) as exc:
        return _error_redirect("/onboarding", exc)
    return RedirectResponse("/onboarding?msg=Onboarding+started", status_code=303)


@router.get("/onboarding/{journey_id}")
def onboarding_detail(
    journey_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    return _journey_detail(request, db, user, journey_id, JourneyType.ONBOARDING)


@router.post("/onboarding/{journey_id}/tasks/{task_id}/complete")
def onboarding_task_complete(
    journey_id: UUID,
    task_id: UUID,
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    return _complete_task(
        db, user, journey_id, task_id, notes, f"/onboarding/{journey_id}"
    )


@router.post("/onboarding/{journey_id}/tasks/{task_id}/skip")
def onboarding_task_skip(
    journey_id: UUID,
    task_id: UUID,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    return _skip_task(
        db, user, journey_id, task_id, reason, f"/onboarding/{journey_id}"
    )


@router.post("/onboarding/{journey_id}/complete")
def onboarding_complete(
    journey_id: UUID,
    force: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    return _complete_journey(
        db, user, journey_id, force, f"/onboarding/{journey_id}"
    )


# --- Offboarding -----------------------------------------------------------


def _offboarding_context(db: Session, user) -> dict:
    separations = journey_service.list_separations(db, org_id=user.org_id, user=user)
    return {
        "separations": separations,
        "employees": _scoped_employees(db, user),
        "exit_types": [(item.value, item.label) for item in ExitType],
        "separation_status_labels": journey_service.SEPARATION_STATUS_LABELS,
        "next_status": NEXT_STATUS,
        "is_hr": user.role in HR_ROLES,
        "today": date.today().isoformat(),
        "open_count": sum(
            1 for row in separations if row["status"] not in {"exited", "withdrawn"}
        ),
        "exit_count": sum(1 for row in separations if row["status"] == "exited"),
    }


@router.get("/offboarding")
def offboarding_index(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    return render(
        request,
        "journeys/offboarding.html",
        **_offboarding_context(db, user),
    )


@router.post("/offboarding/start")
def offboarding_start(
    employee_id: str = Form(""),
    exit_type: str = Form(""),
    last_working_day: str = Form(""),
    notice_date: str = Form(""),
    rehire_eligible: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        if not employee_id:
            raise FormError("Choose the employee")
        if not last_working_day:
            raise FormError("Choose the last working day")
        journey_service.create_separation(
            db,
            org_id=user.org_id,
            actor=user,
            employee_id=employee_id,
            exit_type=exit_type,
            last_working_day=date.fromisoformat(last_working_day),
            notice_date=date.fromisoformat(notice_date) if notice_date else None,
            rehire_eligible=rehire_eligible.lower() in {"1", "on", "true", "yes"},
            notes=text(notes),
        )
    except (FormError, DomainError, ValueError) as exc:
        return _error_redirect("/offboarding", exc)
    return RedirectResponse("/offboarding?msg=Offboarding+started", status_code=303)


@router.get("/offboarding/{separation_id}")
def offboarding_detail(
    separation_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        separation = journey_service.get_separation(
            db, org_id=user.org_id, user=user, separation_id=separation_id
        )
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    journey = None
    if separation["journey_id"]:
        journey = journey_service.get_journey(
            db, org_id=user.org_id, user=user, journey_id=separation["journey_id"]
        )
    return render(
        request,
        "journeys/offboarding_detail.html",
        separation=separation,
        journey=journey,
        next_status=NEXT_STATUS.get(separation["status"]),
        exit_type_labels=EXIT_TYPE_LABELS,
        separation_status_labels=journey_service.SEPARATION_STATUS_LABELS,
        is_hr=user.role in HR_ROLES,
        **_journey_helpers(),
    )


@router.post("/offboarding/{separation_id}/advance")
def offboarding_advance(
    separation_id: UUID,
    status: str = Form(""),
    final_pay_ready: str = Form(""),
    exit_interview_done: str = Form(""),
    rehire_eligible: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        journey_service.advance_separation(
            db,
            org_id=user.org_id,
            actor=user,
            separation_id=separation_id,
            status=status or None,
            final_pay_ready=(
                final_pay_ready.lower() in {"1", "on", "true", "yes"}
                if final_pay_ready
                else None
            ),
            exit_interview_done=(
                exit_interview_done.lower() in {"1", "on", "true", "yes"}
                if exit_interview_done
                else None
            ),
            rehire_eligible=(
                rehire_eligible.lower() in {"1", "on", "true", "yes"}
                if rehire_eligible
                else None
            ),
        )
    except DomainError as exc:
        return _error_redirect(f"/offboarding/{separation_id}", exc)
    return RedirectResponse(
        f"/offboarding/{separation_id}?msg=Separation+updated", status_code=303
    )


@router.post("/offboarding/{separation_id}/tasks/{task_id}/complete")
def offboarding_task_complete(
    separation_id: UUID,
    task_id: UUID,
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user()),
):
    try:
        journey_id = _journey_id_for(db, user, separation_id)
    except DomainError as exc:
        return _error_redirect(f"/offboarding/{separation_id}", exc)
    return _complete_task(
        db, user, journey_id, task_id, notes, f"/offboarding/{separation_id}"
    )


@router.post("/offboarding/{separation_id}/tasks/{task_id}/skip")
def offboarding_task_skip(
    separation_id: UUID,
    task_id: UUID,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_user(Role.ADMIN, Role.HR)),
):
    try:
        journey_id = _journey_id_for(db, user, separation_id)
    except DomainError as exc:
        return _error_redirect(f"/offboarding/{separation_id}", exc)
    return _skip_task(
        db, user, journey_id, task_id, reason, f"/offboarding/{separation_id}"
    )


# --- Shared helpers --------------------------------------------------------


def _journey_helpers() -> dict:
    return {
        "task_status_labels": journey_service.TASK_STATUS_LABELS,
        "journey_status_labels": journey_service.JOURNEY_STATUS_LABELS,
    }


def _journey_id_for(db: Session, user, separation_id) -> str:
    separation = journey_service.get_separation(
        db, org_id=user.org_id, user=user, separation_id=separation_id
    )
    if not separation["journey_id"]:
        raise FormError("This separation has no checklist")
    return separation["journey_id"]


def _journey_detail(request, db, user, journey_id, journey_type: JourneyType):
    try:
        journey = journey_service.get_journey(
            db, org_id=user.org_id, user=user, journey_id=journey_id
        )
    except DomainError as exc:
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )
    if journey["journey_type"] != journey_type.value:
        return render(
            request,
            "error.html",
            status_code=404,
            code=404,
            message=f"{journey_type.label} journey not found",
        )
    return render(
        request,
        "journeys/detail.html",
        journey=journey,
        type_label=journey_type.label,
        is_hr=user.role in HR_ROLES,
        **_journey_helpers(),
    )


def _complete_task(db, user, journey_id, task_id, notes, target):
    try:
        journey_service.complete_task(
            db,
            org_id=user.org_id,
            actor=user,
            journey_id=journey_id,
            task_id=task_id,
            notes=text(notes),
        )
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Task+completed", status_code=303)


def _skip_task(db, user, journey_id, task_id, reason, target):
    try:
        journey_service.skip_task(
            db,
            org_id=user.org_id,
            actor=user,
            journey_id=journey_id,
            task_id=task_id,
            reason=text(reason),
        )
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Task+skipped", status_code=303)


def _complete_journey(db, user, journey_id, force, target):
    try:
        journey_service.complete_journey(
            db,
            org_id=user.org_id,
            actor=user,
            journey_id=journey_id,
            force=force.lower() in {"1", "on", "true", "yes"},
        )
    except DomainError as exc:
        return _error_redirect(target, exc)
    return RedirectResponse(f"{target}?msg=Journey+completed", status_code=303)


__all__ = ["router"]

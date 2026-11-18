"""Talent review-cycle service (R4).

Wires review‑cycle actions into the existing R2 DB‑backed job queue.
``enqueue`` is idempotent via ``idempotency_key``; the job is drained by
``peawork worker`` just like ``leave_accrual`` / ``attendance_reconciliation``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from ..domain import (
    CheckInStatus,
    FeedbackRelationship,
    FeedbackStatus,
    Role,
)
from ..models import Employee, ReviewCheckIn, ReviewCycle, User, utcnow
from ..services import audit
from ..services.errors import ForbiddenError, NotFoundError, ValidationError
from ..services.jobs import enqueue as _job_enqueue


def _require_reviewer(user) -> None:
    """Only admin/HR may manage review cycles."""
    if user.role not in { "admin", "hr" }:
        raise ValidationError("Only admin/HR may manage review cycles")


def create_cycle(
    db: Session,
    *,
    org_id,
    actor,
    name: str,
    code: str,
    start_date: date,
    end_date: date,
) -> dict:
    """Create a review cycle; returns a dict (ORM not imported at API layer)."""
    _require_reviewer(actor)
    from ..models import ReviewCycle  # local import to avoid circulars

    existing = db.scalar(
        select(ReviewCycle).where(
            ReviewCycle.org_id == org_id, ReviewCycle.code == code
        )
    )
    if existing is not None:
        raise ValidationError(f"Cycle code '{code}' already exists.")

    if start_date > end_date:
        raise ValidationError("start_date must not be after end_date.")

    cycle = ReviewCycle(
        org_id=org_id,
        name=name,
        code=code,
        start_date=start_date,
        end_date=end_date,
        status="draft",
    )
    db.add(cycle)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="review_cycle",
        entity_id=cycle.id,
        details={"name": name, "code": code, "start_date": str(start_date)},
        org_id=org_id,
    )
    return {"id": str(cycle.id), "code": code, "status": "draft"}


def list_cycles(db: Session, *, org_id, user) -> list[dict]:
    """Return cycles visible to *user* in *org_id*."""
    from ..models import ReviewCycle

    stmt = select(ReviewCycle).where(ReviewCycle.org_id == org_id)

    if user.role in {"admin", "hr"}:
        pass
    elif user.role == "manager" and user.employee_id:
        stmt = stmt.where(ReviewCycle.created_by == user.employee_id)
    elif user.employee_id:
        stmt = stmt.where(ReviewCycle.created_by == user.employee_id)
    else:
        return []

    cycles = db.scalars(stmt.order_by(ReviewCycle.start_date.desc())).all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "code": c.code,
            "start_date": c.start_date.isoformat(),
            "end_date": c.end_date.isoformat(),
            "status": c.status,
        }
        for c in cycles
    ]


def close_cycle(db: Session, *, org_id, actor, cycle_code: str) -> dict:
    """Mark a cycle closed; enqueue a capture‑window job."""
    _require_reviewer(actor)
    from ..models import ReviewCycle

    cycle = db.scalar(
        select(ReviewCycle).where(
            ReviewCycle.org_id == org_id, ReviewCycle.code == cycle_code
        )
    )
    if cycle is None:
        raise ValidationError(f"Cycle '{cycle_code}' not found.")

    cycle.status = "closed"
    db.add(cycle)
    db.flush()

    # Enqueue a one‑shot job to capture late check‑ins.
    key = f"review_close:{cycle.id}"
    _job_enqueue(
        db,
        org_id=org_id,
        actor=None,
        name="review_capture_window",
        payload={"cycle_id": str(cycle.id)},
        idempotency_key=key,
    )

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="review_cycle",
        entity_id=cycle.id,
        details={"status": "closed"},
        org_id=org_id,
    )
    return {"id": str(cycle.id), "status": "closed"}


def enroll_employee(db: Session, *, org_id, actor, cycle_code: str, employee_id: str) -> dict:
    """Assign an employee to a cycle (no-op for R4; status change handled by close)."""
    _require_reviewer(actor)
    from ..models import ReviewCycle

    cycle = db.scalar(
        select(ReviewCycle).where(
            ReviewCycle.org_id == org_id, ReviewCycle.code == cycle_code
        )
    )
    if not cycle:
        raise ValidationError(f"Cycle '{cycle_code}' not found.")
    # R4: promotion to ``active`` happens on first employee assignment in a later phase.
    return {"id": str(cycle.id), "status": cycle.status}


def get_cycle(db: Session, org_id, cycle_code) -> dict:
    """Return cycle details as a dict."""
    from ..models import ReviewCycle

    cycle = db.scalar(
        select(ReviewCycle).where(
            ReviewCycle.org_id == org_id, ReviewCycle.code == cycle_code
        )
    )
    if not cycle:
        raise ValidationError(f"Cycle '{cycle_code}' not found.")
    return {
        "id": str(cycle.id),
        "name": cycle.name,
        "code": cycle.code,
        "start_date": cycle.start_date.isoformat(),
        "end_date": cycle.end_date.isoformat(),
        "status": cycle.status,
    }


# ---- Goal / OKR functions ----


def create_goal(
    db: Session,
    *,
    org_id,
    actor,
    employee_id: str | None,
    code: str,
    title: str,
    objective: str,
    due_date: date | None = None,
) -> dict:
    """Create a goal / OKR.

    ``code`` must be unique per organisation (if supplied).
    ``objective`` defines the team/org level statement.
    ``employee_id`` may be ``None`` for org-wide goals.
    """
    from ..models import Goal

    if employee_id is None:
        # Org-wide goal; treat as no employee
        employee_id = None

    existing = db.scalar(
        select(Goal).where(Goal.org_id == org_id, Goal.code == code)
    )
    if existing is not None:
        raise ValidationError(f"Goal with code '{code}' already exists.")

    goal = Goal(
        org_id=org_id,
        employee_id=employee_id,
        code=code,
        title=title,
        objective=objective,
        due_date=due_date,
        owner_user_id=actor.id,
        created_by=actor.id,
    )
    db.add(goal)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="goal",
        entity_id=goal.id,
        details={"code": code, "title": title, "employee_id": employee_id},
        org_id=org_id,
    )
    return {"id": str(goal.id), "code": code, "title": title, "status": "open"}


def list_goals(db: Session, *, org_id, user) -> list[dict]:
    """Return goals visible to *user* in *org_id*.

    Administrators/HR see all org goals; managers see theirs and their
    reports’; employees see only their own goals.
    """
    from ..models import Goal

    stmt = select(Goal).where(Goal.org_id == org_id)

    if user.role in {"admin", "hr"}:
        pass  # org-wide view
    elif user.role == "manager":
        # managers see their goals and goals of employees they manage
        # simplify for R4: just show goals where employee_id is in reports
        from ..models import Employee
        manager_emp_id = user.employee_id
        if manager_emp_id:
            stmt = stmt.where(
                (Goal.employee_id == manager_emp_id) |
                (Goal.employee_id.in_(
                    select(Employee.id).where(Employee.manager_id == manager_emp_id)
                ))
            )
    elif user.employee_id:
        # employee sees only own goals (null employee_id for org-wide)
        stmt = stmt.where((Goal.employee_id == user.employee_id) | (Goal.employee_id.is_(None)))
    else:
        return []

    goals = db.scalars(stmt.order_by(Goal.created_at.desc())).all()
    return [
        {
            "id": str(g.id),
            "code": g.code,
            "title": g.title,
            "objective": g.objective,
            "employee_id": str(g.employee_id) if g.employee_id else None,
            "status": g.status,
            "progress": float(g.progress),
            "weight": float(g.weight),
            "due_date": g.due_date.isoformat() if g.due_date else None,
        }
        for g in goals
    ]


def get_goal(db: Session, org_id, goal_id: str) -> dict:
    """Return goal details as a dict."""
    from ..models import Goal
    from uuid import UUID

    goal_id_uuid = UUID(goal_id)
    goal = db.scalar(
        select(Goal).where(Goal.org_id == org_id, Goal.id == goal_id_uuid)
    )
    if not goal:
        raise ValidationError(f"Goal '{goal_id}' not found.")
    return {
        "id": str(goal.id),
        "code": goal.code,
        "title": goal.title,
        "objective": goal.objective,
        "employee_id": str(goal.employee_id) if goal.employee_id else None,
        "review_cycle_id": str(goal.review_cycle_id) if goal.review_cycle_id else None,
        "status": goal.status,
        "progress": float(goal.progress),
        "weight": float(goal.weight),
        "due_date": goal.due_date.isoformat() if goal.due_date else None,
        "created_at": goal.created_at.isoformat(),
        "updated_at": goal.updated_at.isoformat(),
    }


def update_goal_progress(
    db: Session,
    *,
    org_id,
    actor,
    goal_id: str,
    progress: float,
    note: str | None = None,
) -> dict:
    """Append-only progress update for a goal.

    ``progress`` must be in ``[0.0, 1.0]``; each update creates a new row in
    ``goal_progress`` and updates the goal's ``progress`` field.
    """
    from ..models import Goal, GoalProgress
    from uuid import UUID

    if not (0.0 <= progress <= 1.0):
        raise ValidationError("progress must be between 0.0 and 1.0")

    goal_id_uuid = UUID(goal_id)
    goal = db.scalar(
        select(Goal).where(Goal.org_id == org_id, Goal.id == goal_id_uuid)
    )
    if not goal:
        raise ValidationError(f"Goal '{goal_id}' not found.")

    # Record progress in the append-only history
    gp = GoalProgress(
        org_id=org_id,
        goal_id=goal.id,
        progress=progress,
        note=note,
        actor_id=actor.id,
    )
    db.add(gp)
    db.flush()

    # Update goal's progress (the last known value)
    goal.progress = progress
    if progress >= 1.0:
        goal.status = "achieved"
    elif progress <= 0.25:
        goal.status = "open"
    db.add(goal)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="goal_progress",
        entity_id=gp.id,
        details={"goal_id": str(goal.id), "progress": progress, "note": note},
        org_id=org_id,
    )
    return {"id": str(goal.id), "progress": progress, "status": goal.status}


def list_goal_progress(db: Session, *, org_id, goal_id: str) -> list[dict]:
    """Return the progress history for a goal."""
    from ..models import GoalProgress
    from uuid import UUID

    goal_id_uuid = UUID(goal_id)
    stmt = select(GoalProgress).where(
        GoalProgress.org_id == org_id,
        GoalProgress.goal_id == goal_id_uuid,
    ).order_by(GoalProgress.created_at.desc())

    rows = db.scalars(stmt).all()
    return [
        {
            "id": str(r.id),
            "progress": float(r.progress),
            "note": r.note,
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# R4: Check-ins
# ---------------------------------------------------------------------------

_HR_ROLES = {Role.ADMIN, Role.HR}
# Roles that may read any employee's talent data.
_READ_ALL_ROLES = {Role.ADMIN, Role.HR, Role.AUDITOR}


def _is_hr(user) -> bool:
    return user.role in _HR_ROLES


def _employee(db: Session, org_id, employee_id) -> "Employee":
    employee = db.get(Employee, UUID(str(employee_id)))
    if employee is None or employee.org_id != org_id:
        raise NotFoundError("Employee not found in this organisation")
    return employee


def _cycle_by_code(db: Session, org_id, cycle_code: str) -> "ReviewCycle":
    cycle = db.scalar(
        select(ReviewCycle).where(
            ReviewCycle.org_id == org_id, ReviewCycle.code == cycle_code
        )
    )
    if cycle is None:
        raise NotFoundError(f"Review cycle '{cycle_code}' not found")
    return cycle


def _require_cycle_open(cycle) -> None:
    if cycle is not None and cycle.status == "closed":
        raise ValidationError(f"Review cycle '{cycle.code}' is closed")


def _can_view_employee(user, employee) -> bool:
    """Object-level visibility for talent data."""
    if user.role in _READ_ALL_ROLES:
        return True
    if employee is None or user.employee_id is None:
        return False
    if employee.id == user.employee_id:
        return True
    return user.role == Role.MANAGER and employee.manager_id == user.employee_id


def _require_view(user, employee) -> None:
    if not _can_view_employee(user, employee):
        # Do not leak the existence of other employees' talent records.
        raise NotFoundError("Employee not found in this organisation")


def _int_rating(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        rating = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise ValidationError("rating must be a number between 1 and 5") from exc
    if rating < 1 or rating > 5:
        raise ValidationError("rating must be between 1 and 5")
    return rating.quantize(Decimal("0.1"))


def _serialize_check_in(db: Session, check_in) -> dict:
    employee = db.get(Employee, check_in.employee_id)
    manager = db.get(Employee, check_in.manager_id) if check_in.manager_id else None
    cycle = db.get(ReviewCycle, check_in.cycle_id)
    return {
        "id": str(check_in.id),
        "cycle_id": str(check_in.cycle_id),
        "cycle_code": cycle.code if cycle else None,
        "cycle_status": cycle.status if cycle else None,
        "employee_id": str(check_in.employee_id),
        "employee_name": employee.full_name if employee else None,
        "manager_id": str(check_in.manager_id) if check_in.manager_id else None,
        "manager_name": manager.full_name if manager else None,
        "due_on": check_in.due_on.isoformat() if check_in.due_on else None,
        "status": check_in.status.value,
        "employee_notes": check_in.employee_notes,
        "manager_notes": check_in.manager_notes,
        "rating": float(check_in.rating) if check_in.rating is not None else None,
        "submitted_at": check_in.submitted_at.isoformat() if check_in.submitted_at else None,
        "created_at": check_in.created_at.isoformat(),
    }


def create_check_in(
    db: Session,
    *,
    org_id,
    actor,
    cycle_code: str,
    employee_id: str,
    due_on: date | None = None,
) -> dict:
    """Open a check-in for one employee inside a review cycle (HR only)."""
    _require_reviewer(actor)
    cycle = _cycle_by_code(db, org_id, cycle_code)
    _require_cycle_open(cycle)
    employee = _employee(db, org_id, employee_id)

    existing = db.scalar(
        select(ReviewCheckIn).where(
            ReviewCheckIn.cycle_id == cycle.id,
            ReviewCheckIn.employee_id == employee.id,
        )
    )
    if existing is not None:
        raise ValidationError(
            f"A check-in already exists for {employee.full_name} in cycle '{cycle.code}'"
        )

    check_in = ReviewCheckIn(
        org_id=org_id,
        cycle_id=cycle.id,
        employee_id=employee.id,
        manager_id=employee.manager_id,
        due_on=due_on or cycle.end_date,
        status=CheckInStatus.PENDING,
    )
    db.add(check_in)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="review_check_in",
        entity_id=check_in.id,
        details={"cycle": cycle.code, "employee_id": str(employee.id)},
        org_id=org_id,
    )
    return _serialize_check_in(db, check_in)


def list_check_ins(
    db: Session,
    *,
    org_id,
    user,
    cycle_code: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Check-ins visible to *user*: HR-wide, manager team, or self."""
    stmt = select(ReviewCheckIn).where(ReviewCheckIn.org_id == org_id)
    if user.role in _READ_ALL_ROLES:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        report_ids = select(Employee.id).where(
            Employee.org_id == org_id, Employee.manager_id == user.employee_id
        )
        stmt = stmt.where(
            or_(
                ReviewCheckIn.employee_id == user.employee_id,
                ReviewCheckIn.employee_id.in_(report_ids),
            )
        )
    elif user.employee_id:
        stmt = stmt.where(ReviewCheckIn.employee_id == user.employee_id)
    else:
        return []

    if cycle_code:
        stmt = stmt.where(ReviewCheckIn.cycle_id == _cycle_by_code(db, org_id, cycle_code).id)
    if status:
        stmt = stmt.where(ReviewCheckIn.status == status)

    rows = db.scalars(stmt.order_by(ReviewCheckIn.due_on, ReviewCheckIn.created_at)).all()
    return [_serialize_check_in(db, row) for row in rows]


def get_check_in(db: Session, *, org_id, user, check_in_id: str) -> dict:
    check_in = db.scalar(
        select(ReviewCheckIn).where(
            ReviewCheckIn.org_id == org_id, ReviewCheckIn.id == UUID(str(check_in_id))
        )
    )
    if check_in is None:
        raise NotFoundError(f"Check-in '{check_in_id}' not found")
    _require_view(user, db.get(Employee, check_in.employee_id))
    return _serialize_check_in(db, check_in)


def submit_check_in(
    db: Session,
    *,
    org_id,
    actor,
    check_in_id: str,
    employee_notes: str | None = None,
    manager_notes: str | None = None,
    rating=None,
) -> dict:
    """Record check-in notes.

    Employees write their own notes; managers write manager notes and the
    rating; HR may write both. Edits stay open until the cycle is closed.
    """
    check_in = db.scalar(
        select(ReviewCheckIn).where(
            ReviewCheckIn.org_id == org_id, ReviewCheckIn.id == UUID(str(check_in_id))
        )
    )
    if check_in is None:
        raise NotFoundError(f"Check-in '{check_in_id}' not found")

    employee = db.get(Employee, check_in.employee_id)
    _require_view(actor, employee)
    cycle = db.get(ReviewCycle, check_in.cycle_id)
    _require_cycle_open(cycle)

    is_subject = actor.employee_id is not None and check_in.employee_id == actor.employee_id
    is_manager = bool(
        actor.role == Role.MANAGER
        and employee is not None
        and employee.manager_id == actor.employee_id
    )
    if manager_notes is not None or rating is not None:
        # The subject may describe their own work but never grade themselves.
        if not (_is_hr(actor) or is_manager):
            raise ForbiddenError("You cannot write manager notes for this check-in")
    if employee_notes is not None and not (_is_hr(actor) or is_subject or is_manager):
        raise ForbiddenError("You cannot write employee notes for this check-in")

    rating_value = _int_rating(rating)
    if employee_notes is None and manager_notes is None and rating_value is None:
        raise ValidationError("Provide notes or a rating before submitting")

    if employee_notes is not None:
        check_in.employee_notes = employee_notes
    if manager_notes is not None:
        check_in.manager_notes = manager_notes
    if rating_value is not None:
        check_in.rating = rating_value
    check_in.status = CheckInStatus.SUBMITTED
    check_in.submitted_at = utcnow()
    check_in.submitted_by = actor.id
    db.add(check_in)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="review_check_in",
        entity_id=check_in.id,
        details={"status": "submitted"},
        org_id=org_id,
    )
    return _serialize_check_in(db, check_in)


# ---------------------------------------------------------------------------
# R4: 360 feedback
# ---------------------------------------------------------------------------


def _serialize_feedback(db: Session, request, *, include_reviewer: bool = True) -> dict:
    subject = db.get(Employee, request.subject_employee_id)
    reviewer = (
        db.get(Employee, request.reviewer_employee_id)
        if include_reviewer and request.reviewer_employee_id
        else None
    )
    cycle = db.get(ReviewCycle, request.cycle_id) if request.cycle_id else None
    return {
        "id": str(request.id),
        "cycle_id": str(request.cycle_id) if request.cycle_id else None,
        "cycle_code": cycle.code if cycle else None,
        "subject_employee_id": str(request.subject_employee_id),
        "subject_name": subject.full_name if subject else None,
        "reviewer_employee_id": str(request.reviewer_employee_id)
        if include_reviewer and request.reviewer_employee_id
        else None,
        "reviewer_name": reviewer.full_name if reviewer else None,
        "relationship": request.relationship.value,
        "relationship_label": request.relationship.label,
        "anonymous": bool(request.anonymous),
        "status": request.status.value,
        "due_on": request.due_on.isoformat() if request.due_on else None,
        "strengths": request.strengths,
        "improvements": request.improvements,
        "comments": request.comments,
        "rating": float(request.rating) if request.rating is not None else None,
        "submitted_at": request.submitted_at.isoformat() if request.submitted_at else None,
        "created_at": request.created_at.isoformat(),
    }


def create_feedback_request(
    db: Session,
    *,
    org_id,
    actor,
    subject_employee_id: str,
    reviewer_employee_id: str | None = None,
    cycle_code: str | None = None,
    relationship: str = "peer",
    anonymous: bool = False,
    due_on: date | None = None,
) -> dict:
    """Ask someone for 360 feedback about an employee.

    HR/admin may ask anyone about anyone. A manager may ask about themselves or
    a direct report. Everyone else may only request a self-review. ``anonymous``
    requests are scrubbed of reviewer identity when submitted.
    """
    try:
        relationship_enum = FeedbackRelationship(str(relationship))
    except ValueError as exc:
        raise ValidationError(
            "relationship must be one of: "
            + ", ".join(item.value for item in FeedbackRelationship)
        ) from exc

    subject = _employee(db, org_id, subject_employee_id)
    if not _is_hr(actor):
        allowed = subject.id == actor.employee_id or (
            actor.role == Role.MANAGER
            and subject.manager_id == actor.employee_id
        )
        if not allowed:
            raise ForbiddenError("You can only request feedback about yourself or your reports")

    if relationship_enum == FeedbackRelationship.SELF:
        reviewer_employee_id = str(subject.id)
    elif not reviewer_employee_id:
        raise ValidationError("reviewer_employee_id is required for this relationship")

    reviewer = _employee(db, org_id, reviewer_employee_id)
    cycle = _cycle_by_code(db, org_id, cycle_code) if cycle_code else None
    _require_cycle_open(cycle)

    existing = db.scalar(
        select(FeedbackRequest).where(
            FeedbackRequest.org_id == org_id,
            FeedbackRequest.subject_employee_id == subject.id,
            FeedbackRequest.reviewer_employee_id == reviewer.id,
            FeedbackRequest.cycle_id == (cycle.id if cycle else None),
        )
    )
    if existing is not None:
        raise ValidationError(
            "A feedback request already exists for this reviewer in this cycle"
        )

    request = FeedbackRequest(
        org_id=org_id,
        cycle_id=cycle.id if cycle else None,
        subject_employee_id=subject.id,
        reviewer_employee_id=reviewer.id,
        reviewer_user_id=(
            db.scalar(select(User.id).where(User.employee_id == reviewer.id))
        ),
        relationship=relationship_enum,
        anonymous=bool(anonymous),
        status=FeedbackStatus.PENDING,
        due_on=due_on or (cycle.end_date if cycle else None),
        requested_by=actor.id,
    )
    db.add(request)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="feedback_request",
        entity_id=request.id,
        details={
            "subject_id": str(subject.id),
            "reviewer_id": str(reviewer.id),
            "relationship": relationship_enum.value,
            "anonymous": bool(anonymous),
        },
        org_id=org_id,
    )
    return _serialize_feedback(db, request)


def _reviewer_owns(actor, request) -> bool:
    if request.reviewer_user_id is not None and request.reviewer_user_id == actor.id:
        return True
    return bool(
        request.reviewer_employee_id
        and actor.employee_id
        and request.reviewer_employee_id == actor.employee_id
    )


def list_my_feedback_requests(
    db: Session, *, org_id, actor, status: str | None = None
) -> list[dict]:
    """The reviewer inbox: requests assigned to the signed-in user."""
    stmt = select(FeedbackRequest).where(
        FeedbackRequest.org_id == org_id,
        or_(
            FeedbackRequest.reviewer_user_id == actor.id,
            (
                FeedbackRequest.reviewer_employee_id == actor.employee_id
                if actor.employee_id
                else false()
            ),
        ),
    )
    if status:
        stmt = stmt.where(FeedbackRequest.status == status)
    rows = db.scalars(
        stmt.order_by(FeedbackRequest.status, FeedbackRequest.due_on)
    ).all()
    return [_serialize_feedback(db, row) for row in rows]


def submit_feedback(
    db: Session,
    *,
    org_id,
    actor,
    request_id: str,
    strengths: str | None = None,
    improvements: str | None = None,
    comments: str | None = None,
    rating=None,
) -> dict:
    """Submit feedback content as the assigned reviewer.

    Anonymous submissions erase the reviewer link in the same transaction, so
    the identity cannot be recovered later, including from the audit trail.
    """
    request = db.scalar(
        select(FeedbackRequest).where(
            FeedbackRequest.org_id == org_id,
            FeedbackRequest.id == UUID(str(request_id)),
        )
    )
    if request is None or not _reviewer_owns(actor, request):
        raise NotFoundError(f"Feedback request '{request_id}' not found")
    if request.status != FeedbackStatus.PENDING:
        raise ValidationError(
            f"Feedback request is already {request.status.value}"
        )
    if not any([(strengths or "").strip(), (improvements or "").strip(), (comments or "").strip()]):
        raise ValidationError("Provide at least one feedback comment before submitting")

    cycle = db.get(ReviewCycle, request.cycle_id) if request.cycle_id else None
    _require_cycle_open(cycle)

    request.strengths = (strengths or "").strip() or None
    request.improvements = (improvements or "").strip() or None
    request.comments = (comments or "").strip() or None
    request.rating = _int_rating(rating)
    request.status = FeedbackStatus.SUBMITTED
    request.submitted_at = utcnow()

    anonymous = bool(request.anonymous)
    if anonymous:
        # Deliberate: identity is destroyed, not merely hidden.
        request.reviewer_employee_id = None
        request.reviewer_user_id = None

    db.add(request)
    db.flush()

    audit.record(
        db,
        actor=None if anonymous else actor,
        action="update",
        entity_type="feedback_request",
        entity_id=request.id,
        details={"status": "submitted", "anonymous": anonymous},
        org_id=org_id,
    )
    return _serialize_feedback(db, request)


def decline_feedback(db: Session, *, org_id, actor, request_id: str) -> dict:
    """Decline to provide feedback (reviewer only)."""
    request = db.scalar(
        select(FeedbackRequest).where(
            FeedbackRequest.org_id == org_id,
            FeedbackRequest.id == UUID(str(request_id)),
        )
    )
    if request is None or not _reviewer_owns(actor, request):
        raise NotFoundError(f"Feedback request '{request_id}' not found")
    if request.status != FeedbackStatus.PENDING:
        raise ValidationError(f"Feedback request is already {request.status.value}")

    request.status = FeedbackStatus.DECLINED
    db.add(request)
    db.flush()

    audit.record(
        db,
        actor=None if request.anonymous else actor,
        action="update",
        entity_type="feedback_request",
        entity_id=request.id,
        details={"status": "declined", "anonymous": bool(request.anonymous)},
        org_id=org_id,
    )
    return _serialize_feedback(db, request)


def list_feedback_for_employee(
    db: Session, *, org_id, actor, employee_id: str
) -> list[dict]:
    """Submitted feedback about an employee, subject to the visibility matrix.

    Self, manager-of and HR/admin/auditor may read. Anonymous rows never include
    reviewer data because it no longer exists.
    """
    employee = _employee(db, org_id, employee_id)
    _require_view(actor, employee)

    rows = db.scalars(
        select(FeedbackRequest)
        .where(
            FeedbackRequest.org_id == org_id,
            FeedbackRequest.subject_employee_id == employee.id,
            FeedbackRequest.status == FeedbackStatus.SUBMITTED,
        )
        .order_by(FeedbackRequest.submitted_at.desc())
    ).all()
    return [_serialize_feedback(db, row) for row in rows]

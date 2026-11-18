"""Talent review-cycle service (R4).

Wires review‑cycle actions into the existing R2 DB‑backed job queue.
``enqueue`` is idempotent via ``idempotency_key``; the job is drained by
``peawork worker`` just like ``leave_accrual`` / ``attendance_reconciliation``.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services import audit
from ..services.errors import ValidationError
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
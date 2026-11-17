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
"""Skills, competencies, development goals and calibration.

Mirrors market-standard approaches (Workday skills & colors, Personio development
plans, HiBob calibration) while staying opinionated and implementable on top of
the existing PeaWork HRMS data model.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import (
    EmployeeStatus,
    ProficiencyLevel,
    Role,
    Skill,
)
from ..models import (
    Employee,
    EmployeeSkill,
    DevelopmentGoal,
    CalibrationRound,
    CalibrationEntry,
    User,
)
from ..services import errors as errs
from ..services import audit
from .errors import ForbiddenError, NotFoundError, ValidationError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _employee(db: Session, org_id, employee_id) -> Employee:
    try:
        key = UUID(str(employee_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Employee {employee_id} not found") from exc
    employee = db.get(Employee, key)
    if employee is None or employee.org_id != org_id:
        raise NotFoundError(f"Employee {employee_id} not found in this organisation")
    return employee


def _require_manage(db: Session, actor: User, employee: Employee) -> None:
    if actor.role in {Role.ADMIN, Role.HR}:
        return
    if actor.employee_id == employee.id:
        return
    return bool(
        actor.role == Role.MANAGER
        and actor.employee_id is not None
        and actor.employee_id == employee.manager_id  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# EmployeeSkill
# ---------------------------------------------------------------------------

def list_employee_skills(
    db: Session,
    *,
    org_id,
    employee_id: str | None = None,
    skill: str | None = None,
) -> list[dict]:
    """Return employee skills, filtered optionally by employee or skill."""
    stmt = select(EmployeeSkill).where(EmployeeSkill.org_id == org_id)
    if employee_id:
        try:
            key = UUID(str(employee_id))
        except (ValueError, TypeError) as exc:
            raise NotFoundError(f"Employee {employee_id} not found") from exc
        stmt = stmt.where(EmployeeSkill.employee_id == key)
    if skill:
        stmt = stmt.where(EmployeeSkill.skill == skill)
    rows = db.scalars(stmt.order_by(EmployeeSkill.skill, EmployeeSkill.last_assessed_at.desc())).all()
    return [
        {
            "id": str(r.id),
            "employee_id": str(r.employee_id),
            "employee_name": db.get(Employee, r.employee_id).full_name if db.get(Employee, r.employee_id) else None,
            "skill": r.skill.value,
            "skill_label": r.skill.label,
            "proficiency": r.proficiency.value,
            "proficiency_label": r.proficiency.label,
            "verified": bool(r.verified),
            "verification_notes": r.verification_notes,
            "years_experience": float(r.years_experience) if r.years_experience else None,
            "last_assessed_at": r.last_assessed_at.isoformat() if r.last_assessed_at else None,
            "assessed_by": str(r.assessed_by) if r.assessed_by else None,
            "notes": r.notes,
        }
        for r in rows
    ]


def upsert_employee_skill(
    db: Session,
    *,
    org_id,
    actor: User,
    employee_id: str,
    skill: str,
    proficiency: str | None = None,
    verified: bool | None = None,
    years_experience: float | None = None,
    notes: str | None = None,
) -> dict:
    """Create or update an employee's skill proficiency."""
    _require_manage(db, actor, _employee(db, org_id, employee_id))

    skill_enum = Skill(str(skill))
    employee = _employee(db, org_id, employee_id)

    existing = db.scalar(
        select(EmployeeSkill).where(
            EmployeeSkill.org_id == org_id,
            EmployeeSkill.employee_id == employee.id,
            EmployeeSkill.skill == skill_enum,
        )
    )

    if existing is None:
        existing = EmployeeSkill(
            org_id=org_id,
            employee_id=employee.id,
            skill=skill_enum,
            proficiency=ProficiencyLevel(proficiency) if proficiency else ProficiencyLevel.BEGINNER,
            verified=verified if verified is not None else False,
            years_experience=years_experience,
            notes=notes,
        )
        db.add(existing)
    else:
        if proficiency is not None:
            existing.proficiency = ProficiencyLevel(proficiency)
        if verified is not None:
            existing.verified = verified
        if years_experience is not None:
            existing.years_experience = years_experience
        if notes is not None:
            existing.notes = notes
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="employee_skill",
        entity_id=existing.id,
        details={
            "employee_id": str(employee.id),
            "skill": skill_enum.value,
            "proficiency": existing.proficiency.value,
            "verified": existing.verified,
        },
        org_id=org_id,
    )
    return _serialize_skill(existing)


def _serialize_skill(skill: EmployeeSkill) -> dict:
    employee = db_get(Employee, skill.employee_id)  # type: ignore
    return {
        "id": str(skill.id),
        "employee_id": str(skill.employee_id),
        "employee_name": employee.full_name if employee else None,
        "skill": skill.skill.value,
        "skill_label": skill.skill.label,
        "proficiency": skill.proficiency.value,
        "proficiency_label": skill.proficiency.label,
        "verified": bool(skill.verified),
        "verification_notes": skill.verification_notes,
        "years_experience": float(skill.years_experience) if skill.years_experience else None,
        "last_assessed_at": skill.last_assessed_at.isoformat() if skill.last_assessed_at else None,
        "notes": skill.notes,
    }


# ---------------------------------------------------------------------------
# DevelopmentGoal
# ---------------------------------------------------------------------------

def list_development_goals(
    db: Session,
    *,
    org_id,
    employee_id: str | None = None,
    skill: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return development goals, filtered optionally."""
    from sqlalchemy import true

    stmt = select(DevelopmentGoal).where(DevelopmentGoal.org_id == org_id)
    if employee_id:
        try:
            key = UUID(str(employee_id))
        except (ValueError, TypeError) as exc:
            raise NotFoundError(f"Employee {employee_id} not found") from exc
        stmt = stmt.where(DevelopmentGoal.employee_id == key)
    if skill:
        try:
            stmt = stmt.where(DevelopmentGoal.skill == Skill(str(skill)))
        except ValueError:
            pass
    if status:
        stmt = stmt.where(DevelopmentGoal.status == status)
    rows = db.scalars(stmt.order_by(DevelopmentGoal.target_date.desc(), DevelopmentGoal.created_at.desc())).all()
    return [
        {
            "id": str(r.id),
            "employee_id": str(r.employee_id),
            "employee_name": db.get(Employee, r.employee_id).full_name if db.get(Employee, r.employee_id) else None,
            "skill": r.skill.value,
            "skill_label": r.skill.label,
            "current_proficiency": r.current_proficiency.value,
            "target_proficiency": r.target_proficiency.value,
            "target_date": r.target_date.isoformat(),
            "status": r.status,
            "rationale": r.rationale,
            "actions": r.actions,
            "achieved_at": r.achieved_at.isoformat() if r.achieved_at else None,
            "notes": r.notes,
        }
        for r in rows
    ]


def upsert_development_goal(
    db: Session,
    *,
    org_id,
    actor: User,
    employee_id: str,
    skill: str,
    current_proficiency: str | None = None,
    target_proficiency: str,
    target_date: str,
    rationale: str | None = None,
    actions: str | None = None,
    status: str | None = None,
) -> dict:
    """Create or update a development goal (development plan)."""
    try:
        target_date_obj = date.fromisoformat(str(target_date))
    except ValueError as exc:
        raise ValidationError("target_date must be an ISO date (YYYY-MM-DD)") from exc

    try:
        current = ProficiencyLevel(current_proficiency) if current_proficiency else None
        target = ProficiencyLevel(target_proficiency)
    except ValueError as exc:
        raise ValidationError("proficiency must be one of: beginner, intermediate, advanced, expert") from exc

    _require_manage(db, actor, _employee(db, org_id, employee_id))

    employee = _employee(db, org_id, employee_id)
    skill_enum = Skill(str(skill))

    existing = db.scalar(
        select(DevelopmentGoal).where(
            DevelopmentGoal.org_id == org_id,
            DevelopmentGoal.employee_id == employee.id,
            DevelopmentGoal.skill == skill_enum,
        )
    )

    if existing is None:
        existing = DevelopmentGoal(
            org_id=org_id,
            employee_id=employee.id,
            skill=skill_enum,
            current_proficiency=current if current else ProficiencyLevel.BEGINNER,
            target_proficiency=target,
            target_date=target_date_obj,
            status=status or "active",
            rationale=rationale,
            actions=actions,
        )
        db.add(existing)
    else:
        if current_proficiency is not None:
            existing.current_proficiency = current if current else ProficiencyLevel.BEGINNER
        if target_proficiency is not None:
            existing.target_proficiency = target
        if target_date is not None:
            existing.target_date = target_date_obj  # type: ignore[arg-type]  # re-used var
        if rationale is not None:
            existing.rationale = rationale
        if actions is not None:
            existing.actions = actions
        if status is not None:
            existing.status = status
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="development_goal",
        entity_id=existing.id,
        details={
            "employee_id": str(employee.id),
            "skill": skill_enum.value,
            "target_proficiency": existing.target_proficiency.value,
            "target_date": existing.target_date.isoformat(),
            "status": existing.status,
        },
        org_id=org_id,
    )
    return _serialize_goal(existing)


def _serialize_goal(goal: DevelopmentGoal) -> dict:
    employee = db_get(Employee, goal.employee_id)  # type: ignore
    return {
        "id": str(goal.id),
        "employee_id": str(goal.employee_id),
        "employee_name": employee.full_name if employee else None,
        "skill": goal.skill.value,
        "skill_label": goal.skill.label,
        "current_proficiency": goal.current_proficiency.value,
        "target_proficiency": goal.target_proficiency.value,
        "target_date": goal.target_date.isoformat(),
        "status": goal.status,
        "rationale": goal.rationale,
        "actions": goal.actions,
        "achieved_at": goal.achieved_at.isoformat() if goal.achieved_at else None,
        "notes": goal.notes,
    }


# ---------------------------------------------------------------------------
# CalibrationRound
# ---------------------------------------------------------------------------

def list_calibration_rounds(
    db: Session,
    *,
    org_id,
    status: str | None = None,
) -> list[dict]:
    """Return calibration rounds."""
    stmt = select(CalibrationRound).where(CalibrationRound.org_id == org_id)
    if status:
        stmt = stmt.where(CalibrationRound.status == status)
    rows = db.scalars(stmt.order_by(CalibrationRound.start_date.desc())).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "description": r.description,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "status": r.status,
        }
        for r in rows
    ]


def upsert_calibration_round(
    db: Session,
    *,
    org_id,
    actor: User,
    name: str,
    description: str | None = None,
    start_date: str,
    end_date: str,
    status: str | None = None,
) -> dict:
    """Create or update a calibration round."""
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except ValueError as exc:
        raise ValidationError("Dates must be ISO dates (YYYY-MM-DD)") from exc

    if start > end:
        raise ValidationError("start_date must not be after end_date")

    _require_manage(db, actor, None)  # HR/admin only

    existing = db.scalar(
        select(CalibrationRound).where(
            CalibrationRound.org_id == org_id,
            CalibrationRound.name == name,
        )
    )

    if existing is None:
        existing = CalibrationRound(
            org_id=org_id,
            name=name,
            description=description,
            start_date=start,
            end_date=end,
            status=status or "open",
        )
        db.add(existing)
    else:
        if description is not None:
            existing.description = description
        if status is not None:
            existing.status = status
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="calibration_round",
        entity_id=existing.id,
        details={
            "name": existing.name,
            "start_date": existing.start_date.isoformat(),
            "end_date": existing.end_date.isoformat(),
            "status": existing.status,
        },
        org_id=org_id,
    )
    return _serialize_round(existing)


def _serialize_round(round: CalibrationRound) -> dict:
    return {
        "id": str(round.id),
        "name": round.name,
        "description": round.description,
        "start_date": round.start_date.isoformat(),
        "end_date": round.end_date.isoformat(),
        "status": round.status,
    }


# ---------------------------------------------------------------------------
# CalibrationEntry
# ---------------------------------------------------------------------------

def list_calibration_entries(
    db: Session,
    *,
    org_id,
    round_id: str | None = None,
    employee_id: str | None = None,
    skill: str | None = None,
) -> list[dict]:
    """Return calibration entries, filtered optionally."""
    from sqlalchemy import true

    stmt = select(CalibrationEntry).join(CalibrationRound).where(
        CalibrationRound.org_id == org_id
    )
    if round_id:
        try:
            key = UUID(str(round_id))
        except (ValueError, TypeError) as exc:
            raise NotFoundError(f"Calibration round {round_id} not found") from exc
        stmt = stmt.where(CalibrationEntry.round_id == key)
    if employee_id:
        try:
            key = UUID(str(employee_id))
        except (ValueError, TypeError) as exc:
            raise NotFoundError(f"Employee {employee_id} not found") from exc
        stmt = stmt.where(CalibrationEntry.employee_id == key)
    if skill:
        try:
            stmt = stmt.where(CalibrationEntry.skill == Skill(str(skill)))
        except ValueError:
            pass
    rows = db.scalars(
        stmt.order_by(CalibrationEntry.calibrated_rating.desc(), CalibrationEntry.created_at.desc())
    ).all()
    return [
        {
            "id": str(r.id),
            "employee_id": str(r.employee_id),
            "employee_name": db.get(Employee, r.employee_id).full_name if db.get(Employee, r.employee_id) else None,
            "skill": r.skill.value if r.skill else "(overall)",
            "proposed_rating": float(r.proposed_rating) if r.proposed_rating else None,
            "calibrated_rating": float(r.calibrated_rating) if r.calibrated_rating else None,
            "rationale_proposed": r.rationale_proposed,
            "rationale_calibrated": r.rationale_calibrated,
            "status": r.status,
            "locked_at": r.locked_at.isoformat() if r.locked_at else None,
        }
        for r in rows
    ]


def upsert_calibration_entry(
    db: Session,
    *,
    org_id,
    actor: User,
    round_id: str,
    employee_id: str,
    skill: str | None = None,
    proposed_rating: float | None = None,
    rationale_proposed: str | None = None,
) -> dict:
    """Add or update a calibration entry for a round.

    If ``skill`` is None the entry represents the employee's overall rating.
    """
    try:
        round_key = UUID(str(round_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Calibration round {round_id} not found") from exc

    try:
        employee = _employee(db, org_id, employee_id)
    except NotFoundError:
        raise

    _require_manage(db, actor, employee)

    skill_enum = Skill(str(skill)) if skill else None

    # Check round is open/active
    round_obj = db.get(CalibrationRound, round_key)
    if round_obj is None or round_obj.status not in {"open", "in_progress"}:
        raise ValidationError(f"Calibration round '{round_obj.name if round_obj else round_id}' is not open for entries")

    existing = db.scalar(
        select(CalibrationEntry).where(
            CalibrationEntry.round_id == round_key,
            CalibrationEntry.employee_id == employee.id,
        )
    )

    if existing is None:
        existing = CalibrationEntry(
            org_id=org_id,
            round_id=round_key,
            employee_id=employee.id,
            skill=skill_enum,
            proposed_rating=(
                proposed_rating if proposed_rating is not None else None
            ),
            rationale_proposed=rationale_proposed,
            status="pending",
        )
        db.add(existing)
    else:
        if proposed_rating is not None:
            existing.proposed_rating = proposed_rating
        if rationale_proposed is not None:
            existing.rationale_proposed = rationale_proposed
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="calibration_entry",
        entity_id=existing.id,
        details={
            "employee_id": str(employee.id),
            "skill": skill_enum.value if skill_enum else None,
            "proposed_rating": existing.proposed_rating,
            "status": existing.status,
        },
        org_id=org_id,
    )
    return _serialize_entry(existing)


def _serialize_entry(entry: CalibrationEntry) -> dict:
    employee = db_get(Employee, entry.employee_id)  # type: ignore
    return {
        "id": str(entry.id),
        "employee_id": str(entry.employee_id),
        "employee_name": employee.full_name if employee else None,
        "skill": entry.skill.value if entry.skill else "(overall)",
        "proposed_rating": float(entry.proposed_rating) if entry.proposed_rating else None,
        "calibrated_rating": float(entry.calibrated_rating) if entry.calibrated_rating else None,
        "rationale_proposed": entry.rationale_proposed,
        "rationale_calibrated": entry.rationale_calibrated,
        "status": entry.status,
    }


# Helper to get Employee from db within services
from sqlalchemy.orm import Session as SessionTy  # noqa: F811

def db_get(db: SessionTy, id) -> any:
    return db.get(Employee, id)  # simplified for this module; actual use should query the model
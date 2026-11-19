"""Lifecycle journeys: onboarding, offboarding, crossboarding and probation.

Mirrors how Workday/Oracle/BambooHR model lifecycle work: a **template** of
tasks, instantiated as a **journey** for one employee, with each task assigned
to an owner role and due relative to an anchor date (start date or last working
day). Offboarding adds a **separation** case around the journey.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain import (
    EmployeeStatus,
    ExitType,
    JourneyStatus,
    JourneyTaskOwner,
    JourneyTaskStatus,
    JourneyType,
    Role,
    SeparationStatus,
)
from ..models import (
    Employee,
    Journey,
    JourneyTask,
    JourneyTemplate,
    JourneyTemplateTask,
    Separation,
    User,
)
from . import audit
from .errors import ForbiddenError, NotFoundError, ValidationError

_HR_ROLES = {Role.ADMIN, Role.SUPER_ADMIN, Role.HR}
_READ_ALL_ROLES = {Role.ADMIN, Role.SUPER_ADMIN, Role.HR, Role.AUDITOR}

# Default templates are seeded per organisation on first use. Tenants can edit
# them later without a migration.
DEFAULT_TEMPLATES: dict[JourneyType, dict] = {
    JourneyType.ONBOARDING: {
        "code": "onboarding-default",
        "name": "Standard onboarding",
        "description": "Preboarding through the first week.",
        "tasks": [
            ("Personal details and emergency contact", JourneyTaskOwner.EMPLOYEE, -7, True),
            ("Sign contract and policy acknowledgements", JourneyTaskOwner.HR, -5, True),
            ("Right-to-work and tax documents", JourneyTaskOwner.HR, -3, True),
            ("Create account, email and access groups", JourneyTaskOwner.IT, -1, True),
            ("Prepare laptop, badge and workspace", JourneyTaskOwner.IT, -1, True),
            ("Welcome message and buddy assignment", JourneyTaskOwner.MANAGER, -1, False),
            ("30/60/90 day plan", JourneyTaskOwner.MANAGER, 7, True),
            ("First-week check-in", JourneyTaskOwner.MANAGER, 7, True),
        ],
    },
    JourneyType.OFFBOARDING: {
        "code": "offboarding-default",
        "name": "Standard offboarding",
        "description": "Notice, handover, clearance and final pay.",
        "tasks": [
            ("Acknowledge exit and issue letter", JourneyTaskOwner.HR, -14, True),
            ("Handover document and knowledge transfer", JourneyTaskOwner.EMPLOYEE, -5, True),
            ("Manager sign-off on handover", JourneyTaskOwner.MANAGER, -3, True),
            ("Return laptop, phone and badge", JourneyTaskOwner.IT, -1, True),
            ("Revoke system access", JourneyTaskOwner.IT, 0, True),
            ("Final pay and leave encashment", JourneyTaskOwner.FINANCE, 0, True),
            ("Exit interview", JourneyTaskOwner.HR, -1, False),
        ],
    },
    JourneyType.CROSSBOARDING: {
        "code": "crossboarding-default",
        "name": "Internal move",
        "description": "Crossboarding for an internal transfer or promotion.",
        "tasks": [
            ("Confirm new role, manager and effective date", JourneyTaskOwner.HR, -7, True),
            ("Handover plan with current manager", JourneyTaskOwner.MANAGER, -3, True),
            ("Update access groups and equipment", JourneyTaskOwner.IT, 0, True),
            ("New manager 30-day plan", JourneyTaskOwner.MANAGER, 7, True),
        ],
    },
    JourneyType.PROBATION: {
        "code": "probation-default",
        "name": "Probation review",
        "description": "Confirmation decision at the end of probation.",
        "tasks": [
            ("Manager probation review and recommendation", JourneyTaskOwner.MANAGER, 0, True),
            ("HR confirmation decision", JourneyTaskOwner.HR, 3, True),
        ],
    },
}

_TASK_STATUS_LABELS = {
    JourneyTaskStatus.PENDING.value: "Pending",
    JourneyTaskStatus.IN_PROGRESS.value: "In progress",
    JourneyTaskStatus.DONE.value: "Done",
    JourneyTaskStatus.SKIPPED.value: "Skipped",
}

_JOURNEY_STATUS_LABELS = {
    JourneyStatus.DRAFT.value: "Draft",
    JourneyStatus.IN_PROGRESS.value: "In progress",
    JourneyStatus.COMPLETED.value: "Completed",
    JourneyStatus.CANCELLED.value: "Cancelled",
}

_SEPARATION_STATUS_LABELS = {
    SeparationStatus.INITIATED.value: "Initiated",
    SeparationStatus.NOTICE.value: "Notice",
    SeparationStatus.CLEARANCE.value: "Clearance",
    SeparationStatus.FINAL_PAY.value: "Final pay",
    SeparationStatus.EXITED.value: "Exited",
    SeparationStatus.WITHDRAWN.value: "Withdrawn",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_hr(actor: User) -> bool:
    return actor.role in _HR_ROLES


def _employee(db: Session, org_id, employee_id) -> Employee:
    try:
        key = UUID(str(employee_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Employee {employee_id} not found") from exc
    employee = db.get(Employee, key)
    if employee is None or employee.org_id != org_id:
        raise NotFoundError(f"Employee {employee_id} not found in this organisation")
    return employee


def _user_for_employee(db: Session, employee_id) -> UUID | None:
    return db.scalar(select(User.id).where(User.employee_id == employee_id))


def _journey(db: Session, org_id, journey_id) -> Journey:
    try:
        key = UUID(str(journey_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Journey {journey_id} not found") from exc
    journey = db.get(Journey, key)
    if journey is None or journey.org_id != org_id:
        raise NotFoundError(f"Journey {journey_id} not found in this organisation")
    return journey


def _separation(db: Session, org_id, separation_id) -> Separation:
    try:
        key = UUID(str(separation_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Separation {separation_id} not found") from exc
    separation = db.get(Separation, key)
    if separation is None or separation.org_id != org_id:
        raise NotFoundError(f"Separation {separation_id} not found in this organisation")
    return separation


def _require_view(db: Session, actor: User, journey: Journey) -> None:
    if actor.role in _READ_ALL_ROLES:
        return
    if actor.employee_id and journey.employee_id == actor.employee_id:
        return
    if actor.role == Role.MANAGER and actor.employee_id:
        employee = db.get(Employee, journey.employee_id)
        if employee is not None and employee.manager_id == actor.employee_id:
            return
    # Do not leak the existence of other employees' journeys.
    raise NotFoundError("Journey not found")


def ensure_default_templates(db: Session, org_id) -> list[JourneyTemplate]:
    """Create the opinionated default templates once per organisation."""
    existing = {
        template.journey_type
        for template in db.scalars(
            select(JourneyTemplate).where(JourneyTemplate.org_id == org_id)
        ).all()
    }
    created: list[JourneyTemplate] = []
    for journey_type, spec in DEFAULT_TEMPLATES.items():
        if journey_type in existing:
            continue
        template = JourneyTemplate(
            org_id=org_id,
            code=spec["code"],
            name=spec["name"],
            journey_type=journey_type,
            description=spec["description"],
            active=True,
        )
        db.add(template)
        db.flush()
        for order, (name, owner, offset, required) in enumerate(spec["tasks"]):
            db.add(
                JourneyTemplateTask(
                    org_id=org_id,
                    template_id=template.id,
                    name=name,
                    owner_role=owner,
                    offset_days=offset,
                    required=required,
                    sort_order=order,
                )
            )
        created.append(template)
    if created:
        db.flush()
    return created


def list_templates(db: Session, *, org_id, journey_type: str | None = None):
    ensure_default_templates(db, org_id)
    stmt = select(JourneyTemplate).where(
        JourneyTemplate.org_id == org_id, JourneyTemplate.active.is_(True)
    )
    if journey_type:
        stmt = stmt.where(JourneyTemplate.journey_type == journey_type)
    return list(db.scalars(stmt.order_by(JourneyTemplate.name)).all())


def _serialize_task(task: JourneyTask) -> dict:
    return {
        "id": str(task.id),
        "journey_id": str(task.journey_id),
        "name": task.name,
        "description": task.description,
        "owner_role": task.owner_role.value
        if hasattr(task.owner_role, "value")
        else str(task.owner_role),
        "owner_label": task.owner_role.label
        if hasattr(task.owner_role, "label")
        else str(task.owner_role),
        "due_on": task.due_on.isoformat() if task.due_on else None,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "status_label": _TASK_STATUS_LABELS.get(
            task.status.value if hasattr(task.status, "value") else str(task.status),
            str(task.status),
        ),
        "required": bool(task.required),
        "assignee_user_id": str(task.assignee_user_id) if task.assignee_user_id else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "notes": task.notes,
    }


def _serialize_journey(db: Session, journey: Journey, *, with_tasks: bool = True) -> dict:
    employee = db.get(Employee, journey.employee_id)
    tasks = []
    if with_tasks:
        tasks = [
            _serialize_task(task)
            for task in db.scalars(
                select(JourneyTask)
                .where(JourneyTask.journey_id == journey.id)
                .order_by(JourneyTask.sort_order, JourneyTask.due_on)
            ).all()
        ]
    status_value = (
        journey.status.value if hasattr(journey.status, "value") else str(journey.status)
    )
    type_value = (
        journey.journey_type.value
        if hasattr(journey.journey_type, "value")
        else str(journey.journey_type)
    )
    return {
        "id": str(journey.id),
        "employee_id": str(journey.employee_id),
        "employee_name": employee.full_name if employee else None,
        "employee_no": employee.employee_no if employee else None,
        "journey_type": type_value,
        "journey_type_label": JourneyType(type_value).label,
        "status": status_value,
        "status_label": _JOURNEY_STATUS_LABELS.get(status_value, status_value),
        "anchor_date": journey.anchor_date.isoformat(),
        "notes": journey.notes,
        "completed_at": journey.completed_at.isoformat() if journey.completed_at else None,
        "created_at": journey.created_at.isoformat(),
        "tasks": tasks,
        "open_tasks": [task for task in tasks if task["status"] not in {"done", "skipped"}],
        "overdue_tasks": [
            task
            for task in tasks
            if task["status"] not in {"done", "skipped"}
            and task["due_on"]
            and task["due_on"] < date.today().isoformat()
        ],
    }


def start_journey(
    db: Session,
    *,
    org_id,
    actor: User,
    employee_id,
    journey_type: str | JourneyType,
    anchor_date: date,
    template_id=None,
    notes: str | None = None,
) -> dict:
    """Instantiate a journey and copy its template tasks."""
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can start a journey")
    try:
        type_enum = JourneyType(str(journey_type))
    except ValueError as exc:
        raise ValidationError(
            "journey_type must be one of: "
            + ", ".join(item.value for item in JourneyType)
        ) from exc

    employee = _employee(db, org_id, employee_id)
    ensure_default_templates(db, org_id)

    if template_id:
        template = db.get(JourneyTemplate, UUID(str(template_id)))
        if template is None or template.org_id != org_id:
            raise ValidationError("Journey template not found in this organisation")
    else:
        template = db.scalar(
            select(JourneyTemplate).where(
                JourneyTemplate.org_id == org_id,
                JourneyTemplate.journey_type == type_enum,
                JourneyTemplate.active.is_(True),
            )
        )
    if template is None:
        raise ValidationError(f"No active {type_enum.value} template is configured")

    running = db.scalar(
        select(Journey).where(
            Journey.org_id == org_id,
            Journey.employee_id == employee.id,
            Journey.journey_type == type_enum,
            Journey.status.in_([JourneyStatus.DRAFT, JourneyStatus.IN_PROGRESS]),
        )
    )
    if running is not None:
        raise ValidationError(
            f"{type_enum.label} is already in progress for {employee.full_name}"
        )

    journey = Journey(
        org_id=org_id,
        employee_id=employee.id,
        template_id=template.id,
        journey_type=type_enum,
        status=JourneyStatus.IN_PROGRESS,
        anchor_date=anchor_date,
        notes=notes,
        created_by=actor.id,
    )
    db.add(journey)
    db.flush()

    manager_user_id = (
        _user_for_employee(db, employee.manager_id) if employee.manager_id else None
    )
    employee_user_id = _user_for_employee(db, employee.id)
    template_tasks = db.scalars(
        select(JourneyTemplateTask)
        .where(JourneyTemplateTask.template_id == template.id)
        .order_by(JourneyTemplateTask.sort_order)
    ).all()
    for item in template_tasks:
        assignee = None
        if item.owner_role == JourneyTaskOwner.MANAGER:
            assignee = manager_user_id
        elif item.owner_role == JourneyTaskOwner.EMPLOYEE:
            assignee = employee_user_id
        db.add(
            JourneyTask(
                org_id=org_id,
                journey_id=journey.id,
                name=item.name,
                description=item.description,
                owner_role=item.owner_role,
                assignee_user_id=assignee,
                due_on=anchor_date + timedelta(days=item.offset_days),
                status=JourneyTaskStatus.PENDING,
                required=item.required,
                sort_order=item.sort_order,
            )
        )
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="journey",
        entity_id=journey.id,
        details={
            "type": type_enum.value,
            "employee_id": str(employee.id),
            "template": template.code,
        },
        org_id=org_id,
    )
    return _serialize_journey(db, journey)


def list_journeys(
    db: Session,
    *,
    org_id,
    user: User,
    journey_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    stmt = select(Journey).where(Journey.org_id == org_id)
    if user.role in _READ_ALL_ROLES:
        pass
    elif user.role == Role.MANAGER and user.employee_id:
        report_ids = select(Employee.id).where(
            Employee.org_id == org_id, Employee.manager_id == user.employee_id
        )
        stmt = stmt.where(
            or_(
                Journey.employee_id == user.employee_id,
                Journey.employee_id.in_(report_ids),
            )
        )
    elif user.employee_id:
        stmt = stmt.where(Journey.employee_id == user.employee_id)
    else:
        return []
    if journey_type:
        stmt = stmt.where(Journey.journey_type == journey_type)
    if status:
        stmt = stmt.where(Journey.status == status)
    rows = db.scalars(
        stmt.order_by(Journey.anchor_date.desc(), Journey.created_at.desc())
    ).all()
    return [_serialize_journey(db, row) for row in rows]


def get_journey(db: Session, *, org_id, user: User, journey_id) -> dict:
    journey = _journey(db, org_id, journey_id)
    _require_view(db, user, journey)
    return _serialize_journey(db, journey)


def _task(db: Session, journey: Journey, task_id) -> JourneyTask:
    try:
        key = UUID(str(task_id))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(f"Task {task_id} not found") from exc
    task = db.get(JourneyTask, key)
    if task is None or task.journey_id != journey.id:
        raise NotFoundError(f"Task {task_id} not found on this journey")
    return task


def _may_act_on_task(actor: User, journey: Journey, task: JourneyTask) -> bool:
    if _is_hr(actor):
        return True
    if task.assignee_user_id and task.assignee_user_id == actor.id:
        return True
    if task.owner_role == JourneyTaskOwner.EMPLOYEE:
        return bool(actor.employee_id and actor.employee_id == journey.employee_id)
    if task.owner_role == JourneyTaskOwner.MANAGER:
        return actor.role == Role.MANAGER and actor.employee_id is not None
    return False


def _refresh_journey_status(db: Session, journey: Journey) -> None:
    if journey.status != JourneyStatus.IN_PROGRESS:
        return
    remaining = db.scalars(
        select(JourneyTask).where(
            JourneyTask.journey_id == journey.id,
            JourneyTask.required.is_(True),
            JourneyTask.status.notin_([JourneyTaskStatus.DONE, JourneyTaskStatus.SKIPPED]),
        )
    ).all()
    if not remaining:
        journey.status = JourneyStatus.COMPLETED
        journey.completed_at = _utcnow()
        db.add(journey)


def complete_task(
    db: Session,
    *,
    org_id,
    actor: User,
    journey_id,
    task_id,
    notes: str | None = None,
) -> dict:
    journey = _journey(db, org_id, journey_id)
    _require_view(db, actor, journey)
    task = _task(db, journey, task_id)
    if task.status in {JourneyTaskStatus.DONE, JourneyTaskStatus.SKIPPED}:
        raise ValidationError("This task is already closed")
    if not _may_act_on_task(actor, journey, task):
        raise ForbiddenError(
            f"This task belongs to {task.owner_role.label}; ask them to complete it"
        )

    task.status = JourneyTaskStatus.DONE
    task.completed_at = _utcnow()
    task.completed_by = actor.id
    if notes is not None:
        task.notes = notes
    db.add(task)
    db.flush()
    _refresh_journey_status(db, journey)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="journey_task",
        entity_id=task.id,
        details={"status": "done", "journey_id": str(journey.id)},
        org_id=org_id,
    )
    return _serialize_journey(db, journey)


def skip_task(
    db: Session, *, org_id, actor: User, journey_id, task_id, reason: str | None = None
) -> dict:
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can skip a task")
    journey = _journey(db, org_id, journey_id)
    task = _task(db, journey, task_id)
    if task.status in {JourneyTaskStatus.DONE, JourneyTaskStatus.SKIPPED}:
        raise ValidationError("This task is already closed")
    task.status = JourneyTaskStatus.SKIPPED
    task.completed_at = _utcnow()
    task.completed_by = actor.id
    task.notes = reason or task.notes
    db.add(task)
    db.flush()
    _refresh_journey_status(db, journey)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="journey_task",
        entity_id=task.id,
        details={"status": "skipped", "journey_id": str(journey.id)},
        org_id=org_id,
    )
    return _serialize_journey(db, journey)


def complete_journey(
    db: Session, *, org_id, actor: User, journey_id, force: bool = False
) -> dict:
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can close a journey")
    journey = _journey(db, org_id, journey_id)
    if journey.status == JourneyStatus.COMPLETED:
        raise ValidationError("This journey is already completed")
    open_required = db.scalars(
        select(JourneyTask).where(
            JourneyTask.journey_id == journey.id,
            JourneyTask.required.is_(True),
            JourneyTask.status.notin_([JourneyTaskStatus.DONE, JourneyTaskStatus.SKIPPED]),
        )
    ).all()
    if open_required and not force:
        raise ValidationError(
            f"{len(open_required)} required task(s) are still open; complete or skip them first"
        )
    journey.status = JourneyStatus.COMPLETED
    journey.completed_at = _utcnow()
    db.add(journey)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="journey",
        entity_id=journey.id,
        details={"status": "completed", "forced": force},
        org_id=org_id,
    )
    return _serialize_journey(db, journey)


# ---------------------------------------------------------------------------
# Offboarding: separation cases
# ---------------------------------------------------------------------------


def _serialize_separation(db: Session, separation: Separation) -> dict:
    employee = db.get(Employee, separation.employee_id)
    status_value = (
        separation.status.value
        if hasattr(separation.status, "value")
        else str(separation.status)
    )
    exit_value = (
        separation.exit_type.value
        if hasattr(separation.exit_type, "value")
        else str(separation.exit_type)
    )
    journey = db.get(Journey, separation.journey_id) if separation.journey_id else None
    return {
        "id": str(separation.id),
        "employee_id": str(separation.employee_id),
        "employee_name": employee.full_name if employee else None,
        "employee_no": employee.employee_no if employee else None,
        "exit_type": exit_value,
        "exit_type_label": ExitType(exit_value).label,
        "reason_code": separation.reason_code,
        "status": status_value,
        "status_label": _SEPARATION_STATUS_LABELS.get(status_value, status_value),
        "notice_date": separation.notice_date.isoformat()
        if separation.notice_date
        else None,
        "last_working_day": separation.last_working_day.isoformat(),
        "rehire_eligible": bool(separation.rehire_eligible),
        "exit_interview_done": bool(separation.exit_interview_done),
        "final_pay_ready": bool(separation.final_pay_ready),
        "journey_id": str(separation.journey_id) if separation.journey_id else None,
        "journey_status": journey.status.value if journey else None,
        "notes": separation.notes,
        "created_at": separation.created_at.isoformat(),
    }


def create_separation(
    db: Session,
    *,
    org_id,
    actor: User,
    employee_id,
    exit_type: str | ExitType,
    last_working_day: date,
    notice_date: date | None = None,
    reason_code: str | None = None,
    rehire_eligible: bool = True,
    notes: str | None = None,
) -> dict:
    """Open an offboarding case and start its clearance journey."""
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can start offboarding")
    try:
        exit_enum = ExitType(str(exit_type))
    except ValueError as exc:
        raise ValidationError(
            "exit_type must be one of: " + ", ".join(item.value for item in ExitType)
        ) from exc

    employee = _employee(db, org_id, employee_id)
    existing = db.scalar(
        select(Separation).where(
            Separation.org_id == org_id,
            Separation.employee_id == employee.id,
            Separation.status.notin_([SeparationStatus.EXITED, SeparationStatus.WITHDRAWN]),
        )
    )
    if existing is not None:
        raise ValidationError(f"{employee.full_name} already has an open separation")
    if last_working_day < date.today():
        raise ValidationError("Last working day cannot be in the past")

    separation = Separation(
        org_id=org_id,
        employee_id=employee.id,
        exit_type=exit_enum,
        reason_code=reason_code,
        status=SeparationStatus.NOTICE,
        notice_date=notice_date or date.today(),
        last_working_day=last_working_day,
        rehire_eligible=bool(rehire_eligible),
        notes=notes,
        created_by=actor.id,
    )
    db.add(separation)
    db.flush()

    journey = start_journey(
        db,
        org_id=org_id,
        actor=actor,
        employee_id=employee.id,
        journey_type=JourneyType.OFFBOARDING,
        anchor_date=last_working_day,
        notes=f"Offboarding: {exit_enum.label}",
    )
    separation.journey_id = UUID(journey["id"])
    employee.status = EmployeeStatus.NOTICE
    db.add(separation)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="create",
        entity_type="separation",
        entity_id=separation.id,
        details={
            "employee_id": str(employee.id),
            "exit_type": exit_enum.value,
            "last_working_day": last_working_day.isoformat(),
        },
        org_id=org_id,
    )
    return _serialize_separation(db, separation)


def list_separations(
    db: Session, *, org_id, user: User, status: str | None = None
) -> list[dict]:
    stmt = select(Separation).where(Separation.org_id == org_id)
    if user.role not in _READ_ALL_ROLES:
        if user.role == Role.MANAGER and user.employee_id:
            report_ids = select(Employee.id).where(
                Employee.org_id == org_id, Employee.manager_id == user.employee_id
            )
            stmt = stmt.where(
                or_(
                    Separation.employee_id == user.employee_id,
                    Separation.employee_id.in_(report_ids),
                )
            )
        elif user.employee_id:
            stmt = stmt.where(Separation.employee_id == user.employee_id)
        else:
            return []
    if status:
        stmt = stmt.where(Separation.status == status)
    rows = db.scalars(
        stmt.order_by(Separation.last_working_day, Separation.created_at.desc())
    ).all()
    return [_serialize_separation(db, row) for row in rows]


def get_separation(db: Session, *, org_id, user: User, separation_id) -> dict:
    separation = _separation(db, org_id, separation_id)
    if user.role not in _READ_ALL_ROLES:
        if not (
            user.employee_id
            and separation.employee_id == user.employee_id
        ) and not (
            user.role == Role.MANAGER
            and user.employee_id
            and (employee := db.get(Employee, separation.employee_id))
            and employee.manager_id == user.employee_id
        ):
            raise NotFoundError("Separation not found")
    return _serialize_separation(db, separation)


_NEXT_STATUS = {
    SeparationStatus.INITIATED: SeparationStatus.NOTICE,
    SeparationStatus.NOTICE: SeparationStatus.CLEARANCE,
    SeparationStatus.CLEARANCE: SeparationStatus.FINAL_PAY,
    SeparationStatus.FINAL_PAY: SeparationStatus.EXITED,
}


def advance_separation(
    db: Session,
    *,
    org_id,
    actor: User,
    separation_id,
    status: str | SeparationStatus | None = None,
    final_pay_ready: bool | None = None,
    exit_interview_done: bool | None = None,
    rehire_eligible: bool | None = None,
) -> dict:
    """Move a separation through notice → clearance → final pay → exited."""
    if not _is_hr(actor):
        raise ForbiddenError("Only HR or administrators can progress a separation")
    separation = _separation(db, org_id, separation_id)
    employee = db.get(Employee, separation.employee_id)
    if employee is None:
        raise NotFoundError("Employee not found")

    if status is not None:
        try:
            target = SeparationStatus(str(status))
        except ValueError as exc:
            raise ValidationError(
                "status must be one of: "
                + ", ".join(item.value for item in SeparationStatus)
            ) from exc
        if target == SeparationStatus.WITHDRAWN:
            if separation.status == SeparationStatus.EXITED:
                raise ValidationError("An exited separation cannot be withdrawn")
        elif target != separation.status:
            if _NEXT_STATUS.get(separation.status) != target:
                raise ValidationError(
                    f"Cannot move a separation from {separation.status.value} to {target.value}"
                )
        separation.status = target

    if final_pay_ready is not None:
        separation.final_pay_ready = bool(final_pay_ready)
    if exit_interview_done is not None:
        separation.exit_interview_done = bool(exit_interview_done)
    if rehire_eligible is not None:
        separation.rehire_eligible = bool(rehire_eligible)

    if separation.status == SeparationStatus.EXITED:
        if not separation.final_pay_ready:
            raise ValidationError("Mark final pay ready before exiting the employee")
        employee.status = EmployeeStatus.EXITED
        if separation.journey_id:
            journey = db.get(Journey, separation.journey_id)
            if journey is not None and journey.status != JourneyStatus.COMPLETED:
                journey.status = JourneyStatus.COMPLETED
                journey.completed_at = _utcnow()
                db.add(journey)

    db.add(separation)
    db.add(employee)
    db.flush()

    audit.record(
        db,
        actor=actor,
        action="update",
        entity_type="separation",
        entity_id=separation.id,
        details={"status": separation.status.value},
        org_id=org_id,
    )
    return _serialize_separation(db, separation)


# Labels used by the web layer.
JOURNEY_STATUS_LABELS = _JOURNEY_STATUS_LABELS
TASK_STATUS_LABELS = _TASK_STATUS_LABELS
SEPARATION_STATUS_LABELS = _SEPARATION_STATUS_LABELS

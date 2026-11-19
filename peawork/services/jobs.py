"""Database-backed job queue (R2).

Design ("PostgreSQL queue style"): jobs live in the ``jobs`` table, are
enqueued with a caller-supplied ``idempotency_key`` (enqueueing the same key
twice returns the existing job instead of duplicating work), and are drained
by ``run_pending_jobs`` — invoked from the ``peawork worker`` CLI command or
``scripts/`` cron entry, never implicitly inside a web request.

Handlers MUST be idempotent; ``leave_accrual`` additionally dedupes via the
``leave_accrual_runs`` table so a retry never double-credits balances.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import AuditAction, JobStatus, Role
from ..models import (
    Employee,
    EmployeeStatus,
    Job,
    LeaveAccrualRun,
    LeaveBalance,
    LeaveType,
    User,
)
from . import audit
from .attendance import reconcile_attendance
from .errors import ForbiddenError, NotFoundError, ValidationError

JOB_LEAVE_ACCRUAL = "leave_accrual"
JOB_ATTENDANCE_RECONCILIATION = "attendance_reconciliation"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    *,
    org_id: UUID,
    actor: User | None,
    name: str,
    payload: dict,
    idempotency_key: str,
    run_at: datetime | None = None,
    max_attempts: int = 3,
) -> Job:
    key = (idempotency_key or "").strip()
    if not key:
        raise ValidationError("idempotency_key is required")
    if len(key) > 200:
        raise ValidationError("idempotency_key too long (max 200 chars)")

    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing is not None:
        return existing

    job = Job(
        org_id=org_id,
        name=name,
        idempotency_key=key,
        payload=payload or {},
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        run_at=run_at or _utcnow(),
    )
    db.add(job)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="job",
        entity_id=job.id,
        details={"job_name": name, "idempotency_key": key},
        org_id=org_id,
    )
    return job


def list_jobs(
    db: Session,
    *,
    org_id: UUID | None,
    user: User,
    status: JobStatus | None = None,
    limit: int = 100,
) -> list[Job]:
    if user.role not in {Role.ADMIN, Role.SUPER_ADMIN, Role.HR}:
        raise ForbiddenError("Only admin/hr may inspect jobs")
    stmt = select(Job)
    if org_id is not None:
        stmt = stmt.where(Job.org_id == org_id)
    stmt = stmt.order_by(Job.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return list(db.scalars(stmt))


def retry_dead_letter(
    db: Session, *, org_id: UUID, actor: User, job_id: UUID
) -> Job:
    if actor.role not in {Role.ADMIN, Role.SUPER_ADMIN, Role.HR}:
        raise ForbiddenError("Only admin/hr may retry jobs")
    job = db.get(Job, job_id)
    if not job or job.org_id != org_id:
        raise NotFoundError("Job not found")
    if job.status != JobStatus.DEAD_LETTER:
        raise ValidationError("Only dead-letter jobs can be retried")
    job.status = JobStatus.QUEUED
    job.attempts = 0
    job.run_at = _utcnow()
    job.last_error = None
    db.add(job)
    db.flush()
    audit.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="job",
        entity_id=job.id,
        details={"job_name": job.name, "retry": True},
        org_id=org_id,
    )
    return job


def _handle_leave_accrual(db: Session, job: Job) -> dict:
    payload = job.payload or {}
    period = str(payload.get("period") or "").strip()
    if not period or len(period) != 7:
        raise ValidationError("leave_accrual payload requires period YYYY-MM")
    try:
        year, month = int(period[:4]), int(period[5:])
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        raise ValidationError(f"Invalid accrual period: {period!r}") from None

    leave_type_id = UUID(str(payload.get("leave_type_id") or ""))
    leave_type = db.get(LeaveType, leave_type_id)
    if not leave_type or leave_type.org_id != job.org_id:
        raise NotFoundError("Leave type not found for accrual")

    amount_raw = payload.get("amount_per_employee")
    amount = (
        Decimal(str(amount_raw))
        if amount_raw is not None
        else (Decimal(str(leave_type.annual_entitlement)) / Decimal(12)).quantize(Decimal("0.01"))
    )

    # Exactly-once: the unique (org_id, period, leave_type_id) marker wins.
    existing_run = db.scalar(
        select(LeaveAccrualRun).where(
            LeaveAccrualRun.org_id == job.org_id,
            LeaveAccrualRun.period == period,
            LeaveAccrualRun.leave_type_id == leave_type_id,
        )
    )
    if existing_run is not None:
        return {"period": period, "skipped": True, "employees_updated": 0}

    employees = db.scalars(
        select(Employee).where(
            Employee.org_id == job.org_id,
            Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.PROBATION, EmployeeStatus.ON_LEAVE, EmployeeStatus.NOTICE]),
        )
    ).all()

    updated = 0
    for employee in employees:
        balance = db.scalar(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == employee.id,
                LeaveBalance.leave_type_id == leave_type_id,
                LeaveBalance.year == year,
            )
        )
        if balance is None:
            balance = LeaveBalance(
                org_id=job.org_id,
                employee_id=employee.id,
                leave_type_id=leave_type_id,
                year=year,
                opening=Decimal("0"),
                accrued=Decimal("0"),
                used=Decimal("0"),
            )
            db.add(balance)
        balance.accrued = (balance.accrued or Decimal("0")) + amount
        db.add(balance)
        updated += 1

    db.add(LeaveAccrualRun(
        org_id=job.org_id,
        period=period,
        leave_type_id=leave_type_id,
        amount=amount,
        employees_updated=updated,
    ))
    db.flush()
    return {"period": period, "skipped": False, "employees_updated": updated, "amount": str(amount)}


def _handle_attendance_reconciliation(db: Session, job: Job) -> dict:
    payload = job.payload or {}
    on_date = date.fromisoformat(str(payload.get("on_date") or "").strip())
    result = reconcile_attendance(db, org_id=job.org_id, actor=None, on_date=on_date)
    return result


def _handle_review_capture_window(db: Session, job: Job) -> dict:
    """Capture any check‑ins submitted in the window after cycle close.

    For R4 this is a no‑op (check‑in model not yet added); the job is still
    enqueued so the infrastructure is verified and visible in the job console.
    """
    payload = job.payload or {}
    cycle_id = payload.get("cycle_id")
    # TODO(R5): create ReviewCheckIn rows for records in the window.
    return {"cycle_id": str(cycle_id), "captured": 0, "note": "no check-in model yet"}


_HANDLERS = {
    JOB_LEAVE_ACCRUAL: _handle_leave_accrual,
    JOB_ATTENDANCE_RECONCILIATION: _handle_attendance_reconciliation,
    "review_capture_window": _handle_review_capture_window,
}


def run_pending_jobs(db: Session, *, limit: int = 10, org_id: UUID | None = None) -> dict:
    """Claim and run due jobs; retries with backoff, then dead-letters."""
    now = _utcnow()
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.run_at <= now)
        .order_by(Job.run_at)
        .limit(limit)
    )
    if org_id is not None:
        stmt = stmt.where(Job.org_id == org_id)
    jobs = list(db.scalars(stmt))

    succeeded = failed = dead = 0
    for job in jobs:
        job.status = JobStatus.RUNNING
        job.attempts += 1
        db.add(job)
        db.flush()
        try:
            handler = _HANDLERS.get(job.name)
            if handler is None:
                raise ValidationError(f"Unknown job type: {job.name}")
            handler(db, job)
        except Exception as exc:  # noqa: BLE001 - job failure path must not lose the job
            job.last_error = str(exc)[:2000]
            if job.attempts < job.max_attempts:
                job.status = JobStatus.QUEUED
                job.run_at = _utcnow() + timedelta(minutes=2 ** (job.attempts - 1))
                failed += 1
            else:
                job.status = JobStatus.DEAD_LETTER
                job.finished_at = _utcnow()
                dead += 1
        else:
            job.status = JobStatus.SUCCEEDED
            job.finished_at = _utcnow()
            job.last_error = None
            succeeded += 1
        db.add(job)
    db.flush()
    audit.record(
        db,
        actor=None,
        action=AuditAction.UPDATE,
        entity_type="job_run",
        details={"claimed": len(jobs), "succeeded": succeeded, "failed": failed, "dead_lettered": dead},
        org_id=org_id,
    )
    return {"claimed": len(jobs), "succeeded": succeeded, "failed": failed, "dead_lettered": dead}

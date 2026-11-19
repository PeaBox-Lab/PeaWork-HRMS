from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .domain import (
    AttendanceStatus,
    AuditAction,
    BankAccountType,
    BenefitType,
    ChangeRequestType,
    ChangeRequestStatus,
    DependentRelationship,
    BenefitCoverageTier,
    DocumentCategory,
    EmployeeStatus,
    EmploymentType,
    ExitType,
    FeedbackRelationship,
    FeedbackStatus,
    JourneyStatus,
    JourneyTaskOwner,
    JourneyTaskStatus,
    JourneyType,
    JobChangeEvent,
    LeaveStatus,
    NotificationKind,
    PayComponentType,
    PayrollFrequency,
    PayrollStatus,
    Role,
    SeparationStatus,
    StatutoryRuleType,
    TaxRegime,
    TimesheetStatus,
    JobStatus,
    CheckInStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_col(enum_cls: type) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda x: [e.value for e in x],
        validate_strings=True,
    )


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


def bigint_pk() -> Mapped[int]:
    # SQLite only autoincrements a column declared exactly INTEGER PRIMARY KEY.
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    logo_key: Mapped[str | None] = mapped_column(String(255))

    departments: Mapped[list["Department"]] = relationship(back_populates="organization")
    employees: Mapped[list["Employee"]] = relationship(back_populates="organization")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_role_active", "role", "active"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(enum_col(Role), default=Role.EMPLOYEE, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN
    is_sample: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_backup_codes: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON-encoded list
    employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), index=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped["Employee | None"] = relationship(foreign_keys=[employee_id], lazy="selectin")
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions", lazy="joined")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_lookup", "email", "ip", "created_at"),)

    id: Mapped[int] = bigint_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Department(TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_departments_org_code"),
        Index("ix_departments_org_active", "org_id", "active"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    manager_employee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    cost_center: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_sample: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="departments")
    parent: Mapped["Department | None"] = relationship(remote_side=[id], lazy="selectin")
    employees: Mapped[list["Employee"]] = relationship(
        back_populates="department", foreign_keys="Employee.department_id"
    )


class Position(TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_positions_org_code"),
        Index("ix_positions_org_active", "org_id", "active"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    grade: Mapped[str | None] = mapped_column(String(24))
    level: Mapped[str | None] = mapped_column(String(24))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_sample: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    department: Mapped[Department | None] = relationship(lazy="selectin")


class Employee(TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("org_id", "employee_no", name="uq_employees_org_employee_no"),
        UniqueConstraint("org_id", "work_email", name="uq_employees_org_work_email"),
        Index("ix_employees_org_status", "org_id", "status"),
        Index("ix_employees_org_department", "org_id", "department_id"),
        Index("ix_employees_manager", "manager_id"),
        Index("ix_employees_org_sample", "org_id", "is_sample"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_no: Mapped[str] = mapped_column(String(32), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    work_email: Mapped[str | None] = mapped_column(String(255))
    personal_email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    date_of_joining: Mapped[date | None] = mapped_column(Date)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(24))
    employment_type: Mapped[EmploymentType] = mapped_column(
        enum_col(EmploymentType), default=EmploymentType.FULL_TIME, nullable=False
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        enum_col(EmployeeStatus), default=EmployeeStatus.ACTIVE, nullable=False
    )
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    position_id: Mapped[UUID | None] = mapped_column(ForeignKey("positions.id", ondelete="SET NULL"))
    manager_id: Mapped[UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    location: Mapped[str | None] = mapped_column(String(120), default="Gurugram")
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="SET NULL"), index=True
    )
    country_code: Mapped[str | None] = mapped_column(String(2))
    pan_masked: Mapped[str | None] = mapped_column(String(32))
    bank_masked: Mapped[str | None] = mapped_column(String(32))
    is_sample: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="employees")
    department: Mapped[Department | None] = relationship(
        back_populates="employees", foreign_keys=[department_id], lazy="selectin"
    )
    position: Mapped[Position | None] = relationship(lazy="selectin")
    manager: Mapped["Employee | None"] = relationship(
        remote_side=[id], foreign_keys=[manager_id], lazy="selectin"
    )
    reports: Mapped[list["Employee"]] = relationship(
        back_populates="manager", foreign_keys=[manager_id]
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", passive_deletes=True
    )
    separation: Mapped["Separation | None"] = relationship(
        back_populates="employee", lazy="selectin", uselist=False
    )
    journeys: Mapped[list["Journey"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class EmployeeHistory(Base):
    __tablename__ = "employee_history"
    __table_args__ = (Index("ix_employee_history_employee_date", "employee_id", "effective_date"),)

    id: Mapped[UUID] = uuid_pk()
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    change_type: Mapped[str] = mapped_column(String(48), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChangeRequest(Base):
    """
    Effective-dated employee lifecycle change request.
    
    Examples: resignation, promotion, manager change, location change,
    employment-type change, contract change, rehire, group-company transfer,
    country transfer, legal-entity change, project assignment, cost-center change.
    
    Workflow: draft → pending_approval → approved/rejected/cancelled.
    On approval, the change is applied to the employee record (effective_date) and
    an EmployeeHistory row is created with a matching change_type.
    """

    __tablename__ = "change_requests"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_change_requests_org_code"),
        Index("ix_change_requests_org_status", "org_id", "status"),
        Index("ix_change_requests_employee", "employee_id"),
        Index("ix_change_requests_org_type", "org_id", "change_type"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True
    )
    change_type: Mapped[ChangeRequestType] = mapped_column(
        enum_col(ChangeRequestType), nullable=False
    )
    event: Mapped[JobChangeEvent | None] = mapped_column(
        enum_col(JobChangeEvent), nullable=True
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    proposed_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approval_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ChangeRequestStatus] = mapped_column(
        enum_col(ChangeRequestStatus), default=ChangeRequestStatus.DRAFT, nullable=False
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    employee: Mapped[Employee] = relationship(lazy="selectin")



# ============================================================
# Lifecycle journey and separation models
# ============================================================
# Enums live in peawork.domain and are imported above; do not redefine them
# here, or the ORM enum and the service-layer enum drift apart.


class Journey(TimestampMixin, Base):
    """A lifecycle journey (onboarding, offboarding, crossboarding, probation)."""

    __tablename__ = "journeys"
    __table_args__ = (
        Index("ix_journeys_org_employee", "org_id", "employee_id"),
        Index("ix_journeys_org_type_status", "org_id", "journey_type", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("journey_templates.id", ondelete="SET NULL"), nullable=True
    )
    journey_type: Mapped[JourneyType] = mapped_column(enum_col(JourneyType), nullable=False)
    status: Mapped[JourneyStatus] = mapped_column(
        enum_col(JourneyStatus), default=JourneyStatus.DRAFT, nullable=False
    )
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped["Employee"] = relationship(back_populates="journeys", lazy="selectin")
    template: Mapped["JourneyTemplate | None"] = relationship(
        back_populates="journeys", lazy="selectin"
    )
    tasks: Mapped[list["JourneyTask"]] = relationship(
        back_populates="journey", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Journey {self.id} for {self.employee_id} ({self.journey_type.value})>"


class JourneyTask(TimestampMixin, Base):
    """An individual task within a journey."""

    __tablename__ = "journey_tasks"
    __table_args__ = (
        Index("ix_journey_tasks_journey", "journey_id", "sort_order"),
        Index("ix_journey_tasks_org_status", "org_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    journey_id: Mapped[UUID] = mapped_column(
        ForeignKey("journeys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_role: Mapped[JourneyTaskOwner] = mapped_column(
        enum_col(JourneyTaskOwner), nullable=False
    )
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[JourneyTaskStatus] = mapped_column(
        enum_col(JourneyTaskStatus), default=JourneyTaskStatus.PENDING, nullable=False
    )
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    journey: Mapped["Journey"] = relationship(back_populates="tasks", lazy="joined")

    def __repr__(self) -> str:
        return f"<JourneyTask {self.id} for {self.journey_id} ({self.name})>"


class JourneyTemplate(TimestampMixin, Base):
    """A reusable definition used to create journeys."""

    __tablename__ = "journey_templates"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_journey_templates_org_code"),
        Index("ix_journey_templates_org_type", "org_id", "journey_type"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    journey_type: Mapped[JourneyType] = mapped_column(enum_col(JourneyType), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    journeys: Mapped[list["Journey"]] = relationship(
        back_populates="template", lazy="selectin"
    )
    tasks: Mapped[list["JourneyTemplateTask"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<JourneyTemplate {self.id} ({self.code})>"


class JourneyTemplateTask(TimestampMixin, Base):
    """A task definition inside a journey template."""

    __tablename__ = "journey_template_tasks"
    __table_args__ = (
        Index("ix_journey_template_tasks_template", "template_id", "sort_order"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("journey_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_role: Mapped[JourneyTaskOwner] = mapped_column(
        enum_col(JourneyTaskOwner), nullable=False
    )
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    template: Mapped["JourneyTemplate"] = relationship(back_populates="tasks")

    @property
    def journey_type(self) -> JourneyType:
        return self.template.journey_type

    def __repr__(self) -> str:
        return f"<JourneyTemplateTask {self.id} for {self.template_id} ({self.name})>"


class Separation(TimestampMixin, Base):
    """An offboarding case for an employee."""

    __tablename__ = "separations"
    __table_args__ = (
        UniqueConstraint("org_id", "employee_id", name="uq_separations_org_employee"),
        Index("ix_separations_org_status", "org_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exit_type: Mapped[ExitType] = mapped_column(enum_col(ExitType), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[SeparationStatus] = mapped_column(
        enum_col(SeparationStatus), default=SeparationStatus.INITIATED, nullable=False
    )
    notice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_working_day: Mapped[date] = mapped_column(Date, nullable=False)
    rehire_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exit_interview_done: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    final_pay_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("journeys.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    employee: Mapped["Employee"] = relationship(
        back_populates="separation", lazy="selectin", uselist=False
    )
    journey: Mapped["Journey | None"] = relationship(lazy="selectin")

    def __repr__(self) -> str:
        return f"<Separation {self.id} for {self.employee_id} ({self.exit_type.value})>"



class LeaveType(TimestampMixin, Base):
    __tablename__ = "leave_types"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_leave_types_org_code"),
        Index("ix_leave_types_org_active", "org_id", "active"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    annual_entitlement: Mapped[Decimal] = mapped_column(
        Numeric(6, 1), default=Decimal("0"), nullable=False
    )
    carry_forward_limit: Mapped[Decimal] = mapped_column(
        Numeric(6, 1), default=Decimal("0"), nullable=False
    )
    paid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LeaveBalance(TimestampMixin, Base):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "leave_type_id", "year", name="uq_leave_balances_employee_type_year"
        ),
        Index("ix_leave_balances_org_year", "org_id", "year"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    opening: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0"), nullable=False)
    accrued: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0"), nullable=False)
    used: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0"), nullable=False)

    leave_type: Mapped[LeaveType] = relationship(lazy="selectin")

    @property
    def available(self) -> Decimal:
        return (self.opening or Decimal("0")) + (self.accrued or Decimal("0")) - (self.used or Decimal("0"))


class LeaveRequest(TimestampMixin, Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("ix_leave_requests_org_status", "org_id", "status"),
        Index("ix_leave_requests_employee_dates", "employee_id", "start_date", "end_date"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leave_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[Decimal] = mapped_column(Numeric(6, 1), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LeaveStatus] = mapped_column(
        enum_col(LeaveStatus), default=LeaveStatus.PENDING, nullable=False
    )
    approver_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approver_comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped[Employee] = relationship(lazy="selectin")
    leave_type: Mapped[LeaveType] = relationship(lazy="selectin")
    approver: Mapped[User | None] = relationship(lazy="selectin")


class Holiday(TimestampMixin, Base):
    __tablename__ = "holidays"
    __table_args__ = (
        UniqueConstraint("org_id", "holiday_date", name="uq_holidays_org_date"),
        Index("ix_holidays_org_date", "org_id", "holiday_date"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str | None] = mapped_column(String(120), default="All")
    optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Shift(TimestampMixin, Base):
    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_shifts_org_code"),)

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    minimum_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("8"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AttendanceRecord(TimestampMixin, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("employee_id", "attendance_date", name="uq_attendance_employee_date"),
        Index("ix_attendance_org_date", "org_id", "attendance_date"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_id: Mapped[UUID | None] = mapped_column(ForeignKey("shifts.id", ondelete="SET NULL"))
    check_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(
        enum_col(AttendanceStatus), default=AttendanceStatus.PRESENT, nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship(lazy="selectin")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_org_employee", "org_id", "employee_id"),)

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[DocumentCategory] = mapped_column(
        enum_col(DocumentCategory), default=DocumentCategory.OTHER, nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    uploaded_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)

    employee: Mapped[Employee | None] = relationship(back_populates="documents", lazy="selectin")
    acknowledgements: Mapped[list["DocumentAcknowledgement"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentAcknowledgement(Base):
    __tablename__ = "document_acknowledgements"
    __table_args__ = (
        UniqueConstraint("document_id", "acknowledger_id", name="uq_document_acknowledgements_doc_user"),
        Index("ix_document_acknowledgements_org_created", "org_id", "acknowledged_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    acknowledger_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True
    )
    acknowledger_employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="acknowledgements", lazy="selectin")
    acknowledger: Mapped["User | None"] = relationship(foreign_keys=[acknowledger_id], lazy="selectin")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "org_id", "created_at"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = bigint_pk()
    org_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    actor_email: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    action: Mapped[AuditAction] = mapped_column(enum_col(AuditAction), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        enum_col(NotificationKind), default=NotificationKind.INFO, nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(255))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PayComponent(TimestampMixin, Base):
    """Org-level library of salary components (Basic, HRA, PF, ...)."""

    __tablename__ = "pay_components"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_pay_components_org_code"),
        Index("ix_pay_components_org_type", "org_id", "component_type"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    component_type: Mapped[PayComponentType] = mapped_column(
        enum_col(PayComponentType), nullable=False
    )
    is_taxable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_statutory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Compensation(TimestampMixin, Base):
    """Current compensation summary for an employee."""

    __tablename__ = "compensations"
    __table_args__ = (
        UniqueConstraint("employee_id", name="uq_compensations_employee"),
        Index("ix_compensations_org_currency", "org_id", "currency"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    payroll_frequency: Mapped[PayrollFrequency] = mapped_column(
        enum_col(PayrollFrequency), default=PayrollFrequency.MONTHLY, nullable=False
    )
    tax_regime: Mapped[TaxRegime] = mapped_column(
        enum_col(TaxRegime), default=TaxRegime.INDIA_NEW, nullable=False
    )
    pay_scale: Mapped[str | None] = mapped_column(String(40))
    grade: Mapped[str | None] = mapped_column(String(24))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship(lazy="selectin")


class CompensationComponent(Base):
    """Component-level breakdown of a compensation record."""

    __tablename__ = "compensation_components"
    __table_args__ = (
        UniqueConstraint("compensation_id", "component_id", name="uq_comp_component_pair"),
    )

    id: Mapped[UUID] = uuid_pk()
    compensation_id: Mapped[UUID] = mapped_column(
        ForeignKey("compensations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_id: Mapped[UUID] = mapped_column(
        ForeignKey("pay_components.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)

    component: Mapped[PayComponent] = relationship(lazy="selectin")


class PayrollRun(TimestampMixin, Base):
    """A batch payroll run for a pay period."""

    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("org_id", "period_start", "period_end", name="uq_payroll_runs_period"),
        Index("ix_payroll_runs_org_status", "org_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date)
    frequency: Mapped[PayrollFrequency] = mapped_column(
        enum_col(PayrollFrequency), default=PayrollFrequency.MONTHLY, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    status: Mapped[PayrollStatus] = mapped_column(
        enum_col(PayrollStatus), default=PayrollStatus.DRAFT, nullable=False
    )
    total_gross: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    total_net: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StatutoryRuleVersion(TimestampMixin, Base):
    """Statutory rule version with effective dates and validation gates."""

    __tablename__ = "statutory_rule_versions"
    __table_args__ = (
        UniqueConstraint("org_id", "rule_type", "country_code", "state_code", "version", name="uq_statutory_rule_version"),
        Index("ix_statutory_rule_org_type_country", "org_id", "rule_type", "country_code", "state_code"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_type: Mapped[StatutoryRuleType] = mapped_column(
        enum_col(StatutoryRuleType), nullable=False
    )
    country_code: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)
    state_code: Mapped[str | None] = mapped_column(String(2))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rates: Mapped[dict] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Payslip(TimestampMixin, Base):
    """One employee's result inside a payroll run."""

    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("run_id", "employee_id", name="uq_payslips_run_employee"),
        Index("ix_payslips_org_employee", "org_id", "employee_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gross_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    net_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    bank_account_last4: Mapped[str | None] = mapped_column(String(4))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[PayrollRun] = relationship(lazy="selectin")
    employee: Mapped[Employee] = relationship(lazy="selectin")
    lines: Mapped[list["PayslipLine"]] = relationship(
        back_populates="payslip", cascade="all, delete-orphan", passive_deletes=True
    )


class PayslipLine(Base):
    """A single earning/deduction line on a payslip."""

    __tablename__ = "payslip_lines"
    __table_args__ = (Index("ix_payslip_lines_payslip", "payslip_id"),)

    id: Mapped[UUID] = uuid_pk()
    payslip_id: Mapped[UUID] = mapped_column(
        ForeignKey("payslips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pay_components.id", ondelete="SET NULL")
    )
    component_name: Mapped[str] = mapped_column(String(120), nullable=False)
    component_type: Mapped[PayComponentType] = mapped_column(
        enum_col(PayComponentType), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    unit: Mapped[str | None] = mapped_column(String(24))

    payslip: Mapped[Payslip] = relationship(back_populates="lines", lazy="selectin")


class BankAccount(TimestampMixin, Base):
    """Employee bank account for salary disbursement."""

    __tablename__ = "bank_accounts"
    __table_args__ = (Index("ix_bank_accounts_org_employee", "org_id", "employee_id"),)

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_type: Mapped[BankAccountType] = mapped_column(
        enum_col(BankAccountType), default=BankAccountType.SALARY, nullable=False
    )
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(120))
    account_holder: Mapped[str] = mapped_column(String(160), nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    ifsc: Mapped[str | None] = mapped_column(String(24))
    swift: Mapped[str | None] = mapped_column(String(24))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    employee: Mapped[Employee] = relationship(lazy="selectin")


class BenefitPlan(TimestampMixin, Base):
    """Org-level benefit plan catalog (global or country-specific)."""

    __tablename__ = "benefit_plans"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_benefit_plans_org_code"),
        Index("ix_benefit_plans_org_type", "org_id", "benefit_type"),
        Index("ix_benefit_plans_org_country", "org_id", "country_code"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    benefit_type: Mapped[BenefitType] = mapped_column(
        enum_col(BenefitType), nullable=False
    )
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(120))
    policy_number: Mapped[str | None] = mapped_column(String(64))
    coverage_tier: Mapped[BenefitCoverageTier] = mapped_column(
        enum_col(BenefitCoverageTier),
        default=BenefitCoverageTier.EMPLOYEE_ONLY,
        server_default="employee_only",
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default="INR", nullable=False
    )
    employer_contribution: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    employee_contribution: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    payroll_deduction: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    waiting_period_days: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    eligibility: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EmployeeBenefit(TimestampMixin, Base):
    """An employee's enrollment in a benefit plan."""

    __tablename__ = "employee_benefits"
    __table_args__ = (
        UniqueConstraint("employee_id", "plan_id", name="uq_emp_benefits_employee_plan"),
        Index("ix_employee_benefits_org_status", "org_id", "status"),
        Index("ix_employee_benefits_org_employee", "org_id", "employee_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("benefit_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrolled_on: Mapped[date] = mapped_column(Date, nullable=False)
    effective_on: Mapped[date | None] = mapped_column(Date)
    coverage_tier: Mapped[BenefitCoverageTier] = mapped_column(
        enum_col(BenefitCoverageTier),
        default=BenefitCoverageTier.EMPLOYEE_ONLY,
        server_default="employee_only",
        nullable=False,
    )
    coverage_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    employee_premium: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    employer_premium: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="INR", server_default="INR", nullable=False
    )
    payroll_deduction: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date)

    plan: Mapped[BenefitPlan] = relationship(lazy="selectin")
    dependents: Mapped[list["BenefitDependent"]] = relationship(
        back_populates="enrollment",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class BenefitDependent(TimestampMixin, Base):
    """A family member covered by an employee's benefit enrollment."""

    __tablename__ = "benefit_dependents"
    __table_args__ = ()

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("employee_benefits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    relationship_type: Mapped[DependentRelationship] = mapped_column(
        "relationship", enum_col(DependentRelationship), nullable=False
    )
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    national_id_masked: Mapped[str | None] = mapped_column(String(32))

    enrollment: Mapped[EmployeeBenefit] = relationship(back_populates="dependents")


class LegalEntity(TimestampMixin, Base):
    """Legal entities / subsidiaries the organization operates under."""

    __tablename__ = "legal_entities"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_legal_entities_org_code"),
        Index("ix_legal_entities_org_country", "org_id", "country_code"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WorkSchedule(TimestampMixin, Base):
    """Effective-dated org-level weekly-off pattern.

    ``weekly_off`` holds ISO weekday numbers (1 = Monday ... 7 = Sunday).
    The latest active schedule applicable on a date decides week-off days.
    """

    __tablename__ = "work_schedules"
    __table_args__ = (
        UniqueConstraint("org_id", "code", "effective_from", name="uq_work_schedules_org_code_from"),
        Index("ix_work_schedules_org_effective", "org_id", "effective_from"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    weekly_off: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BiometricDevice(TimestampMixin, Base):
    """HMAC-signed biometric punch source (no user session)."""

    __tablename__ = "biometric_devices"
    __table_args__ = (
        UniqueConstraint("org_id", "device_code", name="uq_biometric_devices_org_code"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    device_code: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str | None] = mapped_column(String(120))
    # Shared HMAC key; used only to verify request signatures, never serialized.
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Timesheet(TimestampMixin, Base):
    __tablename__ = "timesheets"
    __table_args__ = (
        UniqueConstraint("employee_id", "week_start", name="uq_timesheets_employee_week"),
        Index("ix_timesheets_org_status", "org_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[TimesheetStatus] = mapped_column(
        enum_col(TimesheetStatus), default=TimesheetStatus.DRAFT, nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approver_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approver_comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped[Employee] = relationship(lazy="selectin")
    entries: Mapped[list[TimesheetEntry]] = relationship(
        back_populates="timesheet", lazy="selectin", cascade="all, delete-orphan"
    )


class TimesheetEntry(TimestampMixin, Base):
    __tablename__ = "timesheet_entries"

    id: Mapped[UUID] = uuid_pk()
    timesheet_id: Mapped[UUID] = mapped_column(
        ForeignKey("timesheets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    project_code: Mapped[str | None] = mapped_column(String(40))
    activity: Mapped[str] = mapped_column(String(200), nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    timesheet: Mapped[Timesheet] = relationship(back_populates="entries")


class LeaveAccrualRun(TimestampMixin, Base):
    """Idempotency marker for leave accrual jobs (one accrual per period/type)."""

    __tablename__ = "leave_accrual_runs"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "period", "leave_type_id", name="uq_leave_accrual_runs_period_type"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    leave_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    employees_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Job(TimestampMixin, Base):
    """Database-backed job queue (PostgreSQL-queue style; no Redis required).

    Enqueueing is idempotent via ``idempotency_key``; handlers are
    responsible for their own exactly-once side effects.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_status_run_at", "status", "run_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        enum_col(JobStatus), default=JobStatus.QUEUED, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewCycle(TimestampMixin, Base):
    """A review cycle / check-in period.

    ``start_date`` / ``end_date`` define the window.
    ``status`` tracks ``draft`` / ``active`` / ``closed``.
    Employees are assigned to a cycle via ``review_assignments`` (handled
    by the talent service; the model only stores the cycle metadata).
    """

    __tablename__ = "review_cycles"

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft / active / closed
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_review_cycles_org_code"),
        Index("ix_review_cycles_org_start", "org_id", "start_date"),
    )


class Goal(TimestampMixin, Base):
    """An OKR-style goal assigned to an employee (or created org-wide).

    ``objective`` is the team/org level statement; ``key_results`` are the
    weighted measurable outcomes (weight fractions summing to ~1.0).
    Progress is append-only via ``GoalProgress`` rows (never update-in-place).
    """

    __tablename__ = "goals"
    __table_args__ = (
        Index("ix_goals_org_employee", "org_id", "employee_id"),
        Index("ix_goals_org_status", "org_id", "status"),
        UniqueConstraint("org_id", "code", name="uq_goals_org_code"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    review_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("review_cycles.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open / achieved / missed
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    weight: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("1.0"), nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.0"), nullable=False)  # 0.0000-1.0000
    due_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_goals_org_code"),
    )


class GoalProgress(Base):
    """Append-only progress history for a goal.

    Each update is an immutable row: previous ``progress`` snapshots remain
    available (audit trail); ``progress`` is the latest value.
    """

    __tablename__ = "goal_progress"
    __table_args__ = (
        Index("ix_goal_progress_goal", "goal_id"),
        Index("ix_goal_progress_org_created", "org_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)  # 0.0000-1.0000
    note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)



# ---------------------------------------------------------------------------
# New: Skills & Competencies
# ---------------------------------------------------------------------------


class Skill(StrEnum):
    """Organizational skills mapped to proficiency levels."""

    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    SQL = "sql"
    EXCEL = "excel"
    POWERPOINT = "powerpoint"
    PROJECT_MANAGEMENT = "project_management"
    AGILE = "agile"
    SCRUM = "scrum"
    LEADERSHIP = "leadership"
    COMMUNICATION = "communication"
    ANALYSIS = "analysis"
    DATA_ANALYSIS = "data_analysis"
    REPORTING = "reporting"
    DESIGN = "design"
    WRITING = "writing"

    @property
    def label(self) -> str:
        return {
            "python": "Python",
            "java": "Java",
            "javascript": "JavaScript",
            "typescript": "TypeScript",
            "sql": "SQL",
            "excel": "Excel",
            "powerpoint": "PowerPoint",
            "project_management": "Project Management",
            "agile": "Agile",
            "scrum": "Scrum",
            "leadership": "Leadership",
            "communication": "Communication",
            "analysis": "Analysis",
            "data_analysis": "Data Analysis",
            "reporting": "Reporting",
            "design": "Design",
            "writing": "Writing",
        }[self.value]


class ProficiencyLevel(StrEnum):
    """Proficiency levels for skills."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"

    @property
    def label(self) -> str:
        return {
            "beginner": "Beginner",
            "intermediate": "Intermediate",
            "advanced": "Advanced",
            "expert": "Expert",
        }[self.value]


class EmployeeSkill(TimestampMixin, Base):
    """An employee's proficiency in a skill.

    Tied to an employee and a skill, with a proficiency level and optional
    verification (e.g., certification, project, assessment).  One row per
    (employee, skill) pair.
    """

    __tablename__ = "employee_skills"
    __table_args__ = (
        UniqueConstraint("employee_id", "skill", name="uq_employee_skills_emp_skill"),
        Index("ix_employee_skills_org", "org_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill: Mapped[Skill] = mapped_column(enum_col(Skill), nullable=False)
    proficiency: Mapped[ProficiencyLevel] = mapped_column(
        enum_col(ProficiencyLevel), default=ProficiencyLevel.BEGINNER, nullable=False
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_notes: Mapped[str | None] = mapped_column(Text)
    years_experience: Mapped[float | None] = mapped_column(Numeric(5, 2))
    last_assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    assessed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# New: Development Goals
# ---------------------------------------------------------------------------


class DevelopmentGoal(TimestampMixin, Base):
    """A measurable development goal for an employee.

    Linked to a skill gap, with a target proficiency, target date, and actions.
    """

    __tablename__ = "development_goals"
    __table_args__ = (
        Index("ix_development_goals_org", "org_id"),
        Index("ix_development_goals_employee", "employee_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill: Mapped[Skill] = mapped_column(enum_col(Skill), nullable=False)
    current_proficiency: Mapped[ProficiencyLevel] = mapped_column(
        enum_col(ProficiencyLevel), default=ProficiencyLevel.BEGINNER, nullable=False
    )
    target_proficiency: Mapped[ProficiencyLevel] = mapped_column(
        enum_col(ProficiencyLevel), nullable=False
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    rationale: Mapped[str | None] = mapped_column(Text)
    actions: Mapped[str | None] = mapped_column(Text)  # free-text action items
    achieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# New: Calibration
# ---------------------------------------------------------------------------


class CalibrationRound(TimestampMixin, Base):
    """A calibration session where managers converge on ratings."""

    __tablename__ = "calibration_rounds"
    __table_args__ = (
        Index("ix_calibration_rounds_org", "org_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open / in_progress / closed
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class CalibrationEntry(TimestampMixin, Base):
    """One employee's rating entry in a calibration round.

    Links an employee + skill (or the employee's overall rating) with a
    proposed rating and a calibrated (final) rating, plus rationale.
    """

    __tablename__ = "calibration_entries"
    __table_args__ = (
        Index("ix_calibration_entries_round", "round_id"),
        Index("ix_calibration_entries_employee_skill", "employee_id", "skill"),
    )

    id: Mapped[UUID] = uuid_pk()
    round_id: Mapped[UUID] = mapped_column(
        ForeignKey("calibration_rounds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill: Mapped[Skill | None] = mapped_column(enum_col(Skill), nullable=True)
    proposed_rating: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    calibrated_rating: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 1), nullable=True
    )
    rationale_proposed: Mapped[str | None] = mapped_column(Text)
    rationale_calibrated: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending / approved / locked
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


# ---------------------------------------------------------------------------
# Review Check-In (R4 remaining)
# ---------------------------------------------------------------------------


class ReviewCheckIn(TimestampMixin, Base):
    """A check-in submission within a review cycle capture window.

    One row per (employee, cycle) pair, with separate employee and manager
    notes and a single submitted rating.
    """

    __tablename__ = "review_check_ins"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id", "employee_id", name="uq_review_check_ins_cycle_employee"
        ),
        Index("ix_review_check_ins_org_employee", "org_id", "employee_id"),
        Index("ix_review_check_ins_org_status", "org_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_cycles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manager_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CheckInStatus] = mapped_column(
        enum_col(CheckInStatus), default=CheckInStatus.PENDING, nullable=False
    )
    employee_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class FeedbackRequest(TimestampMixin, Base):
    """A 360-feedback request for a subject employee from a reviewer."""

    __tablename__ = "feedback_requests"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "subject_employee_id",
            "reviewer_employee_id",
            name="uq_feedback_requests_cycle_subject_reviewer",
        ),
        Index("ix_feedback_requests_org_reviewer", "org_id", "reviewer_employee_id"),
        Index("ix_feedback_requests_org_status", "org_id", "status"),
        Index("ix_feedback_requests_org_subject", "org_id", "subject_employee_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("review_cycles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject_employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    relationship: Mapped[FeedbackRelationship] = mapped_column(
        enum_col(FeedbackRelationship), nullable=False
    )
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[FeedbackStatus] = mapped_column(
        enum_col(FeedbackStatus), default=FeedbackStatus.PENDING, nullable=False
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    strengths: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvements: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "WorkSchedule",
    "BiometricDevice",
    "Timesheet",
    "TimesheetEntry",
    "LeaveAccrualRun",
    "Job",
    "AttendanceRecord",
    "AuditEvent",
    "AttendanceStatus",
    "Document",
    "DocumentAcknowledgement",
    "Department",
    "DocumentCategory",
    "Employee",
    "EmployeeHistory",
    "EmploymentType",
    "EmployeeStatus",
    "Holiday",
    "LeaveType",
    "LeaveBalance",
    "LeaveRequest",
    "LeaveStatus",
    "Notification",
    "NotificationKind",
    "Organization",
    "PayComponent",
    "Compensation",
    "CompensationComponent",
    "PayrollRun",
    "Payslip",
    "PayslipLine",
    "BankAccount",
    "BenefitPlan",
    "EmployeeBenefit",
    "LegalEntity",
    "StatutoryRuleVersion",
    "Position",
    "Shift",
    "User",
    "Role",
    "AuditAction",
    "PayComponentType",
    "TimesheetStatus",
    "JobStatus",
    "PayrollFrequency",
    "PayrollStatus",
    "ReviewCycle",
    "Goal",
    "GoalProgress",
    "StatutoryRuleType",
    "TaxRegime",
    "BenefitType",
    "BankAccountType",
    "ReviewCheckIn",
    "FeedbackRequest",
    "Skill",
    "ProficiencyLevel",
    "EmployeeSkill",
    "DevelopmentGoal",
    "CalibrationRound",
    "CalibrationEntry",
]
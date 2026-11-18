from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .domain import (
    AttendanceStatus,
    ChangeRequestStatus,
    ChangeRequestType,
    DocumentCategory,
    EmployeeStatus,
    EmploymentType,
    LeaveStatus,
    PayComponentType,
    Role,
    StatutoryRuleType,
)

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class OrganizationOut(ORMModel):
    id: UUID
    name: str
    legal_name: str | None
    country_code: str
    timezone: str
    currency: str


class UserOut(ORMModel):
    id: UUID
    # Output models use plain ``str``: addresses already stored (including
    # internal domains such as ``*.local``) must always serialise, even if a
    # future/stricter e-mail validator would reject them on input.
    email: str
    full_name: str
    role: Role
    active: bool
    employee_id: UUID | None
    last_login_at: datetime | None


class MeOut(UserOut):
    organization: OrganizationOut | None = None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: Role = Role.EMPLOYEE
    employee_id: UUID | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=1, max_length=24)
    parent_id: UUID | None = None
    manager_employee_id: UUID | None = None
    cost_center: str | None = Field(default=None, max_length=64)

    @field_validator("code")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper()


class DepartmentOut(ORMModel):
    id: UUID
    name: str
    code: str
    parent_id: UUID | None
    manager_employee_id: UUID | None
    cost_center: str | None
    active: bool


class PositionCreate(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=1, max_length=24)
    department_id: UUID | None = None
    grade: str | None = Field(default=None, max_length=24)
    level: str | None = Field(default=None, max_length=24)


class PositionOut(ORMModel):
    id: UUID
    title: str
    code: str
    department_id: UUID | None
    grade: str | None
    level: str | None
    active: bool


class EmployeeBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    date_of_joining: date | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=24)
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    department_id: UUID | None = None
    position_id: UUID | None = None
    manager_id: UUID | None = None
    location: str | None = Field(default="Gurugram", max_length=120)
    pan_masked: str | None = Field(default=None, max_length=32)
    bank_masked: str | None = Field(default=None, max_length=32)

    @field_validator("work_email", "personal_email")
    @classmethod
    def lower_email(cls, value: EmailStr | None) -> str | None:
        return str(value).lower() if value else None


class EmployeeCreate(EmployeeBase):
    employee_no: str | None = Field(default=None, max_length=32)


class EmployeeUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    date_of_joining: date | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=24)
    employment_type: EmploymentType | None = None
    status: EmployeeStatus | None = None
    department_id: UUID | None = None
    position_id: UUID | None = None
    manager_id: UUID | None = None
    location: str | None = Field(default=None, max_length=120)
    pan_masked: str | None = Field(default=None, max_length=32)
    bank_masked: str | None = Field(default=None, max_length=32)


class EmployeeBrief(ORMModel):
    id: UUID
    employee_no: str
    first_name: str
    last_name: str


class EmployeeOut(ORMModel):
    id: UUID
    org_id: UUID
    employee_no: str
    first_name: str
    last_name: str
    work_email: str | None
    personal_email: str | None
    phone: str | None
    date_of_joining: date | None
    date_of_birth: date | None
    gender: str | None
    employment_type: EmploymentType
    status: EmployeeStatus
    location: str | None
    department: DepartmentOut | None = None
    position: PositionOut | None = None
    manager: EmployeeBrief | None = None
    created_at: datetime
    updated_at: datetime


class LeaveTypeOut(ORMModel):
    id: UUID
    name: str
    code: str
    annual_entitlement: Decimal
    carry_forward_limit: Decimal
    paid: bool
    active: bool


class LeaveBalanceOut(ORMModel):
    id: UUID
    employee_id: UUID
    leave_type_id: UUID
    year: int
    opening: Decimal
    accrued: Decimal
    used: Decimal
    available: Decimal


class LeaveRequestCreate(BaseModel):
    employee_id: UUID | None = None
    leave_type_id: UUID
    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=1000)
    half_day: bool = False


class LeaveDecision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)


class LeaveRequestOut(ORMModel):
    id: UUID
    employee_id: UUID
    leave_type_id: UUID
    start_date: date
    end_date: date
    days: Decimal
    reason: str | None
    status: LeaveStatus
    approver_comment: str | None
    decided_at: datetime | None
    created_at: datetime
    employee: EmployeeBrief | None = None
    leave_type: LeaveTypeOut | None = None


class DocumentOut(ORMModel):
    id: UUID
    employee_id: UUID | None
    category: DocumentCategory
    title: str
    filename: str
    content_type: str
    size_bytes: int
    expires_on: date | None
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: UUID | None = None


class DocumentAcknowledgementOut(ORMModel):
    id: UUID
    document_id: UUID
    acknowledger_id: UUID
    acknowledger_employee_id: UUID | None
    acknowledged_at: datetime
    created_at: datetime
    ip: str | None = None
    user_agent: str | None = None
    comment: str | None = None


class AuditEventOut(ORMModel):
    id: int
    actor_email: str
    action: str
    entity_type: str
    entity_id: str | None
    details: dict | None
    ip: str | None
    created_at: datetime


class AttendanceOut(ORMModel):
    id: UUID
    employee_id: UUID
    attendance_date: date
    check_in: datetime | None
    check_out: datetime | None
    worked_minutes: int
    overtime_minutes: int
    status: AttendanceStatus
    source: str


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    organization: str | None = None


class PayslipOut(ORMModel):
    id: UUID
    run_id: UUID
    employee_id: UUID
    gross_pay: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    bank_account_last4: str | None
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PayslipLineOut(BaseModel):
    id: UUID
    payslip_id: UUID
    component_id: UUID | None
    component_name: str
    component_type: PayComponentType
    amount: Decimal
    quantity: Decimal | None
    unit: str | None


class CompensationOut(ORMModel):
    id: UUID
    base_salary: Decimal
    currency: str
    payroll_frequency: str
    tax_regime: str
    pay_scale: str | None
    grade: str | None
    effective_from: date
    effective_to: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class CompensationComponentOut(BaseModel):
    id: UUID
    component_name: str
    component_type: PayComponentType
    amount: Decimal
    effective_from: date


class PayrollRunOut(ORMModel):
    id: UUID
    name: str
    period_start: date
    period_end: date
    pay_date: date | None
    frequency: str
    currency: str
    status: str
    total_gross: Decimal
    total_net: Decimal
    notes: str | None
    created_at: datetime
    updated_at: datetime


class BankAccountOut(ORMModel):
    id: UUID
    account_type: str
    bank_name: str
    account_holder: str
    account_number_masked: str
    ifsc: str | None
    swift: str | None
    is_primary: bool
    active: bool
    created_at: datetime
    updated_at: datetime


class BenefitPlanOut(ORMModel):
    id: UUID
    name: str
    code: str
    benefit_type: str
    provider: str | None
    employer_contribution: Decimal
    employee_contribution: Decimal
    active: bool
    created_at: datetime
    updated_at: datetime


class EmployeeBenefitOut(ORMModel):
    id: UUID
    plan: BenefitPlanOut
    enrolled_on: date
    coverage_amount: Decimal | None
    status: str
    ends_on: date | None
    created_at: datetime
    updated_at: datetime


class LegalEntityOut(ORMModel):
    id: UUID
    name: str
    code: str
    country_code: str
    currency: str
    tax_id: str | None
    address: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class StatutoryRuleVersionOut(ORMModel):
    id: UUID
    org_id: UUID
    rule_type: StatutoryRuleType
    country_code: str
    state_code: str | None
    effective_from: date
    effective_to: date | None
    version: int
    rates: dict
    active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class StatutoryRuleVersionCreate(BaseModel):
    rule_type: StatutoryRuleType
    country_code: str = Field(default="IN", max_length=2)
    state_code: str | None = Field(default=None, max_length=2)
    effective_from: date
    effective_to: date | None = None
    version: int = Field(default=1, ge=1)
    rates: dict
    active: bool = True
    notes: str | None = Field(default=None, max_length=2000)


class StatutoryRuleVersionUpdate(BaseModel):
    effective_from: date | None = None
    effective_to: date | None = None
    version: int | None = Field(default=None, ge=1)
    rates: dict | None = None
    active: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ChangeRequestCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    change_type: ChangeRequestType
    employee_id: UUID | None = None
    effective_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    old_value: str | None = Field(default=None, max_length=4000)
    new_value: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("code")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper()


class ChangeRequestOut(ORMModel):
    id: UUID
    org_id: UUID
    code: str
    employee_id: UUID | None
    change_type: ChangeRequestType
    status: ChangeRequestStatus
    effective_date: date
    description: str | None
    old_value: str | None
    new_value: str | None
    notes: str | None
    reason: str | None
    actor_id: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime


class ChangeRequestUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    old_value: str | None = Field(default=None, max_length=4000)
    new_value: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)

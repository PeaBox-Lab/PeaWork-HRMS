from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    HR = "hr"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    AUDITOR = "auditor"
    PAYROLL = "payroll"

    @property
    def label(self) -> str:
        return {
            "admin": "Administrator",
            "hr": "HR",
            "manager": "Manager",
            "employee": "Employee",
            "auditor": "Auditor",
            "payroll": "Payroll",
        }[self.value]


class EmployeeStatus(StrEnum):
    ACTIVE = "active"
    PROBATION = "probation"
    ON_LEAVE = "on_leave"
    NOTICE = "notice"
    EXITED = "exited"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERN = "intern"
    CONSULTANT = "consultant"


class LeaveStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    HALF_DAY = "half_day"
    LEAVE = "leave"
    HOLIDAY = "holiday"
    WEEK_OFF = "week_off"
    REMOTE = "remote"


class TimesheetStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


class DocumentCategory(StrEnum):
    IDENTITY = "identity"
    CONTRACT = "contract"
    EDUCATION = "education"
    PAYROLL = "payroll"
    POLICY = "policy"
    MEDICAL = "medical"
    OTHER = "other"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    APPROVE = "approve"
    REJECT = "reject"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    DOWNLOAD = "download"
    VIEW = "view"


class NotificationKind(StrEnum):
    INFO = "info"
    ACTION = "action"
    APPROVAL = "approval"
    SYSTEM = "system"


class PayrollFrequency(StrEnum):
    MONTHLY = "monthly"
    SEMI_MONTHLY = "semi_monthly"
    BI_WEEKLY = "bi_weekly"
    WEEKLY = "weekly"
    DAILY = "daily"


class PayrollStatus(StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    FINALIZED = "finalized"
    PAID = "paid"
    CANCELLED = "cancelled"


class PayComponentType(StrEnum):
    EARNING = "earning"
    DEDUCTION = "deduction"
    EMPLOYER_CONTRIBUTION = "employer_contribution"
    TAX = "tax"


class TaxRegime(StrEnum):
    INDIA_OLD = "india_old"
    INDIA_NEW = "india_new"
    US_FEDERAL = "us_federal"
    UK_PAYE = "uk_paye"
    GENERIC = "generic"


class BenefitType(StrEnum):
    HEALTH_INSURANCE = "health_insurance"
    LIFE_INSURANCE = "life_insurance"
    RETIREMENT_PLAN = "retirement_plan"
    STOCK_OPTIONS = "stock_options"
    WELLNESS = "wellness"
    TRANSPORT = "transport"
    MEAL = "meal"
    CHILDCARE = "childcare"
    EDUCATION = "education"
    OTHER = "other"


class BankAccountType(StrEnum):
    SAVINGS = "savings"
    CHECKING = "checking"
    CURRENT = "current"
    SALARY = "salary"


class StatutoryRuleType(StrEnum):
    PF = "pf"
    ESI = "esi"
    PT = "pt"
    TDS = "tds"

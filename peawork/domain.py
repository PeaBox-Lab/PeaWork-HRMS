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

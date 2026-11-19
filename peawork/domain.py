from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    HR = "hr"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    AUDITOR = "auditor"
    PAYROLL = "payroll"

    @property
    def label(self) -> str:
        return {
            "admin": "Administrator",
            "super_admin": "Super Administrator",
            "hr": "HR",
            "manager": "Manager",
            "employee": "Employee",
            "auditor": "Auditor",
            "payroll": "Payroll",
        }[self.value]

    @property
    def is_admin_role(self) -> bool:
        """Super administrators are administrators of the platform."""
        return self in {Role.ADMIN, Role.SUPER_ADMIN}


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
    DENTAL = "dental"
    VISION = "vision"
    LIFE_INSURANCE = "life_insurance"
    DISABILITY = "disability"
    CRITICAL_ILLNESS = "critical_illness"
    ACCIDENT = "accident"
    TRAVEL = "travel"
    RETIREMENT_PLAN = "retirement_plan"
    STOCK_OPTIONS = "stock_options"
    WELLNESS = "wellness"
    TRANSPORT = "transport"
    MEAL = "meal"
    CHILDCARE = "childcare"
    EDUCATION = "education"
    OTHER = "other"


class BenefitCoverageTier(StrEnum):
    EMPLOYEE_ONLY = "employee_only"
    EMPLOYEE_SPOUSE = "employee_spouse"
    EMPLOYEE_CHILDREN = "employee_children"
    FAMILY = "family"
    CUSTOM = "custom"


class DependentRelationship(StrEnum):
    SPOUSE = "spouse"
    PARTNER = "partner"
    CHILD = "child"
    PARENT = "parent"
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
    INCOME_TAX = "income_tax"
    SOCIAL_SECURITY = "social_security"
    PENSION = "pension"


class CheckInStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"


class FeedbackRelationship(StrEnum):
    SELF = "self"
    MANAGER = "manager"
    PEER = "peer"
    REPORT = "report"
    EXTERNAL = "external"

    @property
    def label(self) -> str:
        return {
            "self": "Self review",
            "manager": "Manager review",
            "peer": "Peer review",
            "report": "Direct report review",
            "external": "External reviewer",
        }[self.value]


class FeedbackStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    DECLINED = "declined"


class ChangeRequestStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class JobChangeEvent(StrEnum):
    """Market-standard job-change events (Workday types / SF events)."""

    PROMOTION = "promotion"
    TRANSFER = "transfer"
    DEMOTION = "demotion"
    LATERAL_MOVE = "lateral_move"
    MANAGER_CHANGE = "manager_change"
    LOCATION_CHANGE = "location_change"
    DATA_CHANGE = "data_change"
    CONTRACT_CHANGE = "contract_change"
    TERMINATION = "termination"

    @property
    def label(self) -> str:
        return {
            "promotion": "Promotion",
            "transfer": "Transfer",
            "demotion": "Demotion",
            "lateral_move": "Lateral move",
            "manager_change": "Manager change",
            "location_change": "Location change",
            "data_change": "Data change",
            "contract_change": "Contract change",
            "termination": "Termination",
        }[self.value]


class JourneyType(StrEnum):
    ONBOARDING = "onboarding"
    OFFBOARDING = "offboarding"
    CROSSBOARDING = "crossboarding"
    PROBATION = "probation"

    @property
    def label(self) -> str:
        return {
            "onboarding": "Onboarding",
            "offboarding": "Offboarding",
            "crossboarding": "Crossboarding",
            "probation": "Probation",
        }[self.value]


class JourneyStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class JourneyTaskOwner(StrEnum):
    HR = "hr"
    MANAGER = "manager"
    IT = "it"
    EMPLOYEE = "employee"
    BUDDY = "buddy"
    FINANCE = "finance"

    @property
    def label(self) -> str:
        return {
            "hr": "HR",
            "manager": "Manager",
            "it": "IT",
            "employee": "Employee",
            "buddy": "Buddy",
            "finance": "Finance",
        }[self.value]


class JourneyTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


class ExitType(StrEnum):
    RESIGNATION = "resignation"
    TERMINATION = "termination"
    END_OF_CONTRACT = "end_of_contract"
    RETIREMENT = "retirement"
    MUTUAL_AGREEMENT = "mutual_agreement"

    @property
    def label(self) -> str:
        return {
            "resignation": "Resignation",
            "termination": "Termination",
            "end_of_contract": "End of fixed-term contract",
            "retirement": "Retirement",
            "mutual_agreement": "Mutual agreement",
        }[self.value]


class SeparationStatus(StrEnum):
    INITIATED = "initiated"
    NOTICE = "notice"
    CLEARANCE = "clearance"
    FINAL_PAY = "final_pay"
    EXITED = "exited"
    WITHDRAWN = "withdrawn"


class ChangeRequestType(StrEnum):
    RESIGNATION = "resignation"
    PROMOTION = "promotion"
    POSITION_CHANGE = "position_change"  # location, department, etc.
    LOCATION_CHANGE = "location_change"  # office relocation, remote/hybrid change
    MANAGER_CHANGE = "manager_change"
    EMPLOYMENT_TYPE_CHANGE = "employment_type_change"
    CONTRACT_CHANGE = "contract_change"
    REHIRE = "rehire"
    GROUP_COMPANY_TRANSFER = "group_company_transfer"
    COUNTRY_TRANSFER = "country_transfer"
    LEGAL_ENTITY_CHANGE = "legal_entity_change"
    PROJECT_ASSIGNMENT = "project_assignment"
    COST_CENTER_CHANGE = "cost_center_change"
    TIMESHEET_ADJUSTMENT = "timesheet_adjustment"
    LEAVE_REQUEST = "leave_request"  # special case, handled elsewhere
    ATTENDANCE_ADJUSTMENT = "attendance_adjustment"  # special case
    OTHER = "other"

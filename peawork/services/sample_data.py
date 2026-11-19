from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..domain import AuditAction, EmployeeStatus, EmploymentType, LeaveStatus
from ..models import (
    AttendanceRecord,
    Department,
    Employee,
    EmployeeHistory,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    Organization,
    Position,
    User,
)
from . import audit
from .bootstrap import seed_leave_types
from .errors import ConflictError

SAMPLE_DEPARTMENT_PREFIX = "SMP-"
SAMPLE_EMPLOYEE_PREFIX = "SMP-"
SAMPLE_EMAIL_DOMAIN = "sample.example.com"

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan",
    "Krishna", "Ishaan", "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Anika",
    "Navya", "Mira", "Riya", "Kavya", "Rohan", "Karan", "Neha", "Pooja", "Amit",
]

LAST_NAMES = [
    "Sharma", "Nair", "Patel", "Rao", "Iyer", "Gupta", "Mehta", "Reddy",
    "Kulkarni", "Singh", "Verma", "Joshi", "Bose", "Menon", "Chawla", "Desai",
    "Kapoor", "Malhotra", "Pillai", "Sethi",
]

# code, name, parent code, cost centre
DEPARTMENT_SPECS = [
    ("EXEC", "Executive Office", None, "CC-100"),
    ("ENG", "Engineering", "EXEC", "CC-200"),
    ("PLT", "Platform Engineering", "ENG", "CC-210"),
    ("APP", "Applications Engineering", "ENG", "CC-220"),
    ("DAT", "Data & AI", "ENG", "CC-230"),
    ("PRD", "Product", "EXEC", "CC-300"),
    ("PPL", "People & Culture", "EXEC", "CC-400"),
    ("TAL", "Talent Acquisition", "PPL", "CC-410"),
    ("FIN", "Finance", "EXEC", "CC-500"),
    ("SAL", "Sales", "EXEC", "CC-600"),
    ("MKT", "Marketing", "EXEC", "CC-700"),
    ("OPS", "Operations", "EXEC", "CC-800"),
    ("SUP", "Customer Support", "EXEC", "CC-900"),
]

# code, title, department code, grade, level
POSITION_SPECS = [
    ("CEO", "Chief Executive Officer", "EXEC", "E1", "L1"),
    ("VP-ENG", "VP Engineering", "ENG", "E2", "L1"),
    ("PLT-M", "Platform Engineering Manager", "PLT", "E3", "L2"),
    ("APP-M", "Applications Engineering Manager", "APP", "E3", "L2"),
    ("DAT-M", "Data & AI Manager", "DAT", "E3", "L2"),
    ("SSE", "Senior Software Engineer", "ENG", "E4", "L3"),
    ("SE", "Software Engineer", "ENG", "E5", "L4"),
    ("PE", "Platform Engineer", "PLT", "E4", "L3"),
    ("APP-SE", "Applications Engineer", "APP", "E5", "L4"),
    ("DE", "Data Engineer", "DAT", "E4", "L3"),
    ("HOP", "Head of Product", "PRD", "P1", "L1"),
    ("PM", "Product Manager", "PRD", "P2", "L3"),
    ("PAN", "Product Analyst", "PRD", "P3", "L4"),
    ("VP-PPL", "VP People & Culture", "PPL", "E2", "L1"),
    ("TAL-M", "Talent Acquisition Manager", "TAL", "E3", "L2"),
    ("HRP", "HR Partner", "PPL", "E4", "L3"),
    ("TP", "Talent Partner", "TAL", "E4", "L3"),
    ("FM", "Finance Manager", "FIN", "E3", "L2"),
    ("ACC", "Accountant", "FIN", "E4", "L3"),
    ("SM", "Sales Manager", "SAL", "E3", "L2"),
    ("AE", "Account Executive", "SAL", "E4", "L3"),
    ("MM", "Marketing Manager", "MKT", "E3", "L2"),
    ("MS", "Marketing Specialist", "MKT", "E4", "L3"),
    ("OM", "Operations Manager", "OPS", "E3", "L2"),
    ("OPA", "Operations Analyst", "OPS", "E4", "L3"),
    ("SUPM", "Support Manager", "SUP", "E3", "L2"),
    ("SSP", "Support Specialist", "SUP", "E4", "L3"),
]

# team department, manager position code, number of individual contributors
TEAM_SPECS = [
    ("ENG", "VP-ENG", 8),
    ("PLT", "PLT-M", 7),
    ("APP", "APP-M", 7),
    ("DAT", "DAT-M", 6),
    ("PRD", "HOP", 7),
    ("PPL", "VP-PPL", 4),
    ("TAL", "TAL-M", 5),
    ("FIN", "FM", 7),
    ("SAL", "SM", 12),
    ("MKT", "MM", 8),
    ("OPS", "OM", 7),
    ("SUP", "SUPM", 9),
]

IC_POSITIONS = {
    "ENG": ["SSE", "SE"],
    "PLT": ["PE", "SSE"],
    "APP": ["APP-SE", "SE"],
    "DAT": ["DE"],
    "PRD": ["PAN", "PM"],
    "PPL": ["HRP"],
    "TAL": ["TP"],
    "FIN": ["ACC"],
    "SAL": ["AE"],
    "MKT": ["MS"],
    "OPS": ["OPA"],
    "SUP": ["SSP"],
}

# Top-level functional heads. Sub-team managers report to the parent head.
DEPARTMENT_HEADS = {
    "ENG": "VP-ENG",
    "PRD": "HOP",
    "PPL": "VP-PPL",
    "FIN": "FM",
    "SAL": "SM",
    "MKT": "MM",
    "OPS": "OM",
    "SUP": "SUPM",
}

LOCATIONS = ["Gurugram", "Bengaluru", "Pune", "Hyderabad", "Mumbai", "Remote"]


@dataclass
class SampleDataResult:
    employees: int = 0
    departments: int = 0
    positions: int = 0
    replaced: bool = False


@dataclass
class CleanSampleResult:
    employees: int = 0
    users: int = 0
    departments: int = 0
    positions: int = 0


def sample_employee_count(db: Session, org_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count(Employee.id)).where(
                Employee.org_id == org_id, Employee.is_sample.is_(True)
            )
        )
        or 0
    )


def _department_code(db: Session, org_id: UUID, base: str) -> str:
    candidate = f"{SAMPLE_DEPARTMENT_PREFIX}{base}"[:24]
    suffix = 2
    while db.scalar(
        select(Department.id).where(
            Department.org_id == org_id, Department.code == candidate
        )
    ):
        candidate = f"{SAMPLE_DEPARTMENT_PREFIX}{base}-{suffix}"[:24]
        suffix += 1
    return candidate


def _position_code(db: Session, org_id: UUID, base: str) -> str:
    candidate = f"SMP-{base}"[:24]
    suffix = 2
    while db.scalar(
        select(Position.id).where(Position.org_id == org_id, Position.code == candidate)
    ):
        candidate = f"SMP-{base}-{suffix}"[:24]
        suffix += 1
    return candidate


def _name(index: int) -> tuple[str, str]:
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    last = LAST_NAMES[(index // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return first, last


def _slug(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum() or ch in {"-", "_"})


def _employee_rows(count: int) -> list[dict]:
    """Build a deterministic CEO → heads → managers → IC hierarchy."""
    rows: list[dict] = []
    team_sizes = [size for _, _, size in TEAM_SPECS]
    head_departments = [
        code for code, name, parent_code, cost_center in DEPARTMENT_SPECS
        if code in DEPARTMENT_HEADS
    ]
    sub_teams = [code for code, _, _ in TEAM_SPECS if code not in DEPARTMENT_HEADS]
    overhead = 1 + len(head_departments) + len(sub_teams)
    if count < overhead:
        raise ValueError(f"Sample size must be at least {overhead} employees")

    # Scale team sizes proportionally to the requested headcount.
    budget = count - overhead
    base = sum(team_sizes)
    scaled = [max(0, round(size * budget / base)) for size in team_sizes]
    diff = budget - sum(scaled)
    index = 0
    while diff:
        step = 1 if diff > 0 else -1
        scaled[index % len(scaled)] += step
        diff -= step
        index += 1

    rows.append(
        {
            "index": 0,
            "level": "ceo",
            "dept": "EXEC",
            "position": "CEO",
            "manager_index": None,
        }
    )

    head_rows: dict[str, int] = {}
    for dept_code, position_code in DEPARTMENT_HEADS.items():
        head_rows[dept_code] = len(rows)
        rows.append(
            {
                "index": len(rows),
                "level": "head",
                "dept": dept_code,
                "position": position_code,
                "manager_index": 0,
            }
        )

    team_managers: dict[str, int] = {}
    for dept_code, position_code, _ in TEAM_SPECS:
        if dept_code in head_rows:
            team_managers[dept_code] = head_rows[dept_code]
            continue
        parent = next(
            parent for code, _, parent, _ in DEPARTMENT_SPECS if code == dept_code
        )
        if parent not in head_rows:
            raise ValueError(f"Department {dept_code} has no reporting head")
        team_managers[dept_code] = len(rows)
        rows.append(
            {
                "index": len(rows),
                "level": "manager",
                "dept": dept_code,
                "position": position_code,
                "manager_index": head_rows[parent],
            }
        )

    for (dept_code, _, _), size in zip(TEAM_SPECS, scaled):
        positions = IC_POSITIONS[dept_code]
        for _ in range(size):
            rows.append(
                {
                    "index": len(rows),
                    "level": "ic",
                    "dept": dept_code,
                    "position": positions[len(rows) % len(positions)],
                    "manager_index": team_managers[dept_code],
                }
            )

    # Trim or extend to hit the exact target while keeping every row attached.
    while len(rows) > count:
        rows.pop()
    while len(rows) < count:
        rows.append(
            {
                "index": len(rows),
                "level": "ic",
                "dept": "ENG",
                "position": "SE",
                "manager_index": team_managers["ENG"],
            }
        )
    return rows


def _department_manager_rows(rows: list[dict]) -> dict[str, int]:
    """Map every department code to the row of its top-level person."""
    managers: dict[str, int] = {}
    for row in rows:
        if row["level"] in {"ceo", "head", "manager"}:
            managers.setdefault(str(row["dept"]), int(row["index"]))
    return managers


def _seed_leave(
    db: Session, org_id: UUID, actor: User, employees: list[Employee]
) -> tuple[int, int]:
    """Attach realistic leave balances and requests to sample employees."""
    leave_types = list(
        db.scalars(
            select(LeaveType)
            .where(LeaveType.org_id == org_id, LeaveType.active.is_(True))
            .order_by(LeaveType.code)
        ).all()
    )
    if not leave_types:
        org = db.get(Organization, org_id)
        if org is None:
            return 0, 0
        seed_leave_types(db, org)
        leave_types = list(
            db.scalars(
                select(LeaveType)
                .where(LeaveType.org_id == org_id, LeaveType.active.is_(True))
                .order_by(LeaveType.code)
            ).all()
        )
    if not leave_types:
        return 0, 0

    today = date.today()
    year = today.year
    balances = 0
    for employee in employees:
        tail = employee.employee_no.rsplit("-", 1)[-1]
        number = int(tail) if tail.isdigit() else 0
        for offset, leave_type in enumerate(leave_types):
            db.add(
                LeaveBalance(
                    org_id=org_id,
                    employee_id=employee.id,
                    leave_type_id=leave_type.id,
                    year=year,
                    opening=leave_type.annual_entitlement,
                    accrued=Decimal("0"),
                    used=Decimal((number + offset) % 4),
                )
            )
            balances += 1
    db.flush()

    requests = 0
    statuses = [LeaveStatus.APPROVED, LeaveStatus.PENDING, LeaveStatus.APPROVED, LeaveStatus.REJECTED]
    for index, employee in enumerate(employees):
        if index % 4:
            continue
        leave_type = leave_types[index % len(leave_types)]
        start = today - timedelta(days=10 + (index * 7) % 90)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        days = Decimal((index % 3) + 1)
        end = start + timedelta(days=int(days) - 1)
        status = statuses[index % len(statuses)]
        decided = status != LeaveStatus.PENDING
        db.add(
            LeaveRequest(
                org_id=org_id,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                start_date=start,
                end_date=end,
                days=days,
                reason="Sample leave request for demonstration reporting.",
                status=status,
                approver_id=actor.id if decided else None,
                approver_comment="Recorded in sample data." if decided else None,
                decided_at=datetime.now(timezone.utc) if decided else None,
            )
        )
        requests += 1
    db.flush()
    return requests, balances


def seed_sample(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    count: int = 100,
    replace: bool = False,
    request=None,
) -> SampleDataResult:
    """Create a coherent sample organisation with ``count`` employees.

    Existing sample records can optionally be replaced. Non-sample records are
    never modified.
    """
    if count < 1:
        raise ValueError("Sample size must be positive")
    existing = sample_employee_count(db, org_id)
    if existing and not replace:
        raise ConflictError(
            f"Sample data already exists ({existing} employees). Remove it first or use --replace."
        )

    replaced = False
    if existing and replace:
        clean_sample(db, org_id=org_id, actor=actor, request=request, record_audit=False)
        replaced = True

    rows = _employee_rows(count)

    departments: dict[str, Department] = {}
    for code, name, parent_code, cost_center in DEPARTMENT_SPECS:
        dept = Department(
            org_id=org_id,
            name=name,
            code=_department_code(db, org_id, code),
            cost_center=cost_center,
            is_sample=True,
        )
        db.add(dept)
        db.flush()
        departments[code] = dept
    for code, _, parent_code, _ in DEPARTMENT_SPECS:
        if parent_code:
            departments[code].parent_id = departments[parent_code].id
    db.flush()

    positions: dict[str, Position] = {}
    for code, title, dept_code, grade, level in POSITION_SPECS:
        position = Position(
            org_id=org_id,
            title=title,
            code=_position_code(db, org_id, code),
            department_id=departments[dept_code].id,
            grade=grade,
            level=level,
            is_sample=True,
        )
        db.add(position)
        db.flush()
        positions[code] = position

    employees: list[Employee] = []
    for row in rows:
        index = int(row["index"])
        first, last = _name(index)
        number = index + 1
        if row["level"] == "ceo":
            joined = date(2014, 4, 1)
        elif row["level"] == "head":
            joined = date(2016, 1, 11) + timedelta(days=index * 11)
        elif row["level"] == "manager":
            joined = date(2017, 6, 5) + timedelta(days=index * 7)
        else:
            joined = date(2019, 1, 7) + timedelta(days=(index * 23) % 2200)
        employee = Employee(
            org_id=org_id,
            employee_no=f"{SAMPLE_EMPLOYEE_PREFIX}{number:04d}",
            first_name=first,
            last_name=last,
            work_email=f"{_slug(first)}.{_slug(last)}.{number:03d}@{SAMPLE_EMAIL_DOMAIN}",
            personal_email=None,
            phone=f"+91 9{(index * 7919 + 100000000) % 1000000000:09d}",
            date_of_joining=joined,
            date_of_birth=date(1972 + (index * 3) % 27, (index % 12) + 1, (index % 27) + 1),
            gender="female" if index % 2 else "male",
            employment_type=(
                EmploymentType.CONTRACT
                if index % 19 == 0
                else EmploymentType.INTERN
                if index % 37 == 0
                else EmploymentType.FULL_TIME
            ),
            status=(
                EmployeeStatus.PROBATION
                if index % 17 == 0
                else EmployeeStatus.ON_LEAVE
                if index % 29 == 0
                else EmployeeStatus.ACTIVE
            ),
            department_id=departments[row["dept"]].id,
            position_id=positions[row["position"]].id,
            location=LOCATIONS[index % len(LOCATIONS)],
            pan_masked=f"XXXXX{index + 1000:04d}X",
            bank_masked=f"XXXX-{index + 1000:04d}",
            is_sample=True,
        )
        db.add(employee)
        db.flush()
        employees.append(employee)

    for row, employee in zip(rows, employees):
        manager_index = row["manager_index"]
        if manager_index is not None:
            employee.manager_id = employees[int(manager_index)].id
    db.flush()

    # Point each department at its head/manager for org-chart rendering.
    manager_rows = _department_manager_rows(rows)
    for dept_code, manager_index in manager_rows.items():
        if dept_code in departments:
            departments[dept_code].manager_employee_id = employees[manager_index].id
    db.flush()

    leave_requests, leave_balances = _seed_leave(db, org_id, actor, employees)

    audit.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="sample_data",
        entity_id=str(org_id),
        details={
            "employees": len(employees),
            "departments": len(departments),
            "positions": len(positions),
            "leave_requests": leave_requests,
            "leave_balances": leave_balances,
            "replaced": replaced,
        },
        request=request,
        org_id=org_id,
    )
    return SampleDataResult(
        employees=len(employees),
        departments=len(departments),
        positions=len(positions),
        replaced=replaced,
    )


def clean_sample(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    request=None,
    record_audit: bool = True,
) -> CleanSampleResult:
    """Delete only records flagged as sample data.

    Non-sample employees, departments and positions are preserved. Departments
    or positions still referenced by real records are kept even if flagged.
    """
    result = CleanSampleResult()

    sample_employees = list(
        db.scalars(
            select(Employee).where(
                Employee.org_id == org_id, Employee.is_sample.is_(True)
            )
        ).all()
    )
    sample_ids = {employee.id for employee in sample_employees}

    if sample_ids:
        # Remove child rows explicitly so cleanup is deterministic even on
        # database backends that do not enforce ON DELETE CASCADE.
        db.execute(delete(LeaveRequest).where(LeaveRequest.employee_id.in_(sample_ids)))
        db.execute(delete(LeaveBalance).where(LeaveBalance.employee_id.in_(sample_ids)))
        db.execute(
            delete(AttendanceRecord).where(AttendanceRecord.employee_id.in_(sample_ids))
        )
        db.execute(
            delete(EmployeeHistory).where(EmployeeHistory.employee_id.in_(sample_ids))
        )
        db.flush()

    for department in db.scalars(
        select(Department).where(Department.org_id == org_id)
    ).all():
        if department.manager_employee_id in sample_ids:
            department.manager_employee_id = None
    db.flush()

    for employee in sample_employees:
        db.delete(employee)
    db.flush()
    result.employees = len(sample_employees)

    sample_users = list(
        db.scalars(
            select(User).where(User.org_id == org_id, User.is_sample.is_(True))
        ).all()
    )
    for user in sample_users:
        if user.role.value in { "admin", "super_admin" }:
            continue
        db.delete(user)
        result.users += 1
    db.flush()

    # Remove sample positions no real employee still points at.
    for position in list(
        db.scalars(
            select(Position).where(
                Position.org_id == org_id, Position.is_sample.is_(True)
            )
        ).all()
    ):
        referenced = db.scalar(
            select(func.count(Employee.id)).where(Employee.position_id == position.id)
        )
        if referenced:
            continue
        db.delete(position)
        result.positions += 1
    db.flush()

    # Delete sample departments deepest-first, keeping any still referenced.
    sample_departments = list(
        db.scalars(
            select(Department).where(
                Department.org_id == org_id, Department.is_sample.is_(True)
            )
        ).all()
    )
    by_id = {dept.id: dept for dept in sample_departments}

    def depth(dept: Department) -> int:
        value = 0
        cursor = dept
        seen: set[UUID] = set()
        while cursor.parent_id in by_id and cursor.parent_id not in seen:
            seen.add(cursor.parent_id)
            cursor = by_id[cursor.parent_id]
            value += 1
        return value

    for department in sorted(sample_departments, key=depth, reverse=True):
        employee_refs = db.scalar(
            select(func.count(Employee.id)).where(Employee.department_id == department.id)
        )
        position_refs = db.scalar(
            select(func.count(Position.id)).where(Position.department_id == department.id)
        )
        if employee_refs or position_refs:
            continue
        db.delete(department)
        result.departments += 1
    db.flush()

    # Real departments must not retain a dangling sample parent.
    for department in db.scalars(
        select(Department).where(
            Department.org_id == org_id, Department.is_sample.is_(False)
        )
    ).all():
        if department.parent_id in sample_ids:
            department.parent_id = None

    if record_audit:
        audit.record(
            db,
            actor=actor,
            action=AuditAction.DELETE,
            entity_type="sample_data",
            entity_id=str(org_id),
            details={
                "employees": result.employees,
                "users": result.users,
                "departments": result.departments,
                "positions": result.positions,
            },
            request=request,
            org_id=org_id,
        )
    return result


__all__ = [
    "CleanSampleResult",
    "SampleDataResult",
    "clean_sample",
    "sample_employee_count",
    "seed_sample",
]

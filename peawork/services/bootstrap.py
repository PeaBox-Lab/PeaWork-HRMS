from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain import AuditAction, EmployeeStatus, EmploymentType, Role
from ..models import Department, Employee, Holiday, LeaveType, Organization, Position, User
from ..security import generate_password, hash_password, password_problems
from . import audit

DEFAULT_LEAVE_TYPES = [
    ("Casual Leave", "CL", Decimal("12"), Decimal("3")),
    ("Sick Leave", "SL", Decimal("10"), Decimal("0")),
    ("Earned Leave", "EL", Decimal("15"), Decimal("5")),
]


@dataclass
class BootstrapResult:
    organization: Organization
    admin: User
    generated_password: str | None
    admin_created: bool
    org_created: bool


def get_organization(db: Session) -> Organization | None:
    return db.scalar(select(Organization).order_by(Organization.created_at).limit(1))


def ensure_organization(db: Session, settings: Settings) -> tuple[Organization, bool]:
    org = get_organization(db)
    if org:
        return org, False
    org = Organization(
        name=settings.organization_name,
        legal_name=settings.organization_name,
        country_code=settings.organization_country,
        timezone=settings.organization_timezone,
        currency=settings.organization_currency,
    )
    db.add(org)
    db.flush()
    return org, True


def ensure_admin(db: Session, settings: Settings, org: Organization) -> tuple[User, str | None, bool]:
    email = settings.bootstrap_admin_email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        return existing, None, False

    password = settings.bootstrap_admin_password
    generated: str | None = None
    if not password:
        generated = generate_password()
        password = generated
    problems = password_problems(password)
    if problems:
        raise ValueError("Bootstrap admin password is too weak: " + "; ".join(problems))

    admin = User(
        org_id=org.id,
        email=email,
        full_name=settings.bootstrap_admin_name,
        password_hash=hash_password(password),
        # The bootstrap account is the platform super admin; a super admin is
        # also an administrator of its organisation.
        role=Role.SUPER_ADMIN,
        must_change_password=generated is not None,
    )
    db.add(admin)
    db.flush()
    audit.record(
        db,
        actor=admin,
        action=AuditAction.CREATE,
        entity_type="user",
        entity_id=admin.id,
        details={"email": email, "role": Role.SUPER_ADMIN.value, "bootstrap": True},
        org_id=org.id,
    )
    return admin, generated, True


def seed_leave_types(db: Session, org: Organization) -> int:
    existing = set(db.scalars(select(LeaveType.code).where(LeaveType.org_id == org.id)).all())
    created = 0
    for name, code, entitlement, carry in DEFAULT_LEAVE_TYPES:
        if code in existing:
            continue
        db.add(
            LeaveType(
                org_id=org.id,
                name=name,
                code=code,
                annual_entitlement=entitlement,
                carry_forward_limit=carry,
            )
        )
        created += 1
    db.flush()
    return created


def seed_holidays(db: Session, org: Organization, years: tuple[int, ...] | None = None) -> int:
    today = date.today()
    years = years or (today.year, today.year + 1)
    fixed = [(1, 26, "Republic Day"), (8, 15, "Independence Day"), (10, 2, "Gandhi Jayanti")]
    created = 0
    existing = set(
        db.scalars(select(Holiday.holiday_date).where(Holiday.org_id == org.id)).all()
    )
    for year in years:
        for month, day, name in fixed:
            holiday_date = date(year, month, day)
            if holiday_date in existing:
                continue
            db.add(Holiday(org_id=org.id, holiday_date=holiday_date, name=name))
            created += 1
    db.flush()
    return created


def seed_demo(db: Session, org: Organization, admin: User) -> None:
    if db.scalar(select(func.count(Employee.id)).where(Employee.org_id == org.id)):
        return

    dept_specs = [
        ("Engineering", "ENG"),
        ("People", "PPL"),
        ("Finance", "FIN"),
    ]
    departments: dict[str, Department] = {}
    for name, code in dept_specs:
        dept = Department(org_id=org.id, name=name, code=code)
        db.add(dept)
        db.flush()
        departments[code] = dept

    pos_engineer = Position(org_id=org.id, title="Software Engineer", code="ENG-L2", department_id=departments["ENG"].id)
    pos_hr = Position(org_id=org.id, title="HR Partner", code="PPL-L3", department_id=departments["PPL"].id)
    db.add_all([pos_engineer, pos_hr])
    db.flush()

    manager = Employee(
        org_id=org.id,
        employee_no="EMP-0001",
        first_name="Aarav",
        last_name="Sharma",
        work_email="aarav.sharma@example.com",
        date_of_joining=date(2024, 4, 1),
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        department_id=departments["ENG"].id,
        position_id=pos_engineer.id,
        location="Gurugram",
    )
    db.add(manager)
    db.flush()

    employee = Employee(
        org_id=org.id,
        employee_no="EMP-0002",
        first_name="Diya",
        last_name="Nair",
        work_email="diya.nair@example.com",
        date_of_joining=date(2025, 6, 16),
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        department_id=departments["ENG"].id,
        position_id=pos_engineer.id,
        manager_id=manager.id,
        location="Gurugram",
    )
    db.add(employee)
    db.flush()

    db.add(
        User(
            org_id=org.id,
            email="manager@example.com",
            full_name="Aarav Sharma",
            password_hash=hash_password("ManagerDemo123!"),
            role="manager",  # type: ignore[arg-type]
            employee_id=manager.id,
        )
    )
    db.add(
        User(
            org_id=org.id,
            email="employee@example.com",
            full_name="Diya Nair",
            password_hash=hash_password("EmployeeDemo123!"),
            role="employee",  # type: ignore[arg-type]
            employee_id=employee.id,
        )
    )
    db.flush()


def bootstrap(db: Session, settings: Settings, *, with_demo: bool = False) -> BootstrapResult:
    org, org_created = ensure_organization(db, settings)
    admin, generated, admin_created = ensure_admin(db, settings, org)
    seed_leave_types(db, org)
    seed_holidays(db, org)
    if with_demo:
        seed_demo(db, org, admin)
    db.commit()
    return BootstrapResult(org, admin, generated, admin_created, org_created)


__all__ = [
    "BootstrapResult",
    "bootstrap",
    "ensure_admin",
    "ensure_organization",
    "get_organization",
    "seed_demo",
    "seed_holidays",
    "seed_leave_types",
]

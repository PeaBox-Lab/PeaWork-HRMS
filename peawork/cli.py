from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, inspect, select

from . import __version__
from .config import Settings, get_settings
from .db import create_all, get_engine, session_scope
from .domain import AuditAction, Role
from .models import Employee, User
from .security import (
    generate_password,
    hash_password,
    password_problems,
)
from .services import audit
from .services.auth import revoke_all_sessions
from .services.bootstrap import (
    bootstrap,
    ensure_organization,
    get_organization,
    seed_demo,
    seed_holidays,
    seed_leave_types,
)
from .services.errors import DomainError
from .services.reports import employees_workbook, full_workbook
from .services.sample_data import clean_sample, sample_employee_count, seed_sample


def _require_schema(settings: Settings) -> None:
    """Create the schema for dev/test, but never for production."""
    tables = set(inspect(get_engine()).get_table_names())
    if "organizations" in tables:
        return
    if settings.is_production:
        raise SystemExit(
            "Database schema is empty. Apply migrations first:\n"
            "  alembic upgrade head"
        )
    print("Schema not found; creating tables for a non-production environment.")
    create_all()


def _emit_password(email: str, password: str | None) -> None:
    if password:
        print()
        print("=" * 68)
        print(f"  One-time password for {email}")
        print(f"  {password}")
        print("  Store it securely. It will not be shown again.")
        print("  The user must change it after the first sign-in.")
        print("=" * 68)
        print()


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    with session_scope() as db:
        result = bootstrap(db, settings, with_demo=args.demo)
        print(f"PeaWork {__version__} initialised")
        print(f"  Environment : {settings.env}")
        print(f"  Organisation: {result.organization.name} ({result.organization.id})")
        print(f"  Admin       : {result.admin.email}")
        if result.admin_created:
            _emit_password(result.admin.email, result.generated_password)
        else:
            print("  Admin account already existed; password unchanged.")
        if args.demo:
            print("  Demo data   : seeded (manager@example.com, employee@example.com)")
    return 0


def cmd_create_admin(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    email = args.email.strip().lower()
    password = args.password
    generated: str | None = None
    if not password:
        generated = generate_password()
        password = generated
    problems = password_problems(password)
    if problems:
        raise SystemExit("Password " + "; ".join(problems))

    with session_scope() as db:
        org, org_created = ensure_organization(db, settings)
        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise SystemExit(f"A user with email {email} already exists")

        employee = None
        if args.employee:
            try:
                employee_id = UUID(args.employee)
            except ValueError:
                raise SystemExit("--employee must be a valid employee UUID")
            employee = db.get(Employee, employee_id)
            if employee is None or employee.org_id != org.id:
                raise SystemExit("Employee not found in this organisation")

        admin = User(
            org_id=org.id,
            email=email,
            full_name=args.name.strip(),
            password_hash=hash_password(password),
            role=Role.ADMIN,
            employee_id=employee.id if employee else None,
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
            details={"email": email, "role": "admin", "via": "cli"},
            org_id=org.id,
        )
        print(f"Created administrator {email} in {org.name}")
        if org_created:
            print("Created the organisation record as part of this command.")
        _emit_password(email, generated)
    return 0


def cmd_seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    with session_scope() as db:
        org = get_organization(db)
        if org is None:
            raise SystemExit("No organisation found. Run `python -m peawork init` first.")
        admin = db.scalar(
            select(User).where(User.org_id == org.id, User.role == Role.ADMIN).limit(1)
        )
        if admin is None:
            raise SystemExit("No administrator found. Run `python -m peawork create-admin` first.")
        created_types = seed_leave_types(db, org)
        created_holidays = seed_holidays(db, org)
        seed_demo(db, org, admin)
        print(
            f"Demo data ensured for {org.name}: "
            f"{created_types} leave type(s), {created_holidays} holiday(s)."
        )
        print("Demo sign-ins: manager@example.com / ManagerDemo123!")
        print("               employee@example.com / EmployeeDemo123!")
        print("Both demo accounts must change password at first sign-in.")
    return 0


def _admin_actor(db):
    org = get_organization(db)
    if org is None:
        raise SystemExit("No organisation found. Run `python -m peawork init` first.")
    admin = db.scalar(
        select(User).where(User.org_id == org.id, User.role == Role.ADMIN).limit(1)
    )
    if admin is None:
        raise SystemExit("No administrator found. Run `python -m peawork create-admin` first.")
    return org, admin


def cmd_seed_sample(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    with session_scope() as db:
        org, admin = _admin_actor(db)
        seed_leave_types(db, org)
        seed_holidays(db, org)
        try:
            result = seed_sample(
                db,
                org_id=org.id,
                actor=admin,
                count=args.count,
                replace=args.replace,
            )
        except (DomainError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print(
            f"Sample data ready for {org.name}: {result.employees} employees, "
            f"{result.departments} departments, {result.positions} positions."
        )
        if result.replaced:
            print("Existing sample data was replaced; real records were preserved.")
        print("Sample employees use the SMP- prefix and @sample.example.com addresses.")
    return 0


def cmd_clean_sample(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    with session_scope() as db:
        org, admin = _admin_actor(db)
        result = clean_sample(db, org_id=org.id, actor=admin)
        print(
            f"Removed sample data: {result.employees} employees, {result.users} users, "
            f"{result.departments} departments, {result.positions} positions."
        )
        print("Real records were preserved.")
    return 0


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    with session_scope() as db:
        org, _ = _admin_actor(db)
        content = (
            employees_workbook(db, org=org)
            if args.employees_only
            else full_workbook(db, org=org)
        )
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    print(f"Wrote {out.resolve()} ({len(content):,} bytes)")
    return 0


def cmd_reset_password(args: argparse.Namespace, settings: Settings) -> int:
    _require_schema(settings)
    email = args.email.strip().lower()
    password = args.password
    generated: str | None = None
    if not password:
        generated = generate_password()
        password = generated
    problems = password_problems(password)
    if problems:
        raise SystemExit("Password " + "; ".join(problems))

    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"No user found with email {email}")
        user.password_hash = hash_password(password)
        user.must_change_password = generated is not None
        revoke_all_sessions(db, user.id)
        audit.record(
            db,
            actor=None,
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=user.id,
            details={"password_reset": True, "via": "cli"},
            org_id=user.org_id,
        )
        print(f"Password reset for {email}. All sessions revoked.")
        _emit_password(email, generated)
    return 0


def cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    """Drain the jobs queue once (or on an interval with --interval)."""
    from .services import jobs as jobs_service

    _require_schema(settings)
    once = args.once or args.interval <= 0
    while True:
        with session_scope() as db:
            org = get_organization(db)
            result = jobs_service.run_pending_jobs(db, limit=args.limit, org_id=org.id if org else None)
        print(
            f"worker: claimed={result['claimed']} succeeded={result['succeeded']} "
            f"failed={result['failed']} dead_lettered={result['dead_lettered']}",
            flush=True,
        )
        if once:
            return 0
        import time

        time.sleep(args.interval)


def cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    print(f"PeaWork {__version__}")
    print(f"  Environment : {settings.env}")
    print(f"  Database    : {settings.database_url.split('@')[-1]}")
    tables = set(inspect(get_engine()).get_table_names())
    print(f"  Tables      : {len(tables)}")
    if "organizations" not in tables:
        print("  Status      : schema missing (run `alembic upgrade head`)")
        return 1
    with session_scope() as db:
        org = get_organization(db)
        users = int(db.scalar(select(func.count(User.id))) or 0)
        employees = int(db.scalar(select(func.count(Employee.id))) or 0)
        admins = int(
            db.scalar(select(func.count(User.id)).where(User.role == Role.ADMIN, User.active.is_(True)))
            or 0
        )
        print(f"  Organisation: {org.name if org else 'missing'}")
        print(f"  Users       : {users} ({admins} active admin)")
        print(f"  Employees   : {employees}")
        print(f"  Sample data : {sample_employee_count(db, org.id) if org else 0} employee(s)")
    print("  Status      : ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m peawork",
        description="PeaWork HRMS maintenance commands.",
    )
    parser.add_argument("--version", action="version", version=f"PeaWork {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialise the organisation, admin and reference data")
    init.add_argument("--demo", action="store_true", help="Also seed demo employees and sign-ins")
    init.set_defaults(func=cmd_init)

    create_admin = sub.add_parser("create-admin", help="Create an additional administrator")
    create_admin.add_argument("--email", required=True)
    create_admin.add_argument("--name", required=True)
    create_admin.add_argument("--password", default=None, help="Omit to generate a one-time password")
    create_admin.add_argument("--employee", default=None, help="Optional employee UUID to link")
    create_admin.set_defaults(func=cmd_create_admin)

    demo = sub.add_parser("seed-demo", help="Ensure demo employees and sign-ins exist")
    demo.set_defaults(func=cmd_seed_demo)

    sample = sub.add_parser("seed-sample", help="Generate a sample employee hierarchy")
    sample.add_argument("--count", type=int, default=100, help="Number of employees (default 100)")
    sample.add_argument(
        "--replace", action="store_true", help="Replace existing sample data"
    )
    sample.set_defaults(func=cmd_seed_sample)

    clean = sub.add_parser("clean-sample", help="Remove only records flagged as sample data")
    clean.set_defaults(func=cmd_clean_sample)

    export = sub.add_parser("export", help="Write an Excel report")
    export.add_argument("--out", default="peawork-report.xlsx", help="Output .xlsx path")
    export.add_argument(
        "--employees-only",
        action="store_true",
        help="Export only the employee register instead of the full workbook",
    )
    export.set_defaults(func=cmd_export)

    reset = sub.add_parser("reset-password", help="Reset a user's password and revoke sessions")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", default=None, help="Omit to generate a one-time password")
    reset.set_defaults(func=cmd_reset_password)

    check = sub.add_parser("check", help="Print deployment and database diagnostics")
    check.set_defaults(func=cmd_check)

    worker = sub.add_parser("worker", help="Drain the background jobs queue")
    worker.add_argument("--once", action="store_true", help="Run one drain pass and exit")
    worker.add_argument("--interval", type=int, default=0, help="Seconds between passes (0 = once)")
    worker.add_argument("--limit", type=int, default=10, help="Max jobs per pass")
    worker.set_defaults(func=cmd_worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        return int(args.func(args, settings))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

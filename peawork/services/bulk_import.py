from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import UUID

from ..domain import AuditAction, EmployeeStatus, EmploymentType
from ..models import Department, Employee, Position, User
from ..schemas import EmployeeCreate
from . import audit
from .employees import create_employee
from .errors import BulkImportError, ConflictError, ForbiddenError, ValidationError

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 2000

TEMPLATE_COLUMNS = [
    "employee_no",
    "first_name",
    "last_name",
    "work_email",
    "personal_email",
    "phone",
    "date_of_joining",
    "date_of_birth",
    "gender",
    "employment_type",
    "status",
    "department",
    "position",
    "manager_email",
    "location",
    "pan_masked",
    "bank_masked",
]

TEMPLATE_EXAMPLE = {
    "employee_no": "EMP-1001",
    "first_name": "Priya",
    "last_name": "Menon",
    "work_email": "priya.menon@example.com",
    "personal_email": "priya.menon.personal@example.com",
    "phone": "+91 98765 43210",
    "date_of_joining": "2026-01-05",
    "date_of_birth": "1993-04-12",
    "gender": "female",
    "employment_type": "full_time",
    "status": "active",
    "department": "Engineering",
    "position": "Software Engineer",
    "manager_email": "manager@example.com",
    "location": "Bengaluru",
    "pan_masked": "XXXXX1234X",
    "bank_masked": "XXXX-1234",
}

HEADER_ALIASES = {
    "employee_no": "employee_no",
    "employee_number": "employee_no",
    "employee_id": "employee_no",
    "emp_no": "employee_no",
    "first_name": "first_name",
    "firstname": "first_name",
    "given_name": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "surname": "last_name",
    "family_name": "last_name",
    "work_email": "work_email",
    "email": "work_email",
    "official_email": "work_email",
    "personal_email": "personal_email",
    "phone": "phone",
    "mobile": "phone",
    "phone_number": "phone",
    "date_of_joining": "date_of_joining",
    "joining_date": "date_of_joining",
    "doj": "date_of_joining",
    "date_of_birth": "date_of_birth",
    "birth_date": "date_of_birth",
    "dob": "date_of_birth",
    "gender": "gender",
    "employment_type": "employment_type",
    "type": "employment_type",
    "status": "status",
    "department": "department",
    "department_name": "department",
    "department_code": "department",
    "dept": "department",
    "position": "position",
    "position_name": "position",
    "designation": "position",
    "job_title": "position",
    "title": "position",
    "manager": "manager",
    "manager_email": "manager_email",
    "manager_employee_no": "manager_employee_no",
    "location": "location",
    "work_location": "location",
    "pan_masked": "pan_masked",
    "pan": "pan_masked",
    "bank_masked": "bank_masked",
    "bank_account": "bank_masked",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


@dataclass
class RowError:
    row: int
    message: str

    def __str__(self) -> str:
        return f"Row {self.row}: {self.message}"


@dataclass
class ImportResult:
    created: int = 0
    departments_created: int = 0
    positions_created: int = 0
    managers_linked: int = 0
    errors: list[RowError] = field(default_factory=list)


def _normalise_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    for char in (" ", "-", "/"):
        text = text.replace(char, "_")
    return text.strip("_")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_table(filename: str, content: bytes) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    name = (filename or "").strip().lower()
    if not name:
        raise ValidationError("Choose a CSV or XLSX file to upload")
    if len(content) > MAX_IMPORT_BYTES:
        raise ValidationError("The upload exceeds the 5 MB import limit")
    if not content:
        raise ValidationError("The uploaded file is empty")

    if name.endswith((".xlsx", ".xlsm")):
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:  # openpyxl raises several format-specific errors
            raise ValidationError("The XLSX file could not be read") from exc
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        workbook.close()
    elif name.endswith((".csv", ".txt")):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [tuple(row) for row in csv.reader(StringIO(text), dialect)]
    elif name.endswith(".xls"):
        raise ValidationError("Legacy .xls files are not supported; save the file as .xlsx")
    else:
        raise ValidationError("Unsupported file type. Upload a .csv or .xlsx file")

    if not rows:
        raise ValidationError("The uploaded file has no rows")

    header_row = rows[0]
    headers: list[str] = []
    for value in header_row:
        key = HEADER_ALIASES.get(_normalise_header(value), _normalise_header(value))
        headers.append(key)

    records: list[tuple[int, dict[str, Any]]] = []
    for line, row in enumerate(rows[1:], start=2):
        if not any(_cell(value) for value in row):
            continue
        record: dict[str, Any] = {}
        for column, value in zip(headers, row):
            if column:
                record[column] = value
        records.append((line, record))
    if len(records) > MAX_IMPORT_ROWS:
        raise ValidationError(f"Imports are limited to {MAX_IMPORT_ROWS} employees per file")
    return headers, records


def _parse_date(value: Any, field_name: str, line: int, errors: list[RowError]) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _cell(value)
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    errors.append(
        RowError(line, f"{field_name} '{text}' is not a recognised date (use YYYY-MM-DD)")
    )
    return None


def _enum_value(
    mapping: dict[str, Any],
    value: Any,
    field_name: str,
    line: int,
    default: Any,
    errors: list[RowError],
) -> Any:
    text = _cell(value).lower()
    if not text:
        return default
    key = text.replace(" ", "_").replace("-", "_")
    if key in mapping:
        return mapping[key]
    allowed = ", ".join(sorted({item.value for item in mapping.values()}))
    errors.append(RowError(line, f"{field_name} '{text}' is invalid (allowed: {allowed})"))
    return default


def _enum_map(enum_cls: type) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for member in enum_cls:
        mapping[member.value] = member
        mapping[member.name.lower()] = member
    return mapping


def _parse_records(
    rows: list[tuple[int, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[RowError]]:
    errors: list[RowError] = []
    records: list[dict[str, Any]] = []
    employment_types = _enum_map(EmploymentType)
    statuses = _enum_map(EmployeeStatus)
    seen_numbers: dict[str, int] = {}
    seen_emails: dict[str, int] = {}

    for line, raw in rows:
        first_name = _cell(raw.get("first_name"))
        last_name = _cell(raw.get("last_name"))
        if not first_name:
            errors.append(RowError(line, "first_name is required"))
        if not last_name:
            errors.append(RowError(line, "last_name is required"))

        employee_no = _cell(raw.get("employee_no")) or None
        work_email = _cell(raw.get("work_email")).lower() or None

        if employee_no:
            key = employee_no.lower()
            if key in seen_numbers:
                errors.append(
                    RowError(line, f"employee_no '{employee_no}' duplicates row {seen_numbers[key]}")
                )
            else:
                seen_numbers[key] = line
        if work_email:
            if work_email in seen_emails:
                errors.append(
                    RowError(line, f"work_email '{work_email}' duplicates row {seen_emails[work_email]}")
                )
            else:
                seen_emails[work_email] = line

        manager = _cell(raw.get("manager"))
        manager_email = _cell(raw.get("manager_email")).lower() or None
        manager_employee_no = _cell(raw.get("manager_employee_no")) or None
        if manager:
            if "@" in manager:
                manager_email = manager_email or manager.lower()
            else:
                manager_employee_no = manager_employee_no or manager

        record = {
            "_line": line,
            "employee_no": employee_no,
            "first_name": first_name[:80],
            "last_name": last_name[:80],
            "work_email": work_email,
            "personal_email": _cell(raw.get("personal_email")).lower() or None,
            "phone": _cell(raw.get("phone")) or None,
            "date_of_joining": _parse_date(raw.get("date_of_joining"), "date_of_joining", line, errors),
            "date_of_birth": _parse_date(raw.get("date_of_birth"), "date_of_birth", line, errors),
            "gender": _cell(raw.get("gender")) or None,
            "employment_type": _enum_value(
                employment_types,
                raw.get("employment_type"),
                "employment_type",
                line,
                EmploymentType.FULL_TIME,
                errors,
            ),
            "status": _enum_value(
                statuses, raw.get("status"), "status", line, EmployeeStatus.ACTIVE, errors
            ),
            "department": _cell(raw.get("department")) or None,
            "position": _cell(raw.get("position")) or None,
            "manager_email": manager_email,
            "manager_employee_no": manager_employee_no,
            "location": _cell(raw.get("location")) or None,
            "pan_masked": _cell(raw.get("pan_masked")) or None,
            "bank_masked": _cell(raw.get("bank_masked")) or None,
        }
        records.append(record)
    return records, errors


def _unique_code(value: str, existing: set[str]) -> str:
    base = "".join(ch for ch in value.upper() if ch.isalnum())[:18] or "IMPORT"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:19]}-{suffix}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _department_index(db: Session, org_id: UUID) -> tuple[list[Department], dict[str, Department]]:
    departments = list(
        db.scalars(select(Department).where(Department.org_id == org_id)).all()
    )
    index: dict[str, Department] = {}
    for department in departments:
        index[department.code.lower()] = department
        index[department.name.lower()] = department
    return departments, index


def _position_index(db: Session, org_id: UUID) -> tuple[list[Position], dict[str, Position]]:
    positions = list(db.scalars(select(Position).where(Position.org_id == org_id)).all())
    index: dict[str, Position] = {}
    for position in positions:
        index[position.code.lower()] = position
        index[position.title.lower()] = position
    return positions, index


def _resolve_position(
    db: Session,
    org_id: UUID,
    index: dict[str, Position],
    title: str,
    department: Department | None,
    *,
    line: int,
    errors: list[RowError],
) -> tuple[Position | None, bool]:
    position = index.get(title.lower())
    if position is not None:
        return position, False
    code = _unique_code(title, {item.code.upper() for item in index.values()})
    position = Position(
        org_id=org_id,
        title=title[:120],
        code=code,
        department_id=department.id if department else None,
    )
    db.add(position)
    db.flush()
    index[title.lower()] = position
    index[code.lower()] = position
    return position, True


def _resolve_department(
    db: Session,
    org_id: UUID,
    index: dict[str, Department],
    value: str,
    *,
    line: int,
    errors: list[RowError],
) -> tuple[Department | None, bool]:
    department = index.get(value.lower())
    if department is not None:
        return department, False
    code = _unique_code(value, {item.code.upper() for item in index.values()})
    department = Department(org_id=org_id, name=value[:120], code=code)
    db.add(department)
    db.flush()
    index[value.lower()] = department
    index[code.lower()] = department
    return department, True


def import_employees(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    filename: str,
    content: bytes,
    auto_create: bool = True,
    request=None,
) -> ImportResult:
    """Import employees from CSV/XLSX with all-or-nothing semantics.

    Any validation or reference error rolls back every employee created by the
    upload. Missing departments and positions are created when ``auto_create``
    is enabled; managers may be referenced by email or employee number.
    """
    if actor.role.value not in {"admin", "super_admin", "hr"}:
        raise ForbiddenError("Only HR or administrators may import employees")

    _, rows = _read_table(filename, content)
    records, errors = _parse_records(rows)
    if not records and not errors:
        raise ValidationError("No employee rows were found in the file")
    if errors:
        raise BulkImportError(errors)

    result = ImportResult()
    with db.begin_nested():
        _, dept_index = _department_index(db, org_id)
        _, pos_index = _position_index(db, org_id)
        created: list[tuple[dict[str, Any], Employee]] = []

        for record in records:
            line = int(record["_line"])
            department = None
            if record["department"]:
                if record["department"].lower() in dept_index:
                    department = dept_index[record["department"].lower()]
                elif auto_create:
                    department, was_created = _resolve_department(
                        db, org_id, dept_index, record["department"], line=line, errors=errors
                    )
                    if was_created:
                        result.departments_created += 1
                else:
                    errors.append(
                        RowError(
                            line,
                            f"Unknown department '{record['department']}' "
                            "(add it first or enable auto-create)",
                        )
                    )

            position = None
            if record["position"]:
                if record["position"].lower() in pos_index:
                    position = pos_index[record["position"].lower()]
                elif auto_create:
                    position, was_created = _resolve_position(
                        db,
                        org_id,
                        pos_index,
                        record["position"],
                        department,
                        line=line,
                        errors=errors,
                    )
                    if was_created:
                        result.positions_created += 1
                else:
                    errors.append(
                        RowError(
                            line,
                            f"Unknown position '{record['position']}' "
                            "(add it first or enable auto-create)",
                        )
                    )

            try:
                data = EmployeeCreate(
                    employee_no=record["employee_no"],
                    first_name=record["first_name"],
                    last_name=record["last_name"],
                    work_email=record["work_email"],
                    personal_email=record["personal_email"],
                    phone=record["phone"],
                    date_of_joining=record["date_of_joining"],
                    date_of_birth=record["date_of_birth"],
                    gender=record["gender"],
                    employment_type=record["employment_type"],
                    status=record["status"],
                    department_id=department.id if department else None,
                    position_id=position.id if position else None,
                    location=record["location"] or "Gurugram",
                    pan_masked=record["pan_masked"],
                    bank_masked=record["bank_masked"],
                )
                employee = create_employee(
                    db, org_id=org_id, actor=actor, data=data, request=request
                )
            except PydanticValidationError as exc:
                messages = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                errors.append(RowError(line, messages))
                continue
            except (ConflictError, ValidationError) as exc:
                errors.append(RowError(line, str(exc)))
                continue
            created.append((record, employee))

        if errors:
            raise BulkImportError(errors)

        all_employees = list(
            db.scalars(select(Employee).where(Employee.org_id == org_id)).all()
        )
        by_number = {employee.employee_no.lower(): employee for employee in all_employees}
        by_email = {
            employee.work_email.lower(): employee
            for employee in all_employees
            if employee.work_email
        }
        # A manager may also be referenced by their sign-in email.
        linked_users = list(
            db.scalars(
                select(User).where(
                    User.org_id == org_id, User.employee_id.is_not(None)
                )
            ).all()
        )
        by_user_email = {
            account.email.lower(): by_number[account.employee.employee_no.lower()]
            for account in linked_users
            if account.employee is not None
            and account.employee.employee_no.lower() in by_number
        }

        for record, employee in created:
            reference = record["manager_email"] or record["manager_employee_no"]
            if not reference:
                continue
            target = (
                by_email.get(reference.lower())
                or by_user_email.get(reference.lower())
                or by_number.get(reference.lower())
            )
            line = int(record["_line"])
            if target is None:
                errors.append(RowError(line, f"Manager '{reference}' was not found"))
                continue
            if target.id == employee.id:
                errors.append(RowError(line, "An employee cannot be their own manager"))
                continue
            cursor = target
            seen: set[UUID] = set()
            creates_cycle = False
            while cursor is not None and cursor.manager_id is not None and cursor.id not in seen:
                seen.add(cursor.id)
                if cursor.manager_id == employee.id:
                    creates_cycle = True
                    break
                cursor = db.get(Employee, cursor.manager_id)
            if creates_cycle:
                errors.append(RowError(line, "This manager assignment creates a reporting cycle"))
                continue
            employee.manager_id = target.id
            result.managers_linked += 1

        if errors:
            raise BulkImportError(errors)

        db.flush()
        result.created = len(created)
        audit.record(
            db,
            actor=actor,
            action=AuditAction.CREATE,
            entity_type="employee_import",
            entity_id=str(org_id),
            details={
                "filename": filename[:255],
                "employees": result.created,
                "departments_created": result.departments_created,
                "positions_created": result.positions_created,
                "managers_linked": result.managers_linked,
            },
            request=request,
            org_id=org_id,
        )
    return result


def template_csv() -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=TEMPLATE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerow(TEMPLATE_EXAMPLE)
    return buffer.getvalue()


def template_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Employees"
    sheet.append(TEMPLATE_COLUMNS)
    sheet.append([TEMPLATE_EXAMPLE[column] for column in TEMPLATE_COLUMNS])
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_COLUMNS))}2"
    for index, column in enumerate(TEMPLATE_COLUMNS, start=1):
        width = max(len(column) + 4, 16)
        sheet.column_dimensions[get_column_letter(index)].width = width

    notes = workbook.create_sheet("Instructions")
    notes_rows = [
        ("How to import employees", ""),
        ("1", "Keep the header row in the Employees sheet and add one employee per row."),
        ("2", "Only first_name and last_name are required; employee_no is generated when blank."),
        ("3", "Dates use YYYY-MM-DD. Excel date cells are also accepted."),
        ("4", "employment_type accepts full_time, part_time, contract, intern, consultant."),
        ("5", "status accepts active, probation, on_leave, notice, exited."),
        ("6", "department and position are matched by code or name; unknown values are created."),
        ("7", "manager_email or manager_employee_no links reporting lines after all rows load."),
        ("8", "The whole file is rejected if any row fails validation."),
    ]
    for row in notes_rows:
        notes.append(row)
    notes["A1"].font = Font(bold=True, size=14)
    notes.column_dimensions["A"].width = 8
    notes.column_dimensions["B"].width = 100

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = [
    "ImportResult",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_ROWS",
    "RowError",
    "TEMPLATE_COLUMNS",
    "import_employees",
    "template_csv",
    "template_workbook",
]

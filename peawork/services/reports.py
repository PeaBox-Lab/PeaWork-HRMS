from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from typing import Any, Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import (
    AuditEvent,
    Department,
    Document,
    Employee,
    LeaveBalance,
    LeaveRequest,
    Organization,
    Position,
    User,
)

ILLEGAL_CHARACTERS = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_ALIGNMENT = Alignment(vertical="center", wrap_text=True)
THIN_BORDER = Border(bottom=Side(style="thin", color="BFBFBF"))
MAX_TEXT_LENGTH = 32760
DATE_FORMAT = "yyyy-mm-dd"
DATETIME_FORMAT = "yyyy-mm-dd hh:mm"

EMPLOYEE_HEADERS = [
    "Employee No",
    "First name",
    "Last name",
    "Work email",
    "Personal email",
    "Phone",
    "Date of joining",
    "Date of birth",
    "Gender",
    "Employment type",
    "Status",
    "Department",
    "Position",
    "Manager",
    "Location",
    "PAN (masked)",
    "Bank (masked)",
    "Sample data",
]


def safe_cell(value: Any) -> Any:
    """Return an Excel-safe value, neutralising formula injection."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, (int, float, Decimal, date)):
        return value
    text = ILLEGAL_CHARACTERS.sub("", str(value))[:MAX_TEXT_LENGTH]
    if text.startswith(DANGEROUS_PREFIXES):
        return "'" + text
    return text


def _write_sheet(
    sheet: Worksheet,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    widths: dict[int, int] | None = None,
) -> int:
    sheet.append([safe_cell(header) for header in headers])
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
    sheet.row_dimensions[1].height = 28
    sheet.freeze_panes = "A2"

    count = 0
    for row in rows:
        sheet.append([safe_cell(value) for value in row])
        count += 1

    for index, header in enumerate(headers, start=1):
        if widths and index in widths:
            width = widths[index]
        else:
            longest = len(str(header))
            for value in sheet.iter_cols(
                min_col=index, max_col=index, min_row=2, max_row=min(count + 1, 200)
            ):
                for cell in value:
                    if cell.value is not None:
                        longest = max(longest, len(str(cell.value)))
            width = min(max(longest + 3, 12), 48)
        sheet.column_dimensions[get_column_letter(index)].width = width

    if headers:
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(headers))}{max(count + 1, 2)}"
        )
    return count


def _employees(db: Session, org_id) -> list[Employee]:
    return list(
        db.scalars(
            select(Employee)
            .where(Employee.org_id == org_id)
            .order_by(Employee.employee_no)
        ).all()
    )


def _employee_name(employee: Employee | None) -> str:
    return employee.full_name if employee else ""


def _employee_rows(employees: list[Employee]) -> list[list[Any]]:
    return [
        [
            employee.employee_no,
            employee.first_name,
            employee.last_name,
            employee.work_email,
            employee.personal_email,
            employee.phone,
            employee.date_of_joining,
            employee.date_of_birth,
            employee.gender,
            employee.employment_type.value,
            employee.status.value,
            employee.department.name if employee.department else "",
            employee.position.title if employee.position else "",
            _employee_name(employee.manager),
            employee.location,
            employee.pan_masked,
            employee.bank_masked,
            "Yes" if employee.is_sample else "No",
        ]
        for employee in employees
    ]


def _new_workbook() -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    return workbook


def _finalise(workbook: Workbook) -> bytes:
    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _apply_date_formats(sheet: Worksheet, columns: Sequence[int], fmt: str = DATE_FORMAT) -> None:
    for column in columns:
        for row in sheet.iter_rows(
            min_row=2,
            min_col=column,
            max_col=column,
            max_row=sheet.max_row,
        ):
            for cell in row:
                if isinstance(cell.value, datetime):
                    cell.number_format = DATETIME_FORMAT
                elif isinstance(cell.value, date):
                    cell.number_format = fmt


def employees_workbook(
    db: Session,
    *,
    org: Organization,
    generated_at: datetime | None = None,
) -> bytes:
    """Return a single-sheet employee register workbook."""
    generated_at = generated_at or datetime.now(timezone.utc)
    employees = _employees(db, org.id)
    workbook = _new_workbook()
    sheet = workbook.create_sheet("Employees")
    _write_sheet(
        sheet,
        EMPLOYEE_HEADERS,
        _employee_rows(employees),
        widths={1: 16, 4: 32, 5: 30, 12: 24, 13: 28, 14: 26, 15: 16},
    )
    _apply_date_formats(sheet, [7, 8])
    sheet.oddHeader.center.text = f"{org.name} — Employee register"
    sheet.oddFooter.left.text = (
        f"PeaWork {__version__} · generated {generated_at:%Y-%m-%d %H:%M UTC}"
    )
    return _finalise(workbook)


def full_workbook(
    db: Session,
    *,
    org: Organization,
    generated_at: datetime | None = None,
) -> bytes:
    """Return the complete HR data workbook."""
    generated_at = generated_at or datetime.now(timezone.utc)
    org_id = org.id
    employees = _employees(db, org_id)
    employee_by_id = {employee.id: employee for employee in employees}
    departments = list(
        db.scalars(
            select(Department)
            .where(Department.org_id == org_id)
            .order_by(Department.name)
        ).all()
    )
    positions = list(
        db.scalars(
            select(Position)
            .where(Position.org_id == org_id)
            .order_by(Position.title)
        ).all()
    )
    leave_requests = list(
        db.scalars(
            select(LeaveRequest)
            .where(LeaveRequest.org_id == org_id)
            .order_by(LeaveRequest.start_date.desc())
        ).all()
    )
    balances = list(
        db.scalars(
            select(LeaveBalance)
            .where(LeaveBalance.org_id == org_id)
            .order_by(LeaveBalance.year.desc(), LeaveBalance.employee_id)
        ).all()
    )
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.org_id == org_id)
            .order_by(Document.created_at.desc())
        ).all()
    )
    users = list(
        db.scalars(
            select(User).where(User.org_id == org_id).order_by(User.email)
        ).all()
    )
    audit_events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.org_id == org_id)
            .order_by(AuditEvent.created_at.desc())
        ).all()
    )

    headcount: dict[Any, int] = {}
    for employee in employees:
        headcount[employee.department_id] = headcount.get(employee.department_id, 0) + 1
    incumbents: dict[Any, int] = {}
    for employee in employees:
        if employee.position_id:
            incumbents[employee.position_id] = incumbents.get(employee.position_id, 0) + 1

    workbook = _new_workbook()

    summary = workbook.create_sheet("Summary")
    summary.append(["PeaWork HRMS data export"])
    summary["A1"].font = Font(bold=True, size=16, color="1F4E79")
    summary.append([])
    summary_rows = [
        ("Organisation", org.name),
        ("Legal name", org.legal_name or ""),
        ("Currency", org.currency),
        ("Timezone", org.timezone),
        ("Generated at (UTC)", generated_at.replace(tzinfo=None)),
        ("PeaWork version", __version__),
        ("Employees", len(employees)),
        ("Sample employees", sum(1 for employee in employees if employee.is_sample)),
        ("Departments", len(departments)),
        ("Positions", len(positions)),
        ("Leave requests", len(leave_requests)),
        ("Pending leave requests", sum(1 for item in leave_requests if item.status.value == "pending")),
        ("Leave balances", len(balances)),
        ("Documents", len(documents)),
        ("Users", len(users)),
        ("Audit events", len(audit_events)),
    ]
    for label, value in summary_rows:
        summary.append([label, safe_cell(value)])
        summary.cell(row=summary.max_row, column=1).font = Font(bold=True)
        if label == "Generated at (UTC)":
            summary.cell(row=summary.max_row, column=2).number_format = DATETIME_FORMAT
    summary.column_dimensions["A"].width = 26
    summary.column_dimensions["B"].width = 44
    summary.sheet_view.showGridLines = False

    employee_sheet = workbook.create_sheet("Employees")
    _write_sheet(
        employee_sheet,
        EMPLOYEE_HEADERS,
        _employee_rows(employees),
        widths={1: 16, 4: 32, 5: 30, 12: 24, 13: 28, 14: 26, 15: 16},
    )
    _apply_date_formats(employee_sheet, [7, 8])

    department_sheet = workbook.create_sheet("Departments")
    _write_sheet(
        department_sheet,
        [
            "Name",
            "Code",
            "Parent",
            "Manager",
            "Cost centre",
            "Headcount",
            "Active",
            "Sample data",
        ],
        [
            [
                department.name,
                department.code,
                department.parent.name if department.parent else "",
                _employee_name(employee_by_id.get(department.manager_employee_id)),
                department.cost_center,
                headcount.get(department.id, 0),
                "Yes" if department.active else "No",
                "Yes" if department.is_sample else "No",
            ]
            for department in departments
        ],
        widths={1: 28, 2: 16, 3: 24, 4: 26},
    )

    position_sheet = workbook.create_sheet("Positions")
    _write_sheet(
        position_sheet,
        ["Title", "Code", "Department", "Grade", "Level", "Incumbents", "Active", "Sample data"],
        [
            [
                position.title,
                position.code,
                position.department.name if position.department else "",
                position.grade,
                position.level,
                incumbents.get(position.id, 0),
                "Yes" if position.active else "No",
                "Yes" if position.is_sample else "No",
            ]
            for position in positions
        ],
        widths={1: 32, 3: 24},
    )

    leave_sheet = workbook.create_sheet("Leave Requests")
    _write_sheet(
        leave_sheet,
        [
            "Employee No",
            "Employee",
            "Leave type",
            "Start date",
            "End date",
            "Days",
            "Status",
            "Reason",
            "Approver",
            "Decided at",
            "Created at",
        ],
        [
            [
                request.employee.employee_no if request.employee else "",
                _employee_name(request.employee),
                request.leave_type.name if request.leave_type else "",
                request.start_date,
                request.end_date,
                request.days,
                request.status.value,
                request.reason,
                request.approver.full_name if request.approver else "",
                request.decided_at,
                request.created_at,
            ]
            for request in leave_requests
        ],
        widths={2: 26, 3: 18, 8: 40, 9: 24},
    )
    _apply_date_formats(leave_sheet, [4, 5])
    _apply_date_formats(leave_sheet, [10, 11], DATETIME_FORMAT)

    balance_sheet = workbook.create_sheet("Leave Balances")
    _write_sheet(
        balance_sheet,
        ["Employee No", "Employee", "Leave type", "Year", "Opening", "Accrued", "Used", "Available"],
        [
            [
                employee_by_id[balance.employee_id].employee_no
                if balance.employee_id in employee_by_id
                else "",
                _employee_name(employee_by_id.get(balance.employee_id)),
                balance.leave_type.name if balance.leave_type else "",
                balance.year,
                balance.opening,
                balance.accrued,
                balance.used,
                balance.available,
            ]
            for balance in balances
        ],
        widths={2: 26, 3: 18},
    )

    document_sheet = workbook.create_sheet("Documents")
    _write_sheet(
        document_sheet,
        [
            "Employee",
            "Category",
            "Title",
            "Filename",
            "Content type",
            "Size (bytes)",
            "Checksum (SHA-256)",
            "Expires on",
            "Uploaded at",
        ],
        [
            [
                _employee_name(document.employee),
                document.category.value,
                document.title,
                document.filename,
                document.content_type,
                document.size_bytes,
                document.checksum_sha256,
                document.expires_on,
                document.created_at,
            ]
            for document in documents
        ],
        widths={1: 26, 3: 32, 4: 32, 7: 70},
    )
    _apply_date_formats(document_sheet, [8])
    _apply_date_formats(document_sheet, [9], DATETIME_FORMAT)

    user_sheet = workbook.create_sheet("Users")
    _write_sheet(
        user_sheet,
        [
            "Email",
            "Full name",
            "Role",
            "Active",
            "Employee No",
            "Must change password",
            "Sample data",
            "Last login",
        ],
        [
            [
                user.email,
                user.full_name,
                user.role.value,
                "Yes" if user.active else "No",
                user.employee.employee_no if user.employee else "",
                "Yes" if user.must_change_password else "No",
                "Yes" if user.is_sample else "No",
                user.last_login_at,
            ]
            for user in users
        ],
        widths={1: 34, 2: 28, 6: 20},
    )
    _apply_date_formats(user_sheet, [8], DATETIME_FORMAT)

    audit_sheet = workbook.create_sheet("Audit Log")
    _write_sheet(
        audit_sheet,
        [
            "Created at",
            "Actor",
            "Action",
            "Entity type",
            "Entity ID",
            "Details",
            "IP",
            "Request ID",
        ],
        [
            [
                event.created_at,
                event.actor_email,
                event.action.value,
                event.entity_type,
                event.entity_id,
                json.dumps(event.details, ensure_ascii=False, default=str)
                if event.details
                else "",
                event.ip,
                event.request_id,
            ]
            for event in audit_events
        ],
        widths={1: 20, 2: 30, 5: 38, 6: 60, 8: 24},
    )
    _apply_date_formats(audit_sheet, [1], DATETIME_FORMAT)

    return _finalise(workbook)


__all__ = ["employees_workbook", "full_workbook", "safe_cell"]

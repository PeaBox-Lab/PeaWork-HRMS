from __future__ import annotations

from io import BytesIO
from uuid import UUID

from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from peawork.db import session_scope
from peawork.models import (
    AuditEvent,
    Department,
    Employee,
    LeaveBalance,
    LeaveRequest,
    Organization,
    Position,
    User,
)
from peawork.services.reports import safe_cell

from .helpers import csrf, login_admin, login_employee


def _org_id() -> UUID:
    with session_scope() as db:
        return db.scalar(select(Organization.id))


def _employee_count(*, sample: bool | None = None) -> int:
    with session_scope() as db:
        stmt = select(func.count(Employee.id))
        if sample is not None:
            stmt = stmt.where(Employee.is_sample.is_(sample))
        return int(db.scalar(stmt) or 0)


def _csv(rows: list[str]) -> bytes:
    header = (
        "employee_no,first_name,last_name,work_email,date_of_joining,gender,"
        "employment_type,status,department,position,manager_email,location"
    )
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def _xlsx(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "employee_no",
            "first_name",
            "last_name",
            "work_email",
            "department",
            "position",
            "manager_email",
        ]
    )
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_sample_data_seed_replace_and_clean(client):
    login_admin(client)
    token = csrf(client)

    settings_page = client.get("/admin/settings")
    assert settings_page.status_code == 200
    assert "Sample data" in settings_page.text

    response = client.post("/admin/sample-data", data={"_csrf": token, "count": "100"})
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]

    org_id = _org_id()
    with session_scope() as db:
        sample = list(
            db.scalars(
                select(Employee).where(
                    Employee.org_id == org_id, Employee.is_sample.is_(True)
                )
            ).all()
        )
        assert len(sample) == 100
        assert sum(1 for employee in sample if employee.manager_id is None) == 1
        assert all(employee.department_id for employee in sample)
        assert all(employee.position_id for employee in sample)
        assert len({employee.department.code for employee in sample}) == 13
        assert all(employee.work_email.endswith("@sample.example.com") for employee in sample)
        assert len({employee.work_email for employee in sample}) == 100

        by_id = {employee.id: employee for employee in sample}

        def depth(employee: Employee) -> int:
            levels = 0
            cursor = employee
            while cursor.manager_id in by_id and levels < 20:
                cursor = by_id[cursor.manager_id]
                levels += 1
            return levels

        assert max(depth(employee) for employee in sample) >= 3

        departments = list(
            db.scalars(
                select(Department).where(
                    Department.org_id == org_id, Department.is_sample.is_(True)
                )
            ).all()
        )
        assert len(departments) == 13
        assert all(department.manager_employee_id for department in departments)
        assert len(
            list(
                db.scalars(
                    select(Position).where(
                        Position.org_id == org_id, Position.is_sample.is_(True)
                    )
                ).all()
            )
        ) >= 20
        assert int(
            db.scalar(
                select(func.count(LeaveRequest.id)).where(
                    LeaveRequest.org_id == org_id,
                    LeaveRequest.employee_id.in_([employee.id for employee in sample]),
                )
            )
            or 0
        ) > 0
        assert int(
            db.scalar(
                select(func.count(LeaveBalance.id)).where(
                    LeaveBalance.org_id == org_id,
                    LeaveBalance.employee_id.in_([employee.id for employee in sample]),
                )
            )
            or 0
        ) == 300

    duplicate = client.post("/admin/sample-data", data={"_csrf": token, "count": "100"})
    assert duplicate.status_code == 303
    assert "error=" in duplicate.headers["location"]

    replaced = client.post(
        "/admin/sample-data",
        data={"_csrf": token, "count": "100", "replace": "1"},
    )
    assert replaced.status_code == 303
    assert _employee_count(sample=True) == 100

    cleaned = client.post("/admin/sample-data/clean", data={"_csrf": token})
    assert cleaned.status_code == 303
    assert _employee_count(sample=True) == 0
    with session_scope() as db:
        assert int(db.scalar(select(func.count(Employee.id))) or 0) == 2
        assert int(
            db.scalar(
                select(func.count(Department.id)).where(Department.is_sample.is_(True))
            )
            or 0
        ) == 0
        assert int(
            db.scalar(
                select(func.count(Position.id)).where(Position.is_sample.is_(True))
            )
            or 0
        ) == 0
        assert db.scalar(select(User.id).where(User.email == "manager@example.com"))
        assert db.scalar(select(User.id).where(User.email == "employee@example.com"))


def test_bulk_import_csv_with_managers_and_auto_create(client):
    login_admin(client)
    token = csrf(client)
    before = _employee_count()
    payload = _csv(
        [
            "IMP-2001,Ira,Kapoor,ira.kapoor@example.com,2026-01-05,female,full_time,active,"
            "Research,Research Scientist,manager@example.com,Bengaluru",
            "IMP-2002,Dev,Patel,dev.patel@example.com,2026-02-02,male,full_time,active,"
            "Research,Research Scientist,ira.kapoor@example.com,Pune",
        ]
    )
    response = client.post(
        "/admin/import",
        data={"_csrf": token, "auto_create": "1"},
        files={"file": ("people.csv", payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert "Imported" in response.text
    assert _employee_count() == before + 2

    with session_scope() as db:
        ira = db.scalar(select(Employee).where(Employee.work_email == "ira.kapoor@example.com"))
        dev = db.scalar(select(Employee).where(Employee.work_email == "dev.patel@example.com"))
        manager = db.scalar(
            select(Employee).where(Employee.work_email == "aarav.sharma@example.com")
        )
        assert ira is not None and dev is not None and manager is not None
        assert ira.employee_no == "IMP-2001"
        assert ira.manager_id == manager.id
        assert dev.manager_id == ira.id
        assert ira.department is not None and ira.department.name == "Research"
        assert ira.position is not None and ira.position.title == "Research Scientist"
        assert ira.department_id == dev.department_id
        assert (
            db.scalar(
                select(AuditEvent.id).where(AuditEvent.entity_type == "employee_import")
            )
            is not None
        )


def test_bulk_import_xlsx(client):
    login_admin(client)
    token = csrf(client)
    before = _employee_count()
    payload = _xlsx(
        [
            ["IMP-3001", "Meera", "Iyer", "meera.iyer@example.com", "Engineering", "Engineer", ""],
        ]
    )
    response = client.post(
        "/admin/import",
        data={"_csrf": token, "auto_create": "1"},
        files={
            "file": (
                "people.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    assert "Imported" in response.text
    assert _employee_count() == before + 1


def test_bulk_import_rolls_back_every_row_on_error(client):
    login_admin(client)
    token = csrf(client)
    before = _employee_count()
    payload = _csv(
        [
            "IMP-4001,Good,Row,good.row@example.com,2026-03-02,female,full_time,active,"
            "Engineering,Engineer,,Gurugram",
            "IMP-4002,Bad,Row,bad.row@example.com,not-a-date,female,full_time,active,"
            "Engineering,Engineer,,Gurugram",
        ]
    )
    response = client.post(
        "/admin/import",
        data={"_csrf": token, "auto_create": "1"},
        files={"file": ("people.csv", payload, "text/csv")},
    )
    assert response.status_code == 422
    assert "nothing was imported" in response.text
    assert _employee_count() == before
    with session_scope() as db:
        assert (
            db.scalar(
                select(Employee.id).where(Employee.work_email == "good.row@example.com")
            )
            is None
        )


def test_bulk_import_unknown_manager_rolls_back(client):
    login_admin(client)
    token = csrf(client)
    before = _employee_count()
    payload = _csv(
        [
            "IMP-5001,No,Manager,no.manager@example.com,2026-04-01,female,full_time,active,"
            "Engineering,Engineer,missing.person@example.com,Gurugram",
        ]
    )
    response = client.post(
        "/admin/import",
        data={"_csrf": token, "auto_create": "1"},
        files={"file": ("people.csv", payload, "text/csv")},
    )
    assert response.status_code == 422
    assert "was not found" in response.text
    assert _employee_count() == before


def test_import_templates_are_valid(client):
    login_admin(client)

    csv_response = client.get("/admin/import/template.csv")
    assert csv_response.status_code == 200
    assert csv_response.text.startswith("employee_no,first_name,last_name")
    assert "text/csv" in csv_response.headers["content-type"]

    xlsx_response = client.get("/admin/import/template.xlsx")
    assert xlsx_response.status_code == 200
    workbook = load_workbook(BytesIO(xlsx_response.content))
    assert workbook.sheetnames == ["Employees", "Instructions"]
    assert [cell.value for cell in workbook["Employees"][1]][:3] == [
        "employee_no",
        "first_name",
        "last_name",
    ]


def test_excel_reports_include_all_sheets(client):
    login_admin(client)
    page = client.get("/admin/reports")
    assert page.status_code == 200
    assert "Full organisation report" in page.text

    employee_response = client.get("/admin/reports/employees.xlsx")
    assert employee_response.status_code == 200
    assert "spreadsheetml.sheet" in employee_response.headers["content-type"]
    assert "peawork-employees.xlsx" in employee_response.headers["content-disposition"]
    workbook = load_workbook(BytesIO(employee_response.content))
    sheet = workbook["Employees"]
    assert sheet["A1"].value == "Employee No"
    assert sheet.max_row == _employee_count() + 1

    full_response = client.get("/admin/reports/full.xlsx")
    assert full_response.status_code == 200
    full = load_workbook(BytesIO(full_response.content))
    assert full.sheetnames == [
        "Summary",
        "Employees",
        "Departments",
        "Positions",
        "Leave Requests",
        "Leave Balances",
        "Documents",
        "Users",
        "Audit Log",
    ]
    assert full["Summary"]["A1"].value == "PeaWork HRMS data export"

    with session_scope() as db:
        downloads = int(
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.entity_type == "report"
                )
            )
            or 0
        )
        assert downloads == 2


def test_excel_formula_injection_is_neutralised():
    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert safe_cell("-2+3") == "'-2+3"
    assert safe_cell("@cmd") == "'@cmd"
    assert safe_cell(5) == 5
    assert safe_cell(-5) == -5
    assert safe_cell("Normal text") == "Normal text"


def test_data_tools_require_privileged_roles(client):
    login_employee(client)
    assert client.get("/admin/import").status_code == 403
    assert client.get("/admin/reports").status_code == 403
    assert client.get("/admin/reports/employees.xlsx").status_code == 403
    token = csrf(client)
    assert (
        client.post("/admin/sample-data", data={"_csrf": token, "count": "100"}).status_code
        == 403
    )
    assert (
        client.post("/admin/sample-data/clean", data={"_csrf": token}).status_code == 403
    )

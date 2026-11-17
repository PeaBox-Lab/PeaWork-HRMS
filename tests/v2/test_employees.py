from __future__ import annotations

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import AuditEvent, Employee
from tests.v2.helpers import api_headers, csrf, login_admin, login_employee


def _manager_id() -> str:
    with session_scope() as db:
        employee = db.scalar(
            select(Employee).where(Employee.work_email == "aarav.sharma@example.com")
        )
        assert employee is not None
        return str(employee.id)


def test_employee_api_create_read_update_and_audit(client):
    login_admin(client)
    headers = api_headers(client)

    payload = {
        "first_name": "Nisha",
        "last_name": "Rao",
        "work_email": "nisha.rao@example.com",
        "personal_email": "nisha@example.net",
        "phone": "+91 98765 43210",
        "date_of_joining": "2025-01-06",
        "employment_type": "full_time",
        "status": "active",
        "location": "Pune",
        "pan_masked": "XXXXX1234X",
    }
    created = client.post("/api/v1/employees", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["employee_no"].startswith("EMP-")
    assert body["first_name"] == "Nisha"
    employee_id = body["id"]

    listing = client.get("/api/v1/employees?search=Nisha")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = client.get(f"/api/v1/employees/{employee_id}")
    assert detail.status_code == 200
    assert detail.json()["location"] == "Pune"

    updated = client.patch(
        f"/api/v1/employees/{employee_id}",
        json={"location": "Bengaluru", "status": "probation"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["location"] == "Bengaluru"
    assert updated.json()["status"] == "probation"

    page = client.get(f"/employees/{employee_id}")
    assert page.status_code == 200
    assert "Nisha Rao" in page.text
    assert "Employee history" in page.text

    with session_scope() as db:
        actions = [
            event.action.value
            for event in db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "employee",
                    AuditEvent.entity_id == employee_id,
                )
            ).all()
        ]
    assert "create" in actions
    assert "update" in actions


def test_employee_form_post_preserves_request_body(client):
    """Regression: v1 CSRF middleware consumed the body and broke form POSTs."""
    login_admin(client)
    response = client.post(
        "/employees/new",
        data={
            "_csrf": csrf(client),
            "employee_no": "EMP-9001",
            "first_name": "Form",
            "last_name": "Tester",
            "work_email": "form.tester@example.com",
            "date_of_joining": "2026-01-05",
            "employment_type": "full_time",
            "status": "active",
            "location": "Gurugram",
        },
    )
    assert response.status_code == 303, response.text
    assert "/employees/" in response.headers["location"]

    listing = client.get("/employees?search=Form+Tester")
    assert listing.status_code == 200
    assert "Form Tester" in listing.text
    assert "EMP-9001" in listing.text


def test_duplicate_employee_number_is_rejected(client):
    login_admin(client)
    payload = {
        "first_name": "First",
        "last_name": "Person",
        "employee_no": "EMP-0001",
        "employment_type": "full_time",
        "status": "active",
    }
    response = client.post("/api/v1/employees", json=payload, headers=api_headers(client))
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "already exists" in response.json()["detail"]


def test_employee_scope_and_object_level_authorisation(client, second_client):
    login_employee(second_client)

    listing = second_client.get("/api/v1/employees")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["work_email"] == "diya.nair@example.com"

    forbidden = second_client.get(f"/api/v1/employees/{_manager_id()}")
    assert forbidden.status_code == 403
    assert forbidden.headers["content-type"].startswith("application/problem+json")

    page = second_client.get(f"/employees/{_manager_id()}")
    assert page.status_code == 403
    assert "Something needs your attention" in page.text

    login_admin(client)
    admin_view = client.get("/api/v1/employees")
    assert admin_view.json()["total"] >= 2


def test_employee_cannot_create_or_edit_people(client):
    login_employee(client)
    response = client.post(
        "/api/v1/employees",
        json={
            "first_name": "Unauthorised",
            "last_name": "Hire",
            "employment_type": "full_time",
            "status": "active",
        },
        headers=api_headers(client),
    )
    assert response.status_code == 403

    page = client.get("/employees/new")
    assert page.status_code == 403


def test_employee_pages_render(client):
    login_admin(client)
    for path in ("/", "/employees", "/employees/new", "/organization", "/documents", "/profile"):
        response = client.get(path)
        assert response.status_code == 200, path

    with session_scope() as db:
        employee = db.scalar(select(Employee).order_by(Employee.employee_no))
        assert employee is not None
        employee_id = employee.id

    for path in (f"/employees/{employee_id}", f"/employees/{employee_id}/edit"):
        response = client.get(path)
        assert response.status_code == 200, path

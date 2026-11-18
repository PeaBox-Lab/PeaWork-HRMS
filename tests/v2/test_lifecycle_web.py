"""Lifecycle web UI tests (server-rendered queue, detail and workflow actions)."""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import ChangeRequest, Employee, User
from peawork.security import hash_password
from tests.v2.helpers import (
    EMPLOYEE_EMAIL,
    api_headers,
    csrf,
    login,
    login_admin,
    login_employee,
)

AUDITOR_EMAIL = "auditor@test.local"
AUDITOR_PASSWORD = "AuditorTest123!"


def _first_employee() -> Employee:
    with session_scope() as db:
        employee = db.scalar(select(Employee).order_by(Employee.employee_no))
        assert employee is not None
        return employee


def _employee_user() -> User:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == EMPLOYEE_EMAIL))
        assert user is not None and user.employee_id is not None
        return user


def _form(client, **values) -> dict[str, str]:
    data = {"_csrf": csrf(client)}
    data.update({key: str(value) for key, value in values.items()})
    return data


def _create_via_api(client, code: str, change_type: str, **extra) -> dict:
    payload = {"code": code, "change_type": change_type}
    payload.update(extra)
    response = client.post("/api/v1/lifecycle", json=payload, headers=api_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.v2
def test_lifecycle_page_requires_login(client):
    """Anonymous visitors are redirected to the login page."""
    response = client.get("/lifecycle")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.v2
def test_lifecycle_page_lists_requests(client):
    """The queue renders requests visible to the signed-in user."""
    login_admin(client)
    _create_via_api(client, "LC-WEB-1", "promotion", description="Promote to lead")

    response = client.get("/lifecycle")
    assert response.status_code == 200
    assert "Lifecycle change requests" in response.text
    assert "LC-WEB-1" in response.text
    assert "Promote to lead" in response.text


@pytest.mark.v2
def test_lifecycle_page_filters_by_status(client):
    """The status filter narrows the table."""
    login_admin(client)
    _create_via_api(client, "LC-WEB-F1", "promotion")
    approved = _create_via_api(client, "LC-WEB-F2", "promotion")
    assert (
        client.post(
            f"/api/v1/lifecycle/{approved['id']}/approve",
            json={},
            headers=api_headers(client),
        ).status_code
        == 200
    )

    response = client.get("/lifecycle?status=draft")
    assert response.status_code == 200
    assert "LC-WEB-F1" in response.text
    assert "LC-WEB-F2" not in response.text


@pytest.mark.v2
def test_lifecycle_web_create(client):
    """POST /lifecycle/new creates a draft with a generated code."""
    login_admin(client)
    employee = _first_employee()

    response = client.post(
        "/lifecycle/new",
        data=_form(
            client,
            employee_id=employee.id,
            change_type="location_change",
            effective_date="2026-12-01",
            description="Relocate to Pune office",
            location="Pune",
            reason="Team consolidation",
        ),
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/lifecycle/")

    cr_id = location.split("/")[2].split("?")[0]
    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(cr_id))
        assert cr is not None
        assert cr.code.startswith("CR-")
        assert cr.status.value == "draft"
        assert cr.details == {"location": "Pune"}
        assert str(cr.employee_id) == str(employee.id)


@pytest.mark.v2
def test_lifecycle_web_create_requires_type(client):
    """Missing change type re-renders the form with an error."""
    login_admin(client)
    employee = _first_employee()
    response = client.post(
        "/lifecycle/new",
        data=_form(client, employee_id=employee.id, change_type=""),
    )
    assert response.status_code == 200
    assert "Choose a change type" in response.text


@pytest.mark.v2
def test_lifecycle_web_new_page(client):
    """GET /lifecycle/new renders the create form for HR."""
    login_admin(client)
    response = client.get("/lifecycle/new")
    assert response.status_code == 200
    assert "Raise a change request" in response.text
    assert "Change type" in response.text


@pytest.mark.v2
def test_lifecycle_detail_page(client):
    """The detail page shows the request, employee and workflow."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client,
        "LC-WEB-2",
        "promotion",
        employee_id=str(employee.id),
        description="Promotion review",
    )

    response = client.get(f"/lifecycle/{created['id']}")
    assert response.status_code == 200
    assert "LC-WEB-2" in response.text
    assert employee.full_name in response.text
    assert "Submit for approval" in response.text


@pytest.mark.v2
def test_lifecycle_web_submit_and_approve(client):
    """Draft → pending_approval → approved through the UI."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client, "LC-WEB-3", "manager_change", employee_id=str(employee.id)
    )

    response = client.post(
        f"/lifecycle/{created['id']}/submit", data=_form(client)
    )
    assert response.status_code == 303
    assert "Submitted" in response.headers["location"]

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None and cr.status.value == "pending_approval"

    response = client.post(
        f"/lifecycle/{created['id']}/approve", data=_form(client)
    )
    assert response.status_code == 303
    assert "approved" in response.headers["location"]

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None and cr.status.value == "approved"


@pytest.mark.v2
def test_lifecycle_web_reject_records_reason(client):
    """Rejecting from the detail page stores the reason."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client, "LC-WEB-4", "promotion", employee_id=str(employee.id)
    )
    assert (
        client.post(
            f"/api/v1/lifecycle/{created['id']}/submit",
            json={},
            headers=api_headers(client),
        ).status_code
        == 200
    )

    response = client.post(
        f"/lifecycle/{created['id']}/reject",
        data=_form(client, reason="Budget not approved"),
    )
    assert response.status_code == 303

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None
        assert cr.status.value == "rejected"
        assert cr.reason == "Budget not approved"


@pytest.mark.v2
def test_lifecycle_web_cancel(client):
    """A draft can be withdrawn from the detail page."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client, "LC-WEB-5", "location_change", employee_id=str(employee.id)
    )

    response = client.post(f"/lifecycle/{created['id']}/cancel", data=_form(client))
    assert response.status_code == 303

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None and cr.status.value == "cancelled"


@pytest.mark.v2
def test_lifecycle_web_edit_draft(client):
    """The edit form updates editable draft fields."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client,
        "LC-WEB-6",
        "promotion",
        employee_id=str(employee.id),
        description="Before edit",
    )

    page = client.get(f"/lifecycle/{created['id']}/edit")
    assert page.status_code == 200
    assert "Before edit" in page.text

    response = client.post(
        f"/lifecycle/{created['id']}/edit",
        data=_form(
            client,
            employee_id=employee.id,
            effective_date="2026-12-15",
            description="After edit",
            location="Mumbai",
        ),
    )
    assert response.status_code == 303, response.text

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None
        assert cr.description == "After edit"
        assert cr.effective_date.isoformat() == "2026-12-15"
        assert cr.details == {"location": "Mumbai"}


@pytest.mark.v2
def test_lifecycle_web_complete_applies_change(client):
    """Completing from the UI applies target values and writes history."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client,
        "LC-WEB-7",
        "location_change",
        employee_id=str(employee.id),
        details={"location": "Hyderabad"},
    )
    assert (
        client.post(
            f"/api/v1/lifecycle/{created['id']}/submit",
            json={},
            headers=api_headers(client),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/lifecycle/{created['id']}/approve",
            json={},
            headers=api_headers(client),
        ).status_code
        == 200
    )

    detail = client.get(f"/lifecycle/{created['id']}")
    assert detail.status_code == 200
    assert "Apply to employee record" in detail.text

    response = client.post(
        f"/lifecycle/{created['id']}/complete",
        data=_form(client, location="Kochi"),
    )
    assert response.status_code == 303, response.text

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        employee_row = db.get(Employee, employee.id)
        assert cr is not None and cr.status.value == "completed"
        assert employee_row is not None and employee_row.location == "Kochi"


@pytest.mark.v2
def test_employee_can_raise_own_request(client):
    """Self-service: the form defaults the target to the caller's record."""
    login_employee(client)
    user = _employee_user()

    response = client.post(
        "/lifecycle/new",
        data=_form(client, change_type="location_change", location="Remote"),
    )
    assert response.status_code == 303
    assert "/lifecycle/" in response.headers["location"]

    with session_scope() as db:
        cr = db.scalar(
            select(ChangeRequest).order_by(ChangeRequest.created_at.desc())
        )
        assert cr is not None
        assert cr.employee_id == user.employee_id


@pytest.mark.v2
def test_employee_cannot_raise_for_colleague(client):
    """Self-service cannot target another employee."""
    login_employee(client)
    user = _employee_user()
    with session_scope() as db:
        colleague = db.scalar(
            select(Employee).where(Employee.id != user.employee_id)
        )
        assert colleague is not None

    response = client.post(
        "/lifecycle/new",
        data=_form(
            client, employee_id=colleague.id, change_type="location_change"
        ),
    )
    assert response.status_code == 200
    assert "direct reports" in response.text

    with session_scope() as db:
        assert (
            db.scalar(select(ChangeRequest).where(ChangeRequest.employee_id == colleague.id))
            is None
        )


@pytest.mark.v2
def test_employee_cannot_approve(client, second_client):
    """Approval is HR-only."""
    login_admin(client)
    employee = _first_employee()
    created = _create_via_api(
        client, "LC-WEB-8", "promotion", employee_id=str(employee.id)
    )
    assert (
        client.post(
            f"/api/v1/lifecycle/{created['id']}/submit",
            json={},
            headers=api_headers(client),
        ).status_code
        == 200
    )

    login_employee(second_client)
    response = second_client.post(
        f"/lifecycle/{created['id']}/approve", data=_form(second_client)
    )
    assert response.status_code == 403


@pytest.mark.v2
def test_employee_queue_only_shows_own_requests(client, second_client):
    """Employees do not see colleagues' change requests."""
    login_admin(client)
    employee_user = _employee_user()
    with session_scope() as db:
        colleague = db.scalar(
            select(Employee).where(Employee.id != employee_user.employee_id)
        )
        assert colleague is not None

    _create_via_api(
        client, "LC-WEB-9", "promotion", employee_id=str(colleague.id)
    )
    _create_via_api(
        client, "LC-WEB-10", "other", employee_id=str(employee_user.employee_id)
    )

    login_employee(second_client)
    response = second_client.get("/lifecycle")
    assert response.status_code == 200
    assert "LC-WEB-10" in response.text
    assert "LC-WEB-9" not in response.text


@pytest.mark.v2
def test_read_only_role_has_no_write_actions(client, second_client):
    """Auditor/payroll roles see the queue but cannot create or act."""
    login_admin(client)
    created = _create_via_api(
        client, "LC-WEB-11", "promotion", employee_id=str(_first_employee().id)
    )

    with session_scope() as db:
        admin = db.scalar(select(User).where(User.email == "admin@test.local"))
        assert admin is not None
        db.add(
            User(
                org_id=admin.org_id,
                email=AUDITOR_EMAIL,
                full_name="Audit Reader",
                password_hash=hash_password(AUDITOR_PASSWORD),
                role="auditor",
            )
        )

    login(second_client, AUDITOR_EMAIL, AUDITOR_PASSWORD)

    page = second_client.get("/lifecycle")
    assert page.status_code == 200
    assert "LC-WEB-11" in page.text
    assert "Read-only access" in page.text
    assert "Create a change request" not in page.text

    new_page = second_client.get("/lifecycle/new")
    assert new_page.status_code == 403
    assert "cannot raise lifecycle change requests" in new_page.text

    action = second_client.post(
        f"/lifecycle/{created['id']}/submit", data=_form(second_client)
    )
    assert action.status_code == 303
    assert "error=" in action.headers["location"]

    with session_scope() as db:
        cr = db.get(ChangeRequest, UUID(created["id"]))
        assert cr is not None and cr.status.value == "draft"

"""R5 Employee lifecycle change-request tests.

Pattern mirrors R2/R2 test style: API-first, DB-backed, role-scoped,
error cases, bootstrap fixture per test.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from peawork.db import session_scope
from peawork.domain import EmployeeStatus
from peawork.models import Employee, EmployeeHistory, User
from tests.v2.helpers import (
    EMPLOYEE_EMAIL,
    MANAGER_EMAIL,
    api_headers,
    login_admin,
    login_employee,
    login_manager,
)


# --- Lifecycle Change Requests ---


@pytest.mark.v2
def test_lifecycle_create(client):
    """POST /api/v1/lifecycle → 201, returns change request dict."""
    login_admin(client)
    r = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-001",
            "change_type": "promotion",
            "description": "Promotion to senior role",
            "old_value": "associate",
            "new_value": "senior associate",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "LC-001"
    assert body["change_type"] == "promotion"
    assert body["status"] == "draft"


@pytest.mark.v2
def test_lifecycle_create_duplicate_code(client):
    """Duplicate code → 409."""
    login_admin(client)
    client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-DUP",
            "change_type": "resignation",
            "description": "Resignation request",
        },
        headers=api_headers(client),
    )
    r = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-DUP",
            "change_type": "resignation",
            "description": "Duplicate resignation",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 409


@pytest.mark.v2
def test_lifecycle_list(client):
    """GET /api/v1/lifecycle → 200, returns list."""
    login_admin(client)
    client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-LIST-001",
            "change_type": "promotion",
            "description": "Promotion request",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/lifecycle", headers=api_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.v2
def test_lifecycle_get(client):
    """GET /api/v1/lifecycle/{id} → 200."""
    login_admin(client)
    create_r = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-GET-001",
            "change_type": "manager_change",
            "description": "Change manager",
        },
        headers=api_headers(client),
    )
    cr_id = create_r.json()["id"]
    r = client.get(f"/api/v1/lifecycle/{cr_id}", headers=api_headers(client))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == cr_id
    assert body["code"] == "LC-GET-001"


@pytest.mark.v2
def test_lifecycle_list_by_status(client):
    """Filter list by status query parameter."""
    login_admin(client)
    client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-FILT-001",
            "change_type": "resignation",
            "description": "Resignation draft",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/lifecycle?status=draft", headers=api_headers(client))
    assert r.status_code == 200
    bodies = r.json()
    assert any(b["code"] == "LC-FILT-001" for b in bodies)


@pytest.mark.v2
def test_lifecycle_create_missing_code(client):
    """Missing required code → 422."""
    login_admin(client)
    r = client.post(
        "/api/v1/lifecycle",
        json={"change_type": "promotion"},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_create_missing_change_type(client):
    """Missing change_type → 422."""
    login_admin(client)
    r = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-ERR-001"},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_approve_reject_cancel_flow(client):
    """approve → reject → cancel workflow."""
    login_admin(client)
    create_r = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-WF-001",
            "change_type": "contract_change",
            "description": "Contract extension",
        },
        headers=api_headers(client),
    )
    cr_id = create_r.json()["id"]

    # Approve
    r = client.post(f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client))
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Reject (rejecting already-approved is allowed per current impl)
    r = client.post(f"/api/v1/lifecycle/{cr_id}/reject", json={"reason": "Insufficient notice"}, headers=api_headers(client))
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Create new one and cancel
    create_r2 = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-WF-002",
            "change_type": "location_change",
            "description": "Office relocation",
        },
        headers=api_headers(client),
    )
    cr2_id = create_r2.json()["id"]
    r = client.post(f"/api/v1/lifecycle/{cr2_id}/cancel", json={}, headers=api_headers(client))
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


@pytest.mark.v2
def test_lifecycle_role_scoping(client, second_client):
    """Admin sees all lifecycle requests; manager sees scoped."""
    login_admin(client)
    client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-SCOPE-1", "change_type": "promotion"},
        headers=api_headers(client),
    )
    r = client.get("/api/v1/lifecycle", headers=api_headers(client))
    assert r.status_code == 200

    login_manager(second_client)
    r = second_client.get("/api/v1/lifecycle", headers=api_headers(second_client))
    assert r.status_code == 200


@pytest.mark.v2
def test_lifecycle_valid_change_types(client):
    """All lifecycle change types accepted."""
    login_admin(client)
    types = [
        "resignation",
        "promotion",
        "location_change",
        "manager_change",
        "employment_type_change",
        "contract_change",
        "rehire",
        "group_company_transfer",
        "country_transfer",
        "legal_entity_change",
        "project_assignment",
        "cost_center_change",
        "other",
    ]
    for idx, ct in enumerate(types):
        r = client.post(
            "/api/v1/lifecycle",
            json={"code": f"LC-CT-{idx:02d}", "change_type": ct},
            headers=api_headers(client),
        )
        assert r.status_code == 201, f"{ct}: {r.text}"


# --- Draft editing, submission, and completion ---


def _active_employee_id() -> tuple[str, str | None]:
    with session_scope() as db:
        employee = db.scalar(select(Employee).order_by(Employee.employee_no))
        assert employee is not None
        return str(employee.id), employee.location


@pytest.mark.v2
def test_lifecycle_update_draft(client):
    """PATCH /lifecycle/{id} updates editable fields on a draft."""
    login_admin(client)
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-UPD-1",
            "change_type": "promotion",
            "description": "old description",
        },
        headers=api_headers(client),
    )
    assert created.status_code == 201
    cr_id = created.json()["id"]

    r = client.patch(
        f"/api/v1/lifecycle/{cr_id}",
        json={"description": "new description", "notes": "edited"},
        headers=api_headers(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "new description"
    assert body["notes"] == "edited"
    assert body["status"] == "draft"


@pytest.mark.v2
def test_lifecycle_update_rejects_unknown_field(client):
    """PATCH with a non-editable field → 422."""
    login_admin(client)
    created = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-UPD-2", "change_type": "promotion"},
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    r = client.patch(
        f"/api/v1/lifecycle/{cr_id}",
        json={"code": "CANNOT-EDIT"},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_update_rejected_after_approval(client):
    """Approved requests are immutable → 422."""
    login_admin(client)
    created = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-UPD-3", "change_type": "promotion"},
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )
    r = client.patch(
        f"/api/v1/lifecycle/{cr_id}",
        json={"description": "too late"},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_submit_requires_employee(client):
    """Draft without an employee cannot be submitted → 422."""
    login_admin(client)
    created = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-SUB-1", "change_type": "promotion"},
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/submit", json={}, headers=api_headers(client)
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_submit_and_approve_flow(client):
    """draft → pending_approval → approved for a linked employee."""
    login_admin(client)
    employee_id, _ = _active_employee_id()
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-SUB-2",
            "change_type": "promotion",
            "employee_id": employee_id,
        },
        headers=api_headers(client),
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]

    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/submit", json={}, headers=api_headers(client)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_approval"

    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


@pytest.mark.v2
def test_lifecycle_complete_requires_approval(client):
    """Completing a draft request → 422."""
    login_admin(client)
    employee_id, _ = _active_employee_id()
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-CMP-1",
            "change_type": "location_change",
            "employee_id": employee_id,
            "details": {"location": "Pune"},
        },
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/complete", json={}, headers=api_headers(client)
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_complete_applies_location_change(client):
    """Approved request mutates the employee and writes history on completion."""
    login_admin(client)
    employee_id, original_location = _active_employee_id()
    new_location = "Pune" if original_location != "Pune" else "Bengaluru"

    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-CMP-2",
            "change_type": "location_change",
            "employee_id": employee_id,
            "description": "Office relocation",
            "details": {"location": new_location},
        },
        headers=api_headers(client),
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]

    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/submit", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )

    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/complete", json={}, headers=api_headers(client)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["details"]["location"] == new_location

    with session_scope() as db:
        employee = db.get(Employee, UUID(employee_id))
        assert employee is not None
        assert employee.location == new_location
        history = list(
            db.scalars(
                select(EmployeeHistory).where(
                    EmployeeHistory.employee_id == UUID(employee_id),
                    EmployeeHistory.change_type == "location_change",
                )
            ).all()
        )
        assert history, "expected an EmployeeHistory row for the applied change"
        assert history[-1].new_value == new_location


@pytest.mark.v2
def test_lifecycle_complete_rejects_unknown_field(client):
    """Completion details may only target writable employee columns → 422."""
    login_admin(client)
    employee_id, _ = _active_employee_id()
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-CMP-3",
            "change_type": "other",
            "employee_id": employee_id,
        },
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/complete",
        json={"details": {"salary": 999999}},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_lifecycle_complete_resignation_defaults_to_notice(client):
    """Resignation without explicit details moves an active employee to notice."""
    login_admin(client)
    with session_scope() as db:
        employee = db.scalar(
            select(Employee).where(Employee.status == "active").order_by(Employee.employee_no)
        )
        assert employee is not None
        employee_id = str(employee.id)

    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-CMP-4",
            "change_type": "resignation",
            "employee_id": employee_id,
        },
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/complete", json={}, headers=api_headers(client)
    )
    assert r.status_code == 200, r.text

    with session_scope() as db:
        employee = db.get(Employee, UUID(employee_id))
        assert employee is not None
        assert employee.status.value == "notice"


@pytest.mark.v2
def test_lifecycle_complete_unlinked_request_rejected(client):
    """An approved request with no employee cannot be completed → 422."""
    login_admin(client)
    created = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-CMP-5", "change_type": "other"},
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/v1/lifecycle/{cr_id}/complete", json={}, headers=api_headers(client)
    )
    assert r.status_code == 422


# --- Role-aware authorization ---


def _user_and_employee(email: str):
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None and user.employee_id is not None
        employee = db.get(Employee, user.employee_id)
        assert employee is not None
        return user.id, employee.id


def _colleague_id(exclude: UUID) -> str:
    with session_scope() as db:
        employee = db.scalar(
            select(Employee).where(Employee.id != exclude).order_by(Employee.employee_no)
        )
        assert employee is not None
        return str(employee.id)


@pytest.mark.v2
def test_lifecycle_employee_creates_own_request(client):
    """Self-service creation defaults the employee link to the caller."""
    login_employee(client)
    _, employee_id = _user_and_employee(EMPLOYEE_EMAIL)

    r = client.post(
        "/api/v1/lifecycle",
        json={"code": "LC-AUTH-1", "change_type": "location_change"},
        headers=api_headers(client),
    )
    assert r.status_code == 201, r.text
    assert r.json()["employee_id"] == str(employee_id)


@pytest.mark.v2
def test_lifecycle_employee_cannot_create_for_colleague(client):
    """Self-service creation cannot target another employee, expected 403."""
    login_employee(client)
    _, employee_id = _user_and_employee(EMPLOYEE_EMAIL)
    colleague = _colleague_id(employee_id)

    r = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-AUTH-2",
            "change_type": "location_change",
            "employee_id": colleague,
        },
        headers=api_headers(client),
    )
    assert r.status_code == 403


@pytest.mark.v2
def test_lifecycle_employee_cannot_approve(client, second_client):
    """Employees cannot approve change requests, expected 403."""
    login_admin(client)
    employee_id, _ = _active_employee_id()
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-AUTH-3",
            "change_type": "promotion",
            "employee_id": employee_id,
        },
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/submit", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )

    login_employee(second_client)
    r = second_client.post(
        f"/api/v1/lifecycle/{cr_id}/approve",
        json={},
        headers=api_headers(second_client),
    )
    assert r.status_code == 403


@pytest.mark.v2
def test_lifecycle_employee_cannot_apply_hr_field(client, second_client):
    """Employees cannot apply HR-only fields on their own request, expected 403."""
    login_employee(second_client)
    _, employee_id = _user_and_employee(EMPLOYEE_EMAIL)
    created = second_client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-AUTH-4",
            "change_type": "resignation",
            "details": {"status": "notice"},
        },
        headers=api_headers(second_client),
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]
    assert (
        second_client.post(
            f"/api/v1/lifecycle/{cr_id}/submit",
            json={},
            headers=api_headers(second_client),
        ).status_code
        == 200
    )

    login_admin(client)
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )

    r = second_client.post(
        f"/api/v1/lifecycle/{cr_id}/complete",
        json={},
        headers=api_headers(second_client),
    )
    assert r.status_code == 403

    with session_scope() as db:
        employee = db.get(Employee, employee_id)
        assert employee is not None
        assert employee.status.value != "notice"


@pytest.mark.v2
def test_lifecycle_employee_can_complete_self_service_field(client, second_client):
    """Self-service fields may be completed by the employee themselves."""
    login_employee(second_client)
    _, employee_id = _user_and_employee(EMPLOYEE_EMAIL)
    created = second_client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-AUTH-5",
            "change_type": "location_change",
            "details": {"location": "Remote"},
        },
        headers=api_headers(second_client),
    )
    cr_id = created.json()["id"]
    assert (
        second_client.post(
            f"/api/v1/lifecycle/{cr_id}/submit",
            json={},
            headers=api_headers(second_client),
        ).status_code
        == 200
    )

    login_admin(client)
    assert (
        client.post(
            f"/api/v1/lifecycle/{cr_id}/approve", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )

    r = second_client.post(
        f"/api/v1/lifecycle/{cr_id}/complete",
        json={"details": {"location": "Remote"}},
        headers=api_headers(second_client),
    )
    assert r.status_code == 200, r.text

    with session_scope() as db:
        employee = db.get(Employee, employee_id)
        assert employee is not None
        assert employee.location == "Remote"


@pytest.mark.v2
def test_lifecycle_manager_sees_own_and_reports_only(client, second_client):
    """Manager listing includes self and direct reports, not the wider org."""
    login_admin(client)
    _, manager_employee_id = _user_and_employee(MANAGER_EMAIL)
    with session_scope() as db:
        report = db.scalar(
            select(Employee).where(Employee.manager_id == manager_employee_id)
        )
        assert report is not None
        # The demo seed has a single reporting line; add an unrelated colleague.
        unrelated = Employee(
            org_id=report.org_id,
            employee_no="EMP-9001",
            first_name="Ravi",
            last_name="Kumar",
            status=EmployeeStatus.ACTIVE,
        )
        db.add(unrelated)
        db.flush()
        unrelated_id = unrelated.id
        report_id = report.id

    for code, target in (
        ("LC-AUTH-6", manager_employee_id),
        ("LC-AUTH-7", report_id),
        ("LC-AUTH-8", unrelated_id),
    ):
        created = client.post(
            "/api/v1/lifecycle",
            json={
                "code": code,
                "change_type": "promotion",
                "employee_id": str(target),
            },
            headers=api_headers(client),
        )
        assert created.status_code == 201, created.text

    login_manager(second_client)
    r = second_client.get("/api/v1/lifecycle", headers=api_headers(second_client))
    assert r.status_code == 200
    codes = {item["code"] for item in r.json()}
    assert {"LC-AUTH-6", "LC-AUTH-7"} <= codes
    assert "LC-AUTH-8" not in codes


@pytest.mark.v2
def test_lifecycle_employee_cannot_read_colleague_request(client, second_client):
    """Object-level read policy returns 404 for other employees' requests."""
    login_admin(client)
    _, employee_id = _user_and_employee(EMPLOYEE_EMAIL)
    colleague = _colleague_id(employee_id)
    created = client.post(
        "/api/v1/lifecycle",
        json={
            "code": "LC-AUTH-9",
            "change_type": "promotion",
            "employee_id": colleague,
        },
        headers=api_headers(client),
    )
    cr_id = created.json()["id"]

    login_employee(second_client)
    r = second_client.get(
        f"/api/v1/lifecycle/{cr_id}", headers=api_headers(second_client)
    )
    assert r.status_code == 404

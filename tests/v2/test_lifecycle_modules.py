"""Market-standard lifecycle modules: job changes, onboarding, offboarding."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import Employee, Journey, JourneyTask, Separation, User
from tests.v2.helpers import (
    EMPLOYEE_EMAIL,
    MANAGER_EMAIL,
    api_headers,
    csrf,
    login_admin,
    login_employee,
    login_manager,
)


def _today() -> str:
    return date.today().isoformat()


def _future(days: int = 20) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _employee_id(email: str) -> str:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None and user.employee_id is not None
        return str(user.employee_id)


def _form(client, **values) -> dict[str, str]:
    data = {"_csrf": csrf(client)}
    data.update({key: str(value) for key, value in values.items()})
    return data


def _create_job_change(client, **overrides) -> dict:
    payload = {
        "employee_id": _employee_id(EMPLOYEE_EMAIL),
        "event": "promotion",
        "reason_code": "increase_in_responsibility",
        "effective_date": _today(),
        "proposed": {"location": "Pune"},
    }
    payload.update(overrides)
    response = client.post(
        "/api/v1/job-changes", json=payload, headers=api_headers(client)
    )
    return response


def _start_onboarding(client, *, employee_id: str, anchor: str | None = None) -> dict:
    response = client.post(
        "/api/v1/journeys",
        json={
            "employee_id": employee_id,
            "journey_type": "onboarding",
            "anchor_date": anchor or _today(),
        },
        headers=api_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- Job changes -----------------------------------------------------------


def test_job_change_snapshots_and_reason(client):
    """A draft records event, reason and current → proposed values."""
    login_admin(client)
    response = _create_job_change(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["event"] == "promotion"
    assert body["event_label"] == "Promotion"
    assert body["reason_code"] == "increase_in_responsibility"
    assert body["status"] == "draft"
    assert body["proposed_snapshot"]["location"] == "Pune"
    assert body["current_snapshot"]["location"] == "Gurugram"
    assert body["approval_step"] == "manager"


def test_job_change_rejects_reason_for_wrong_event(client):
    """Reasons are validated against the event (Workday/SF behaviour)."""
    login_admin(client)
    response = _create_job_change(
        client, event="promotion", reason_code="relocation"
    )
    assert response.status_code == 422


def test_job_change_rejects_disallowed_field(client):
    """A promotion cannot rewrite a bank-like or unrelated field."""
    login_admin(client)
    response = _create_job_change(
        client, event="location_change", proposed={"department_id": "x"}
    )
    assert response.status_code == 422


def test_job_change_manager_then_hr_approval_applies(client, second_client):
    """Manager approves first; HR approval applies an effective-today change."""
    login_admin(client)
    created = _create_job_change(client, event="manager_change",
                                 reason_code="new_manager",
                                 proposed={"manager_id": _employee_id(MANAGER_EMAIL)})
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]
    assert client.post(
        f"/api/v1/job-changes/{cr_id}/submit", json={}, headers=api_headers(client)
    ).status_code == 200

    login_manager(second_client)
    first = second_client.post(
        f"/api/v1/job-changes/{cr_id}/approve",
        json={},
        headers=api_headers(second_client),
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending_approval"
    assert first.json()["approval_step"] == "hr"

    final = client.post(
        f"/api/v1/job-changes/{cr_id}/approve",
        json={},
        headers=api_headers(client),
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "completed"
    assert final.json()["applied_at"] is not None


def test_future_dated_change_is_not_applied_early(client):
    """Approval does not mutate the employee before the effective date."""
    login_admin(client)
    created = _create_job_change(client, effective_date=_future())
    cr_id = created.json()["id"]
    client.post(f"/api/v1/job-changes/{cr_id}/submit", json={}, headers=api_headers(client))
    approved = client.post(
        f"/api/v1/job-changes/{cr_id}/approve", json={}, headers=api_headers(client)
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["applied_at"] is None


def test_job_change_reason_catalogue(client):
    login_admin(client)
    response = client.get("/api/v1/job-changes/reasons", headers=api_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert "promotion" in body["reasons"]
    assert any(row["code"] == "department_transfer" for row in body["reasons"]["transfer"])

    filtered = client.get(
        "/api/v1/job-changes/reasons?event=promotion", headers=api_headers(client)
    )
    assert filtered.status_code == 200
    assert filtered.json()["reasons"][0][0] == ""


def test_employee_cannot_raise_change_for_a_peer(client, second_client):
    login_employee(second_client)
    response = _create_job_change(
        second_client,
        employee_id=_employee_id(MANAGER_EMAIL),
        event="promotion",
        reason_code="increase_in_responsibility",
        proposed={"location": "Pune"},
    )
    assert response.status_code == 403


def test_job_changes_api_requires_authentication(client):
    assert client.get("/api/v1/job-changes").status_code == 401


def test_job_changes_web_pages(client):
    login_admin(client)
    listing = client.get("/job-changes")
    assert listing.status_code == 200
    assert "Job changes" in listing.text
    form = client.get("/job-changes/new")
    assert form.status_code == 200
    assert "Raise a job change" in form.text
    created = _create_job_change(client)
    detail = client.get(f"/job-changes/{created.json()['id']}")
    assert detail.status_code == 200
    assert "Current → proposed" in detail.text


# --- Onboarding ------------------------------------------------------------


def test_onboarding_creates_tasks_and_due_dates(client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    body = _start_onboarding(client, employee_id=employee_id)
    assert body["status"] == "in_progress"
    assert len(body["tasks"]) == 8
    assert {task["owner_role"] for task in body["tasks"]} == {
        "employee",
        "hr",
        "it",
        "manager",
    }
    first = next(task for task in body["tasks"] if task["owner_role"] == "employee")
    assert first["due_on"] == (date.today() - timedelta(days=7)).isoformat()

    with session_scope() as db:
        rows = db.scalars(select(JourneyTask)).all()
        assert len(rows) == 8


def test_onboarding_duplicate_rejected(client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    _start_onboarding(client, employee_id=employee_id)
    again = client.post(
        "/api/v1/journeys",
        json={
            "employee_id": employee_id,
            "journey_type": "onboarding",
            "anchor_date": _today(),
        },
        headers=api_headers(client),
    )
    assert again.status_code == 422


def test_onboarding_completes_when_required_tasks_are_done(client):
    login_admin(client)
    journey = _start_onboarding(client, employee_id=_employee_id(EMPLOYEE_EMAIL))
    for task in journey["tasks"]:
        if task["required"]:
            response = client.post(
                f"/api/v1/journeys/{journey['id']}/tasks/{task['id']}/complete",
                json={},
                headers=api_headers(client),
            )
            assert response.status_code == 200, response.text
    final = client.get(
        f"/api/v1/journeys/{journey['id']}", headers=api_headers(client)
    )
    assert final.json()["status"] == "completed"


def test_employee_cannot_complete_it_task(client, second_client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    journey = _start_onboarding(client, employee_id=employee_id)
    it_task = next(task for task in journey["tasks"] if task["owner_role"] == "it")

    login_employee(second_client)
    denied = second_client.post(
        f"/api/v1/journeys/{journey['id']}/tasks/{it_task['id']}/complete",
        json={},
        headers=api_headers(second_client),
    )
    assert denied.status_code == 403

    own_task = next(
        task for task in journey["tasks"] if task["owner_role"] == "employee"
    )
    allowed = second_client.post(
        f"/api/v1/journeys/{journey['id']}/tasks/{own_task['id']}/complete",
        json={},
        headers=api_headers(second_client),
    )
    assert allowed.status_code == 200, allowed.text


def test_employee_cannot_read_peer_journey(client, second_client):
    login_admin(client)
    journey = _start_onboarding(client, employee_id=_employee_id(MANAGER_EMAIL))

    login_employee(second_client)
    response = second_client.get(
        f"/api/v1/journeys/{journey['id']}", headers=api_headers(second_client)
    )
    assert response.status_code == 404


def test_onboarding_web_start_and_detail(client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Start onboarding" in page.text

    started = client.post(
        "/onboarding/start",
        data=_form(client, employee_id=employee_id, anchor_date=_today()),
    )
    assert started.status_code == 303
    with session_scope() as db:
        journey = db.scalar(select(Journey))
        assert journey is not None
        detail = client.get(f"/onboarding/{journey.id}")
    assert detail.status_code == 200
    assert "Clearance" not in detail.text
    assert "Tasks" in detail.text


# --- Offboarding -----------------------------------------------------------


def test_offboarding_creates_separation_and_checklist(client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    response = client.post(
        "/api/v1/separations",
        json={
            "employee_id": employee_id,
            "exit_type": "resignation",
            "last_working_day": _future(),
            "rehire_eligible": True,
        },
        headers=api_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "notice"
    assert body["journey_id"] is not None

    with session_scope() as db:
        employee = db.get(Employee, UUID(employee_id))
        assert employee is not None and employee.status.value == "notice"
        separation = db.scalar(select(Separation))
        assert separation is not None
        tasks = db.scalars(select(JourneyTask)).all()
        assert len(tasks) == 7


def test_offboarding_exit_requires_final_pay(client):
    login_admin(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    created = client.post(
        "/api/v1/separations",
        json={
            "employee_id": employee_id,
            "exit_type": "resignation",
            "last_working_day": _future(),
        },
        headers=api_headers(client),
    ).json()
    separation_id = created["id"]
    for status in ("clearance", "final_pay"):
        moved = client.post(
            f"/api/v1/separations/{separation_id}/advance",
            json={"status": status},
            headers=api_headers(client),
        )
        assert moved.status_code == 200, moved.text

    blocked = client.post(
        f"/api/v1/separations/{separation_id}/advance",
        json={"status": "exited"},
        headers=api_headers(client),
    )
    assert blocked.status_code == 422

    exited = client.post(
        f"/api/v1/separations/{separation_id}/advance",
        json={"status": "exited", "final_pay_ready": True},
        headers=api_headers(client),
    )
    assert exited.status_code == 200, exited.text
    assert exited.json()["status"] == "exited"
    with session_scope() as db:
        employee = db.get(Employee, UUID(employee_id))
        assert employee is not None and employee.status.value == "exited"


def test_offboarding_cannot_skip_statuses(client):
    login_admin(client)
    created = client.post(
        "/api/v1/separations",
        json={
            "employee_id": _employee_id(EMPLOYEE_EMAIL),
            "exit_type": "termination",
            "last_working_day": _future(),
        },
        headers=api_headers(client),
    ).json()
    response = client.post(
        f"/api/v1/separations/{created['id']}/advance",
        json={"status": "exited", "final_pay_ready": True},
        headers=api_headers(client),
    )
    assert response.status_code == 422


def test_offboarding_web_pages(client):
    login_admin(client)
    page = client.get("/offboarding")
    assert page.status_code == 200
    assert "Initiate offboarding" in page.text

    started = client.post(
        "/offboarding/start",
        data=_form(
            client,
            employee_id=_employee_id(EMPLOYEE_EMAIL),
            exit_type="resignation",
            last_working_day=_future(),
        ),
    )
    assert started.status_code == 303
    with session_scope() as db:
        separation = db.scalar(select(Separation))
        assert separation is not None
        detail = client.get(f"/offboarding/{separation.id}")
    assert detail.status_code == 200
    assert "Clearance checklist" in detail.text

"""R4 talent: check-ins and 360 feedback tests (API-first, role-scoped)."""
from __future__ import annotations

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import AuditEvent, Employee, FeedbackRequest, ReviewCheckIn, User
from tests.v2.helpers import (
    EMPLOYEE_EMAIL,
    MANAGER_EMAIL,
    api_headers,
    login_admin,
    login_employee,
    login_manager,
)


def _employee_id(email: str) -> str:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None and user.employee_id is not None
        return str(user.employee_id)


def _create_cycle(client, code: str = "RC-2026") -> str:
    response = client.post(
        "/api/v1/review-cycles",
        json={
            "name": f"Review {code}",
            "code": code,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    assert response.status_code == 201, response.text
    return code


def _create_check_in(client, *, cycle: str, employee_id: str, code: str | None = None):
    response = client.post(
        "/api/v1/check-ins",
        json={
            "cycle_code": cycle,
            "employee_id": employee_id,
            "due_on": "2026-11-30",
        },
        headers=api_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _request_feedback(client, **body):
    return client.post(
        "/api/v1/feedback-requests", json=body, headers=api_headers(client)
    )


# --- Check-ins ---


def test_check_in_created_for_employee(client):
    """HR opens a check-in; the manager is derived from the employee record."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)

    body = _create_check_in(client, cycle=cycle, employee_id=employee_id)
    assert body["status"] == "pending"
    assert body["cycle_code"] == cycle
    assert body["employee_name"] == "Diya Nair"
    assert body["manager_name"] == "Aarav Sharma"
    assert body["due_on"] == "2026-11-30"


def test_check_in_duplicate_rejected(client):
    """One check-in per employee per cycle."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    _create_check_in(client, cycle=cycle, employee_id=employee_id)

    response = client.post(
        "/api/v1/check-ins",
        json={"cycle_code": cycle, "employee_id": employee_id},
        headers=api_headers(client),
    )
    assert response.status_code == 422


def test_check_in_scoping_for_manager_and_employee(client, second_client, third_client):
    """Managers see their own and reports' check-ins; employees see their own."""
    login_admin(client)
    cycle = _create_cycle(client)
    manager_id = _employee_id(MANAGER_EMAIL)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    _create_check_in(client, cycle=cycle, employee_id=manager_id)
    _create_check_in(client, cycle=cycle, employee_id=employee_id)

    login_manager(second_client)
    manager_view = second_client.get(
        "/api/v1/check-ins", headers=api_headers(second_client)
    )
    assert manager_view.status_code == 200
    assert {item["employee_id"] for item in manager_view.json()} == {
        manager_id,
        employee_id,
    }

    login_employee(third_client)
    employee_view = third_client.get(
        "/api/v1/check-ins", headers=api_headers(third_client)
    )
    assert employee_view.status_code == 200
    rows = employee_view.json()
    assert len(rows) == 1
    assert rows[0]["employee_id"] == employee_id


def test_employee_writes_own_notes_only(client, second_client):
    """Self notes are employee-owned; manager fields are rejected."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    check_in = _create_check_in(client, cycle=cycle, employee_id=employee_id)

    login_employee(second_client)
    ok = second_client.post(
        f"/api/v1/check-ins/{check_in['id']}/submit",
        json={"employee_notes": "Shipped the data platform migration."},
        headers=api_headers(second_client),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "submitted"
    assert ok.json()["employee_notes"].startswith("Shipped")

    denied = second_client.post(
        f"/api/v1/check-ins/{check_in['id']}/submit",
        json={"manager_notes": "I rate myself 5"},
        headers=api_headers(second_client),
    )
    assert denied.status_code == 403


def test_manager_records_rating(client, second_client):
    """Managers can add manager notes and a 1-5 rating."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    check_in = _create_check_in(client, cycle=cycle, employee_id=employee_id)

    login_manager(second_client)
    response = second_client.post(
        f"/api/v1/check-ins/{check_in['id']}/submit",
        json={"manager_notes": "Consistent delivery", "rating": 4.5},
        headers=api_headers(second_client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == 4.5
    assert response.json()["manager_notes"] == "Consistent delivery"


def test_check_in_rating_validated(client, second_client):
    """Ratings outside 1-5 are rejected."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    check_in = _create_check_in(client, cycle=cycle, employee_id=employee_id)

    login_manager(second_client)
    response = second_client.post(
        f"/api/v1/check-ins/{check_in['id']}/submit",
        json={"manager_notes": "Great", "rating": 9},
        headers=api_headers(second_client),
    )
    assert response.status_code == 422


def test_closed_cycle_blocks_check_in_submission(client, second_client):
    """Closed cycles freeze check-in content."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    check_in = _create_check_in(client, cycle=cycle, employee_id=employee_id)
    assert (
        client.post(
            f"/api/v1/review-cycles/{cycle}/close", json={}, headers=api_headers(client)
        ).status_code
        == 200
    )

    login_employee(second_client)
    response = second_client.post(
        f"/api/v1/check-ins/{check_in['id']}/submit",
        json={"employee_notes": "Late note"},
        headers=api_headers(second_client),
    )
    assert response.status_code == 422


# --- 360 feedback ---


def test_named_feedback_end_to_end(client, second_client, third_client):
    """Request → inbox → submit → subject reads attributed feedback."""
    login_admin(client)
    cycle = _create_cycle(client)
    subject_id = _employee_id(EMPLOYEE_EMAIL)
    reviewer_id = _employee_id(MANAGER_EMAIL)

    created = _request_feedback(
        client,
        subject_employee_id=subject_id,
        reviewer_employee_id=reviewer_id,
        cycle_code=cycle,
        relationship="manager",
        anonymous=False,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    login_manager(second_client)
    inbox = second_client.get(
        "/api/v1/feedback-requests", headers=api_headers(second_client)
    )
    assert inbox.status_code == 200
    assert [row["id"] for row in inbox.json()] == [request_id]

    submitted = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/submit",
        json={
            "strengths": "Deep technical judgement",
            "improvements": "Delegate more",
            "comments": "Ready for the next level",
            "rating": 4,
        },
        headers=api_headers(second_client),
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["reviewer_name"] == "Aarav Sharma"

    login_employee(third_client)
    feedback = third_client.get(
        f"/api/v1/feedback/{subject_id}", headers=api_headers(third_client)
    )
    assert feedback.status_code == 200
    rows = feedback.json()
    assert len(rows) == 1
    assert rows[0]["anonymous"] is False
    assert rows[0]["reviewer_name"] == "Aarav Sharma"
    assert rows[0]["strengths"] == "Deep technical judgement"


def test_anonymous_feedback_is_erased(client, second_client, third_client):
    """Anonymous submissions destroy the reviewer link and the audit actor."""
    login_admin(client)
    cycle = _create_cycle(client)
    subject_id = _employee_id(EMPLOYEE_EMAIL)
    reviewer_id = _employee_id(MANAGER_EMAIL)

    created = _request_feedback(
        client,
        subject_employee_id=subject_id,
        reviewer_employee_id=reviewer_id,
        cycle_code=cycle,
        relationship="peer",
        anonymous=True,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    login_manager(second_client)
    submitted = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/submit",
        json={"comments": "Great partner across teams"},
        headers=api_headers(second_client),
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["anonymous"] is True
    assert submitted.json()["reviewer_name"] is None

    # The identity is gone from the business table...
    with session_scope() as db:
        request = db.get(FeedbackRequest, __import__("uuid").UUID(request_id))
        assert request is not None
        assert request.reviewer_employee_id is None
        assert request.reviewer_user_id is None
        assert request.comments == "Great partner across teams"

        # ...and the submission audit row cannot point at the reviewer either.
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_type == "feedback_request",
                    AuditEvent.entity_id == request_id,
                )
                .order_by(AuditEvent.created_at)
            ).all()
        )
        assert events, "expected audit events for the feedback request"
        assert events[-1].actor_id is None

    login_employee(third_client)
    feedback = third_client.get(
        f"/api/v1/feedback/{subject_id}", headers=api_headers(third_client)
    )
    assert feedback.status_code == 200
    rows = feedback.json()
    assert rows[0]["anonymous"] is True
    assert rows[0]["reviewer_employee_id"] is None
    assert rows[0]["reviewer_name"] is None


def test_feedback_privacy_blocks_peers(client, second_client):
    """An employee cannot read another employee's feedback."""
    login_admin(client)
    manager_id = _employee_id(MANAGER_EMAIL)
    _request_feedback(
        client,
        subject_employee_id=manager_id,
        reviewer_employee_id=_employee_id(EMPLOYEE_EMAIL),
        relationship="report",
        anonymous=False,
    )

    login_employee(second_client)
    response = second_client.get(
        f"/api/v1/feedback/{manager_id}", headers=api_headers(second_client)
    )
    assert response.status_code == 404


def test_feedback_request_cannot_be_submitted_by_others(client, second_client):
    """Only the assigned reviewer may answer a request."""
    login_admin(client)
    created = _request_feedback(
        client,
        subject_employee_id=_employee_id(MANAGER_EMAIL),
        reviewer_employee_id=_employee_id(EMPLOYEE_EMAIL),
        relationship="report",
    )
    request_id = created.json()["id"]

    login_manager(second_client)
    response = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/submit",
        json={"comments": "Not my request"},
        headers=api_headers(second_client),
    )
    assert response.status_code == 404


def test_feedback_requires_content(client, second_client):
    """Blank submissions are rejected."""
    login_admin(client)
    created = _request_feedback(
        client,
        subject_employee_id=_employee_id(EMPLOYEE_EMAIL),
        reviewer_employee_id=_employee_id(MANAGER_EMAIL),
        relationship="manager",
    )
    request_id = created.json()["id"]

    login_manager(second_client)
    response = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/submit",
        json={"comments": "   "},
        headers=api_headers(second_client),
    )
    assert response.status_code == 422


def test_feedback_duplicate_rejected(client):
    """The same reviewer cannot be asked twice for the same cycle."""
    login_admin(client)
    cycle = _create_cycle(client)
    body = {
        "subject_employee_id": _employee_id(EMPLOYEE_EMAIL),
        "reviewer_employee_id": _employee_id(MANAGER_EMAIL),
        "cycle_code": cycle,
        "relationship": "manager",
    }
    assert _request_feedback(client, **body).status_code == 201
    assert _request_feedback(client, **body).status_code == 422


def test_feedback_decline(client, second_client):
    """Reviewers can decline; a declined request cannot be submitted."""
    login_admin(client)
    created = _request_feedback(
        client,
        subject_employee_id=_employee_id(EMPLOYEE_EMAIL),
        reviewer_employee_id=_employee_id(MANAGER_EMAIL),
        relationship="manager",
    )
    request_id = created.json()["id"]

    login_manager(second_client)
    declined = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/decline",
        json={},
        headers=api_headers(second_client),
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"

    late = second_client.post(
        f"/api/v1/feedback-requests/{request_id}/submit",
        json={"comments": "Too late"},
        headers=api_headers(second_client),
    )
    assert late.status_code == 422


def test_employee_cannot_request_feedback_about_colleague(client, second_client):
    """Non-HR requests are limited to self or direct reports."""
    login_employee(second_client)
    response = _request_feedback(
        second_client,
        subject_employee_id=_employee_id(MANAGER_EMAIL),
        reviewer_employee_id=_employee_id(EMPLOYEE_EMAIL),
        relationship="peer",
    )
    assert response.status_code == 403


def test_employee_can_request_self_review(client, second_client):
    """Self-review requests default the reviewer to the requester."""
    login_employee(second_client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    response = _request_feedback(
        second_client,
        subject_employee_id=employee_id,
        relationship="self",
    )
    assert response.status_code == 201, response.text
    assert response.json()["reviewer_employee_id"] == employee_id
    assert response.json()["relationship"] == "self"


def test_manager_can_request_feedback_for_report(client, second_client):
    """Managers may request feedback about their direct reports."""
    login_manager(second_client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    response = _request_feedback(
        second_client,
        subject_employee_id=employee_id,
        reviewer_employee_id=_employee_id(MANAGER_EMAIL),
        relationship="manager",
        anonymous=True,
    )
    assert response.status_code == 201, response.text


def test_check_in_unknown_cycle_404(client):
    """Unknown cycles produce a 404, not a validation error."""
    login_admin(client)
    response = client.post(
        "/api/v1/check-ins",
        json={
            "cycle_code": "NOPE",
            "employee_id": _employee_id(EMPLOYEE_EMAIL),
        },
        headers=api_headers(client),
    )
    assert response.status_code == 404


def test_check_in_list_requires_authentication(client):
    """Unauthenticated API access is refused."""
    assert client.get("/api/v1/check-ins").status_code == 401


def test_check_in_rows_persisted(client):
    """Sanity check the ORM row and manager link."""
    login_admin(client)
    cycle = _create_cycle(client)
    employee_id = _employee_id(EMPLOYEE_EMAIL)
    _create_check_in(client, cycle=cycle, employee_id=employee_id)

    with session_scope() as db:
        rows = db.scalars(select(ReviewCheckIn)).all()
        assert len(rows) == 1
        employee = db.get(Employee, rows[0].employee_id)
        assert employee is not None
        assert rows[0].manager_id == employee.manager_id

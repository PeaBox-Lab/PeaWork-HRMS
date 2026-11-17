from __future__ import annotations

from datetime import timedelta

from tests.v2.helpers import (
    api_headers,
    login_admin,
    login_employee,
    login_manager,
    next_weekday,
)


def _leave_types(client) -> list[dict]:
    response = client.get("/api/v1/leave-types")
    assert response.status_code == 200
    return response.json()


def test_leave_request_and_manager_approval(client, second_client):
    login_employee(client)
    types = _leave_types(client)
    casual = next(item for item in types if item["code"] == "CL")

    start = next_weekday(14)
    end = start + timedelta(days=1)
    created = client.post(
        "/api/v1/leave-requests",
        json={
            "leave_type_id": casual["id"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": "Family commitment",
        },
        headers=api_headers(client),
    )
    assert created.status_code == 201, created.text
    leave = created.json()
    assert leave["status"] == "pending"
    days = float(leave["days"])
    assert days > 0
    leave_id = leave["id"]

    duplicate = client.post(
        "/api/v1/leave-requests",
        json={
            "leave_type_id": casual["id"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        headers=api_headers(client),
    )
    assert duplicate.status_code == 409

    login_manager(second_client)
    pending = second_client.get("/api/v1/leave-requests?status=pending")
    assert pending.status_code == 200
    ids = {item["id"] for item in pending.json()["items"]}
    assert leave_id in ids

    decision = second_client.post(
        f"/api/v1/leave-requests/{leave_id}/decision",
        json={"decision": "approve", "comment": "Approved by manager"},
        headers=api_headers(second_client),
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"
    assert decision.json()["approver_comment"] == "Approved by manager"

    balances = client.get("/api/v1/leave/balances")
    assert balances.status_code == 200
    balance = next(item for item in balances.json() if item["leave_type_id"] == casual["id"])
    assert float(balance["used"]) == days
    assert float(balance["available"]) == float(balance["opening"]) - days


def test_employee_cannot_decide_their_own_leave(client):
    login_employee(client)
    casual = next(item for item in _leave_types(client) if item["code"] == "CL")
    start = next_weekday(20)
    created = client.post(
        "/api/v1/leave-requests",
        json={
            "leave_type_id": casual["id"],
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        },
        headers=api_headers(client),
    )
    assert created.status_code == 201, created.text
    leave_id = created.json()["id"]

    response = client.post(
        f"/api/v1/leave-requests/{leave_id}/decision",
        json={"decision": "approve"},
        headers=api_headers(client),
    )
    assert response.status_code == 403


def test_manager_cannot_approve_own_leave(second_client):
    login_manager(second_client)
    casual = next(item for item in _leave_types(second_client) if item["code"] == "CL")
    start = next_weekday(25)
    created = second_client.post(
        "/api/v1/leave-requests",
        json={
            "leave_type_id": casual["id"],
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        },
        headers=api_headers(second_client),
    )
    assert created.status_code == 201, created.text
    leave_id = created.json()["id"]

    response = second_client.post(
        f"/api/v1/leave-requests/{leave_id}/decision",
        json={"decision": "approve"},
        headers=api_headers(second_client),
    )
    assert response.status_code == 403
    assert "own leave" in response.json()["detail"]


def test_leave_and_calendar_pages_render(client):
    login_admin(client)
    leave_page = client.get("/leave")
    assert leave_page.status_code == 200
    assert "Leave" in leave_page.text

    calendar_page = client.get("/leave/calendar")
    assert calendar_page.status_code == 200
    assert "Team calendar" in calendar_page.text

    filtered = client.get("/leave?status=pending")
    assert filtered.status_code == 200

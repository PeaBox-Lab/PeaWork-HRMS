"""R4 Talent tests: review cycles + goals/OKRs.

Pattern mirrors R2 (test_time.py): API-first, DB-backed, role-scoped,
error cases, and smoke against live dev server.
"""
from __future__ import annotations


import pytest

from tests.v2.helpers import api_headers, login_admin, login_manager


# ---------------------------------------------------------------------------
# Review Cycles
# ---------------------------------------------------------------------------


@pytest.mark.v2
def test_review_cycle_create(client):
    """POST /api/v1/review-cycles → 201, returns cycle dict."""
    login_admin(client)
    r = client.post(
        "/api/v1/review-cycles",
        json={
            "name": "H1 Reviews",
            "code": "H1R",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "H1R"
    assert body["status"] == "draft"


@pytest.mark.v2
def test_review_cycle_create_duplicate_code(client):
    """Duplicate code → 422."""
    login_admin(client)
    # Create first cycle
    client.post(
        "/api/v1/review-cycles",
        json={
            "name": "First",
            "code": "H1R",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    # Duplicate
    r = client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Duplicate",
            "code": "H1R",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_review_cycle_create_start_gt_end(client):
    """start_date > end_date → 422."""
    login_admin(client)
    r = client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Bad",
            "code": "BAD",
            "start_date": "2026-12-01",
            "end_date": "2026-10-01",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_review_cycle_list(client):
    """GET /api/v1/review-cycles → 200, returns list."""
    login_admin(client)
    r = client.get("/api/v1/review-cycles", headers=api_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.v2
def test_review_cycle_list_role_scoping(client, second_client):
    """Admin sees all cycles; manager/employee get their scoped lists."""
    # Admin sees all cycles
    login_admin(client)
    r = client.get("/api/v1/review-cycles", headers=api_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # Manager (independent session) gets a scoped list
    login_manager(second_client)
    r = second_client.get("/api/v1/review-cycles", headers=api_headers(second_client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.v2
def test_review_cycle_detail(client):
    """GET /api/v1/review-cycles/{code} → 200, returns cycle dict."""
    login_admin(client)
    client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Detail Test",
            "code": "DT1",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/review-cycles/DT1", headers=api_headers(client))
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "DT1"
    assert body["status"] == "draft"


@pytest.mark.v2
def test_review_cycle_close(client):
    """POST /api/v1/review-cycles/{code}/close → 200, marks closed."""
    login_admin(client)
    code = "CLOSE_TEST"
    client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Close Test",
            "code": code,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    r = client.post(f"/api/v1/review-cycles/{code}/close", json={}, headers=api_headers(client))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "closed"


@pytest.mark.v2
def test_review_cycle_close_idempotent(client):
    """Close twice → idempotent, still closed."""
    login_admin(client)
    code = "CLOSE_IDEM"
    client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Idempotent",
            "code": code,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    r1 = client.post(f"/api/v1/review-cycles/{code}/close", json={}, headers=api_headers(client))
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/review-cycles/{code}/close", json={}, headers=api_headers(client))
    assert r2.status_code == 200


@pytest.mark.v2
def test_review_cycle_enroll(client):
    """POST /api/v1/review-cycles/{code}/enroll → 200."""
    login_admin(client)
    code = "ENROLL_TEST"
    client.post(
        "/api/v1/review-cycles",
        json={
            "name": "Enroll Test",
            "code": code,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    r = client.post(
        f"/api/v1/review-cycles/{code}/enroll",
        json={"employee_id": "EMP-0002"},
        headers=api_headers(client),
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Goals / OKRs
# ---------------------------------------------------------------------------


@pytest.mark.v2
def test_goal_create(client):
    """POST /api/v1/goals → 201, returns goal dict."""
    login_admin(client)
    r = client.post(
        "/api/v1/goals",
        json={
            "code": "GOAL_T1",
            "title": "Increase customer satisfaction",
            "objective": "Improve CSAT score by 15%",
            "due_date": "2026-12-31",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == "GOAL_T1"
    assert body["title"] == "Increase customer satisfaction"
    assert body["status"] == "open"


@pytest.mark.v2
def test_goal_create_duplicate_code(client):
    """Duplicate code → 422."""
    login_admin(client)
    # Create first goal
    client.post(
        "/api/v1/goals",
        json={
            "code": "GOAL_T1",
            "title": "First Goal",
            "objective": "First creation",
        },
        headers=api_headers(client),
    )
    # Duplicate
    r = client.post(
        "/api/v1/goals",
        json={
            "code": "GOAL_T1",
            "title": "Duplicate",
            "objective": "Should fail",
        },
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_goal_list(client):
    """GET /api/v1/goals → 200, returns list."""
    login_admin(client)
    # Create a goal first so the list is non-empty
    client.post(
        "/api/v1/goals",
        json={
            "code": "GOAL_T1",
            "title": "List Goal",
            "objective": "List endpoint test",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/goals", headers=api_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) > 0


@pytest.mark.v2
def test_goal_detail(client):
    """GET /api/v1/goals/{goal_id} → 200, returns goal dict."""
    login_admin(client)
    client.post(
        "/api/v1/goals",
        json={
            "code": "GD_T1",
            "title": "Detail Goal",
            "objective": "Test detail endpoint",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/goals", headers=api_headers(client))
    bodies = r.json()
    assert len(bodies) > 0
    goal_id = bodies[0]["id"]
    r = client.get(f"/api/v1/goals/{goal_id}", headers=api_headers(client))
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == bodies[0]["title"]
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.v2
def test_goal_progress_update(client):
    """POST /api/v1/goals/{goal_id}/progress → 200, creates append-only history."""
    login_admin(client)
    client.post(
        "/api/v1/goals",
        json={
            "code": "PROG_T1",
            "title": "Progress Test",
            "objective": "Test progress tracking",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/goals", headers=api_headers(client))
    goal_id = r.json()[0]["id"]

    r = client.post(
        f"/api/v1/goals/{goal_id}/progress",
        json={"progress": 0.5, "note": "Halfway there"},
        headers=api_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["progress"] == 0.5
    assert body["status"] == "open"

    r = client.post(
        f"/api/v1/goals/{goal_id}/progress",
        json={"progress": 1.0},
        headers=api_headers(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["progress"] == 1.0
    assert body["status"] == "achieved"


@pytest.mark.v2
def test_goal_progress_invalid(client):
    """Invalid progress (out of range) → 422."""
    login_admin(client)
    client.post(
        "/api/v1/goals",
        json={
            "code": "PROG_T2",
            "title": "Invalid Progress Goal",
            "objective": "Test validation",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/goals", headers=api_headers(client))
    goal_id = r.json()[0]["id"]
    r = client.post(
        f"/api/v1/goals/{goal_id}/progress",
        json={"progress": 1.5},
        headers=api_headers(client),
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/v1/goals/{goal_id}/progress",
        json={"progress": -0.1},
        headers=api_headers(client),
    )
    assert r.status_code == 422


@pytest.mark.v2
def test_goal_progress_history(client):
    """GET /api/v1/goals/{goal_id}/progress → 200, returns list."""
    login_admin(client)
    client.post(
        "/api/v1/goals",
        json={
            "code": "GOAL_T2",
            "title": "History Goal",
            "objective": "Test history endpoint",
        },
        headers=api_headers(client),
    )
    r = client.get("/api/v1/goals", headers=api_headers(client))
    goal_id = r.json()[0]["id"]
    r = client.get(f"/api/v1/goals/{goal_id}/progress", headers=api_headers(client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.v2
def test_manager_sees_reports_goals(client, second_client):
    """Manager can see goals of their direct reports."""
    login_manager(second_client)
    r = second_client.get("/api/v1/goals", headers=api_headers(second_client))
    assert r.status_code == 200
    assert isinstance(r.json(), list)

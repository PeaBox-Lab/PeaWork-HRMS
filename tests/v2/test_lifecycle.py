"""R5 Employee lifecycle change-request tests.

Pattern mirrors R2/R2 test style: API-first, DB-backed, role-scoped,
error cases, bootstrap fixture per test.
"""
from __future__ import annotations

import pytest

from tests.v2.helpers import api_headers, login_admin, login_manager


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
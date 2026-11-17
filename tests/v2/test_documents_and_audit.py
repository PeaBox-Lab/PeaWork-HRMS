from __future__ import annotations

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import Employee
from tests.v2.helpers import csrf, login_admin, login_employee


def _employee_id(email: str) -> str:
    with session_scope() as db:
        employee = db.scalar(select(Employee).where(Employee.work_email == email))
        assert employee is not None
        return str(employee.id)


def test_document_acknowledgement_by_employee(client, second_client):
    """Employee uploads a document for themselves and acknowledges it."""
    login_admin(client)
    employee_id = _employee_id("diya.nair@example.com")

    upload = client.post(
        "/api/v1/documents",
        data={
            "_csrf": csrf(client),
            "employee_id": employee_id,
            "title": "Employee Handbook",
            "category": "other",
        },
        files={"file": ("handbook.txt", b"handbook content", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    document_id = document["id"]
    assert document["acknowledged_at"] is None
    assert document["acknowledged_by"] is None

    # Employee logs in and acknowledges
    login_employee(second_client)
    ack = second_client.post(
        f"/api/v1/documents/{document_id}/acknowledge",
        data={"_csrf": csrf(second_client)},
    )
    assert ack.status_code == 200, ack.text

    # Verify document summary updated
    docs = client.get("/api/v1/documents?employee_id=" + employee_id)
    assert docs.status_code == 200
    items = docs.json()
    assert len(items) >= 1
    my_doc = next(d for d in items if d["id"] == document_id)
    assert my_doc["acknowledged_at"] is not None
    assert my_doc["acknowledged_by"] is not None

    # Duplicate acknowledge rejected
    ack2 = second_client.post(
        f"/api/v1/documents/{document_id}/acknowledge",
        data={"_csrf": csrf(second_client)},
    )
    assert ack2.status_code == 409, ack2.text


def test_document_acknowledgement_on_behalf_by_admin(client, second_client):
    """Admin acknowledges a document on behalf of an employee."""
    login_admin(client)
    employee_id = _employee_id("diya.nair@example.com")

    upload = client.post(
        "/api/v1/documents",
        data={
            "_csrf": csrf(client),
            "employee_id": employee_id,
            "title": "Confidentiality Policy",
            "category": "other",
        },
        files={"file": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["id"]

    # Admin acknowledges on behalf (admin already logged in)
    ack = client.post(
        f"/api/v1/documents/{document_id}/acknowledge",
        data={"_csrf": csrf(client)},
    )
    assert ack.status_code == 200

    # Verify acknowledgement recorded
    docs = client.get(f"/api/v1/documents?employee_id={employee_id}")
    my_doc = next(d for d in docs.json() if d["id"] == document_id)
    assert my_doc["acknowledged_at"] is not None
    assert my_doc["acknowledged_by"] is not None

    # Employee cannot acknowledge again (already acknowledged)
    login_employee(second_client)
    ack2 = second_client.post(
        f"/api/v1/documents/{document_id}/acknowledge",
        data={"_csrf": csrf(second_client)},
    )
    assert ack2.status_code == 409


def test_document_upload_download_authorisation_and_audit(client, second_client):
    login_admin(client)
    manager_id = _employee_id("aarav.sharma@example.com")

    upload = client.post(
        "/api/v1/documents",
        data={
            "_csrf": csrf(client),
            "employee_id": manager_id,
            "title": "Signed NDA",
            "category": "contract",
        },
        files={"file": ("nda.txt", b"confidential nda body", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    document_id = document["id"]
    assert document["title"] == "Signed NDA"
    assert document["employee_id"] == manager_id

    download = client.get(f"/api/v1/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"confidential nda body"
    assert "attachment" in download.headers["content-disposition"]

    login_employee(second_client)
    forbidden = second_client.get(f"/documents/{document_id}/download")
    assert forbidden.status_code == 403

    audit_events = client.get("/api/v1/audit?limit=100")
    assert audit_events.status_code == 200
    items = audit_events.json()["items"]
    actions = {item["action"] for item in items}
    assert "create" in actions
    assert "download" in actions

    documents_page = client.get("/documents")
    assert documents_page.status_code == 200
    assert "Signed NDA" in documents_page.text

    deleted = client.post(
        f"/documents/{document_id}/delete", data={"_csrf": csrf(client)}
    )
    assert deleted.status_code == 303
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 404


def test_document_upload_rejects_disallowed_file_type(client):
    login_admin(client)
    employee_id = _employee_id("diya.nair@example.com")
    response = client.post(
        "/api/v1/documents",
        data={
            "_csrf": csrf(client),
            "employee_id": employee_id,
            "title": "Executable",
            "category": "other",
        },
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


def test_audit_page_filters_and_pagination(client):
    login_admin(client)
    page = client.get("/admin/audit")
    assert page.status_code == 200
    assert "Audit log" in page.text
    assert "login" in page.text.lower()

    filtered = client.get("/admin/audit?action=login")
    assert filtered.status_code == 200
    assert "Login" in filtered.text

    exported = client.get("/api/v1/audit")
    assert exported.status_code == 200
    body = exported.json()
    assert body["total"] >= 1
    assert body["limit"] == 50

from __future__ import annotations

import html
import re
from uuid import UUID

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import Department, Organization, Position
from tests.v2.helpers import (
    csrf,
    extract_csrf,
    login,
    login_admin,
)

ONE_TIME_PATTERN = re.compile(r'<code class="mono">([^<]+)</code>')


def test_department_and_position_lifecycle(client):
    login_admin(client)
    created = client.post(
        "/organization/departments",
        data={
            "_csrf": csrf(client),
            "name": "Operations",
            "code": "ops",
            "cost_center": "CC-100",
        },
    )
    assert created.status_code == 303
    assert "msg=Department+created" in created.headers["location"]

    page = client.get("/organization")
    assert page.status_code == 200
    assert "Operations" in page.text
    assert "OPS" in page.text

    with session_scope() as db:
        department = db.scalar(select(Department).where(Department.code == "OPS"))
        assert department is not None
        department_id = str(department.id)

    duplicate = client.post(
        "/organization/departments",
        data={
            "_csrf": csrf(client),
            "name": "Operations duplicate",
            "code": "OPS",
        },
    )
    assert duplicate.status_code == 303
    assert "error=" in duplicate.headers["location"]
    assert "already" in duplicate.headers["location"]

    position = client.post(
        "/organization/positions",
        data={
            "_csrf": csrf(client),
            "title": "Operations Lead",
            "code": "OPS-L1",
            "department_id": department_id,
            "grade": "G7",
            "level": "L1",
        },
    )
    assert position.status_code == 303
    assert "msg=Position+created" in position.headers["location"]

    with session_scope() as db:
        saved = db.scalar(select(Position).where(Position.code == "OPS-L1"))
        assert saved is not None
        assert str(saved.department_id) == department_id

    toggled = client.post(
        f"/organization/departments/{department_id}/toggle",
        data={"_csrf": csrf(client)},
    )
    assert toggled.status_code == 303
    with session_scope() as db:
        department = db.get(Department, UUID(department_id))
        assert department is not None and department.active is False


def test_organisation_settings_update(client):
    login_admin(client)
    response = client.post(
        "/admin/settings",
        data={
            "_csrf": csrf(client),
            "name": "PeaWork Renamed",
            "legal_name": "PeaWork Renamed Pvt Ltd",
            "timezone": "Asia/Kolkata",
            "currency": "inr",
            "address": "12 Example Road, Gurugram",
        },
    )
    assert response.status_code == 303
    assert "msg=Settings+saved" in response.headers["location"]

    with session_scope() as db:
        org = db.scalar(select(Organization))
        assert org is not None
        assert org.name == "PeaWork Renamed"
        assert org.currency == "INR"
        assert org.address == "12 Example Road, Gurugram"

    page = client.get("/admin/settings")
    assert page.status_code == 200
    assert "PeaWork Renamed" in page.text


def test_admin_user_creation_and_forced_password_change(client, second_client):
    login_admin(client)
    page = client.get("/admin/users")
    assert page.status_code == 200

    created = client.post(
        "/admin/users",
        data={
            "_csrf": csrf(client),
            "email": "new.hr@example.com",
            "full_name": "New HR Partner",
            "role": "hr",
            "employee_id": "",
        },
    )
    assert created.status_code == 200, created.text
    assert "One-time password" in created.text
    match = ONE_TIME_PATTERN.search(created.text)
    assert match, "Generated password was not rendered"
    temporary_password = html.unescape(match.group(1))

    login(second_client, "new.hr@example.com", temporary_password)
    blocked = second_client.get("/")
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/profile")

    profile = second_client.get("/profile")
    assert profile.status_code == 200
    assert "temporary password" in profile.text.lower()

    changed = second_client.post(
        "/profile/password",
        data={
            "_csrf": csrf(second_client),
            "current_password": temporary_password,
            "new_password": "ChangedPassword123!",
            "confirm_password": "ChangedPassword123!",
        },
    )
    assert changed.status_code == 303
    assert changed.headers["location"].startswith("/login")

    login(second_client, "new.hr@example.com", "ChangedPassword123!")
    assert second_client.get("/").status_code == 200

    with session_scope() as db:
        from peawork.models import User

        account = db.scalar(select(User).where(User.email == "new.hr@example.com"))
        assert account is not None
        assert account.must_change_password is False
        assert account.role.value == "hr"


def test_last_admin_guard_and_self_deactivation_guard(client):
    login_admin(client)
    me = client.get("/api/v1/me").json()

    demote = client.post(
        f"/admin/users/{me['id']}/role",
        data={"_csrf": extract_csrf(client.get('/admin/users').text), "role": "hr"},
    )
    assert demote.status_code == 303
    assert "last+active+administrator" in demote.headers["location"]

    deactivate = client.post(
        f"/admin/users/{me['id']}/active",
        data={"_csrf": extract_csrf(client.get('/admin/users').text), "active": "0"},
    )
    assert deactivate.status_code == 303
    assert "cannot+deactivate+yourself" in deactivate.headers["location"]


def test_non_admin_cannot_manage_users(client):
    login(client, "manager@example.com", "ManagerDemo123!")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/organization").status_code == 200
    response = client.post(
        "/organization/departments",
        data={"_csrf": csrf(client), "name": "Shadow", "code": "SHD"},
    )
    assert response.status_code == 403


def test_calendar_and_profile_pages_render(client):
    login(client, "employee@example.com", "EmployeeDemo123!")
    assert client.get("/leave/calendar").status_code == 200
    profile = client.get("/profile")
    assert profile.status_code == 200
    assert "My profile" in profile.text
    assert "Diya Nair" in profile.text

    updated = client.post(
        "/profile",
        data={
            "_csrf": csrf(client),
            "phone": "+91 90000 00000",
            "personal_email": "diya.personal@example.net",
        },
    )
    assert updated.status_code == 303
    assert "msg=Profile+updated" in updated.headers["location"]

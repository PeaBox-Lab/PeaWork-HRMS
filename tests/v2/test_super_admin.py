"""Admin vs super admin behaviour: creation gates, roles and masters access."""

from __future__ import annotations

from sqlalchemy import select

from peawork.db import session_scope
from peawork.domain import Role
from peawork.models import Department, Organization, User
from peawork.security import hash_password
from peawork.services import policy

from tests.v2.conftest import ADMIN_EMAIL
from tests.v2.helpers import api_headers, csrf, login, login_admin

SUB_ADMIN_EMAIL = "sub-admin@example.com"
SUB_ADMIN_PASSWORD = "SubAdmin123!"
SUB_MANAGER_EMAIL = "sub-manager@example.com"
SUB_MANAGER_PASSWORD = "SubManager123!"


def _org(db) -> Organization:
    return db.scalar(select(Organization).order_by(Organization.created_at).limit(1))


def _admin_user(db, org_id=None) -> User:
    """A regular (non-super) administrator."""
    user = User(
        org_id=org_id or db.scalar(select(Organization.id).limit(1)),
        email=SUB_ADMIN_EMAIL,
        full_name="Sub Admin",
        password_hash=hash_password(SUB_ADMIN_PASSWORD),
        role=Role.ADMIN,
        active=True,
    )
    db.add(user)
    db.commit()
    return user


def _add_user(db, org_id, email, full_name, role=Role.EMPLOYEE) -> User:
    user = User(
        org_id=org_id,
        email=email,
        full_name=full_name,
        password_hash=hash_password(SUB_ADMIN_PASSWORD),
        role=role,
        active=True,
    )
    db.add(user)
    db.flush()
    return user


def _manager_id(db) -> str:
    value = db.scalar(select(User.id).where(User.email == SUB_MANAGER_EMAIL))
    if value is None:
        _add_user(db, _org(db).id, SUB_MANAGER_EMAIL, "Sub Manager", role=Role.MANAGER)
        db.commit()
        value = db.scalar(select(User.id).where(User.email == SUB_MANAGER_EMAIL))
    return str(value)


def test_bootstrap_account_is_super_admin(client):
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert user is not None
        assert user.role == Role.SUPER_ADMIN
        assert user.is_super_admin
        assert policy.is_admin(user)
        assert policy.is_super_admin(user)
        assert policy.visible_org_ids(user) is None


def test_admin_can_create_non_admin_users(client):
    login_admin(client)
    token = csrf(client)
    response = client.post(
        "/admin/users",
        data={"_csrf": token, "email": SUB_MANAGER_EMAIL, "full_name": "Site Manager", "role": "manager"},
    )
    assert response.status_code == 200
    assert SUB_MANAGER_EMAIL in response.text


def test_admin_cannot_create_admin_user(client):
    with session_scope() as db:
        _admin_user(db)
    login(client, SUB_ADMIN_EMAIL, SUB_ADMIN_PASSWORD)
    token = csrf(client)
    response = client.post(
        "/admin/users",
        data={"_csrf": token, "email": "other-admin@example.com", "full_name": "Other", "role": "admin"},
    )
    assert response.status_code == 303
    location = response.headers.get("location", "").lower()
    assert "super" in location and "admin" in location
    with session_scope() as db:
        assert db.scalar(select(User).where(User.email == "other-admin@example.com")) is None


def test_admin_cannot_promote_to_admin(client):
    with session_scope() as db:
        _admin_user(db)
        manager_id = _manager_id(db)
        db.commit()
    login(client, SUB_ADMIN_EMAIL, SUB_ADMIN_PASSWORD)
    token = csrf(client)
    response = client.post(f"/admin/users/{manager_id}/role", data={"_csrf": token, "role": "admin"})
    assert response.status_code == 303
    location = response.headers.get("location", "").lower()
    assert "super" in location and "add" in location
    with session_scope() as db:
        assert db.scalar(select(User).where(User.email == SUB_MANAGER_EMAIL)).role == Role.MANAGER


def test_superadmin_creates_admin_and_is_admin(client):
    login_admin(client)
    token = csrf(client)
    response = client.post(
        "/admin/users",
        data={"_csrf": token, "email": SUB_ADMIN_EMAIL, "full_name": "Sub Admin", "role": "admin"},
    )
    assert response.status_code == 200
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == SUB_ADMIN_EMAIL))
        assert user is not None
        assert user.role == Role.ADMIN
        assert not user.is_super_admin
        assert policy.is_admin(user)


def test_superadmin_can_promote_to_admin(client):
    login_admin(client)
    token = csrf(client)
    with session_scope() as db:
        manager_id = db.scalar(select(User.id).where(User.email == SUB_MANAGER_EMAIL)) or _manager_id(db)
        db.commit()
    response = client.post(f"/admin/users/{manager_id}/role", data={"_csrf": token, "role": "admin"})
    assert response.status_code == 303
    with session_scope() as db:
        target = db.scalar(select(User).where(User.email == SUB_MANAGER_EMAIL))
        assert target is not None
        assert target.role == Role.ADMIN


def test_superadmin_sees_all_masters(client):
    with session_scope() as db:
        other_org = Organization(name="Second Org", country_code="DE", currency="EUR")
        db.add(other_org)
        db.flush()
        db.add(
            Department(
                org_id=other_org.id, name="Engineering", code="ENGG", active=True
            )
        )
        db.commit()

    login_admin(client)
    headers = api_headers(client)
    response = client.get("/api/v1/departments", headers=headers)
    assert response.status_code == 200, response.text
    names = [row["name"] for row in response.json()]
    assert "Engineering" in names


def test_regular_admin_cannot_see_other_org_masters(client):
    with session_scope() as db:
        _admin_user(db)
        other_org = Organization(name="Secret Org", country_code="DE", currency="EUR")
        db.add(other_org)
        db.flush()
        db.add(
            Department(
                org_id=other_org.id, name="Coffee Station", code="COFFEE", active=True
            )
        )
        db.commit()

    login(client, SUB_ADMIN_EMAIL, SUB_ADMIN_PASSWORD)
    headers = api_headers(client)
    response = client.get("/api/v1/departments", headers=headers)
    assert response.status_code == 200, response.text
    names = [row["name"] for row in response.json()]
    assert "Coffee Station" not in names

from __future__ import annotations

from tests.v2.helpers import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    csrf,
    extract_csrf,
    login,
    login_admin,
)


def test_anonymous_requests_are_redirected_or_rejected(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

    api = client.get("/api/v1/me", headers={"Accept": "application/json"})
    assert api.status_code == 401
    assert api.headers["content-type"].startswith("application/problem+json")


def test_api_rejects_anonymous_employee_access(client):
    response = client.get("/api/v1/employees", headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_login_requires_matching_csrf_token(client):
    client.get("/login")
    response = client.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 403
    assert "CSRF" in response.text


def test_login_sets_opaque_session_and_supports_logout(client):
    login_admin(client)
    assert client.cookies.get("pw_session")
    assert client.cookies.get("pw_csrf")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.text
    assert ADMIN_EMAIL in dashboard.text

    response = client.post("/logout", data={"_csrf": csrf(client)})
    assert response.status_code == 303
    assert client.get("/").status_code == 303


def test_wrong_password_is_rejected_without_a_session(client):
    page = client.get("/login")
    token = extract_csrf(page.text)
    response = client.post(
        "/login",
        data={"_csrf": token, "email": ADMIN_EMAIL, "password": "WrongPassword123!"},
    )
    assert response.status_code == 303
    assert "error=Invalid" in response.headers["location"]
    assert client.cookies.get("pw_session") is None


def test_repeated_failures_throttle_login(client):
    for _ in range(5):
        page = client.get("/login")
        token = extract_csrf(page.text)
        client.post(
            "/login",
            data={"_csrf": token, "email": ADMIN_EMAIL, "password": "WrongPassword123!"},
        )
    page = client.get("/login")
    token = extract_csrf(page.text)
    response = client.post(
        "/login",
        data={"_csrf": token, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 303
    assert "error=Invalid" in response.headers["location"]
    assert client.cookies.get("pw_session") is None


def test_safe_next_prevents_open_redirect(client):
    page = client.get("/login?next=https://evil.example/")
    token = extract_csrf(page.text)
    response = client.post(
        "/login",
        data={
            "_csrf": token,
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
            "next": "https://evil.example/",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_employee_cannot_read_admin_pages(client):
    login(client, "employee@example.com", "EmployeeDemo123!")
    for path in ("/admin/users", "/admin/audit", "/admin/settings"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert "Something needs your attention" in response.text

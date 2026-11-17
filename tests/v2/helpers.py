from __future__ import annotations

import re
from datetime import date, timedelta

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "AdminTest123!"
MANAGER_EMAIL = "manager@example.com"
MANAGER_PASSWORD = "ManagerDemo123!"
EMPLOYEE_EMAIL = "employee@example.com"
EMPLOYEE_PASSWORD = "EmployeeDemo123!"

CSRF_PATTERN = re.compile(r'name="_csrf"\s+value="([^"]+)"')


def extract_csrf(html: str) -> str:
    match = CSRF_PATTERN.search(html)
    assert match, "CSRF token not found in HTML"
    return match.group(1)


def csrf(client) -> str:
    token = client.cookies.get("pw_csrf")
    if token:
        return token
    page = client.get("/login")
    assert page.status_code == 200
    return extract_csrf(page.text)


def login(client, email: str, password: str):
    page = client.get("/login")
    assert page.status_code == 200
    token = extract_csrf(page.text)
    response = client.post(
        "/login",
        data={"_csrf": token, "email": email, "password": password, "next": "/"},
    )
    assert response.status_code == 303, response.text
    return response


def login_admin(client):
    return login(client, ADMIN_EMAIL, ADMIN_PASSWORD)


def login_manager(client):
    return login(client, MANAGER_EMAIL, MANAGER_PASSWORD)


def login_employee(client):
    return login(client, EMPLOYEE_EMAIL, EMPLOYEE_PASSWORD)


def api_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": csrf(client)}


def next_weekday(offset_days: int = 14) -> date:
    value = date.today() + timedelta(days=offset_days)
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value

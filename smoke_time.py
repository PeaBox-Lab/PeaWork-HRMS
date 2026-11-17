"""End-to-end smoke test for the R2 Time & Attendance endpoints via the live API.

Run against a live server:
    python -m uvicorn peawork.main:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "http://127.0.0.1:8090"
CSRF_PATTERN = re.compile(r'name="_csrf"\s+value="([^"]+)"')
SUFFIX = datetime.now(timezone.utc).strftime("%H%M%S%f")[:9]
week_monday = date.today() - timedelta(weeks=random.randint(8, 150))
week_monday = week_monday - timedelta(days=week_monday.weekday())
punch_day = date.today() - timedelta(days=2)
failures: list[str] = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=30)

    page = c.get("/login")
    m = CSRF_PATTERN.search(page.text)
    token = m.group(1) if m else None
    r = c.post(
        "/login",
        data={"_csrf": token, "email": "admin@example.com", "password": "AdminV2Pass123!", "next": "/"},
    )
    check("POST /login admin -> 303", r.status_code == 303)
    csrf = c.cookies.get("pw_csrf")
    headers = {"X-CSRF-Token": csrf}

    # First employee for employee-scoped calls
    r = c.get("/api/v1/employees?limit=1", headers=headers)
    employees = r.json().get("items", [])
    check("GET /api/v1/employees returns at least one employee", bool(employees))
    if not employees:
        print("No employees to run smoke against.")
        return 1
    emp_id = employees[0]["id"]
    emp_no = employees[0]["employee_no"]

    # Shifts + work schedules
    r = c.post(
        "/api/v1/shifts",
        json={"name": "Smoke Shift", "code": f"SMK-{SUFFIX}", "start_time": "09:30",
              "end_time": "18:30", "break_minutes": 60, "grace_minutes": 10, "minimum_hours": 8},
        headers=headers,
    )
    check("POST /api/v1/shifts -> 201", r.status_code == 201, r.text[:160])
    shift_id = r.json()["id"] if r.status_code == 201 else None

    r = c.post(
        "/api/v1/work-schedules",
        json={"name": "Smoke WeekOff", "code": f"WO-{SUFFIX}", "weekly_off": [6, 7],
              "effective_from": "2026-01-05"},
        headers=headers,
    )
    check("POST /api/v1/work-schedules -> 201", r.status_code == 201, r.text[:160])
    r = c.get("/api/v1/shifts", headers=headers)
    check("GET /api/v1/shifts includes smoke shift", r.status_code == 200 and any(s["code"] == f"SMK-{SUFFIX}" for s in r.json()))

    # Attendance record + list + reconcile
    ist = "+05:30"
    r = c.put(
        f"/api/v1/attendance/{emp_id}/{punch_day.isoformat()}",
        json={"check_in": f"{punch_day.isoformat()}T09:00:00{ist}",
              "check_out": f"{punch_day.isoformat()}T19:00:00{ist}",
              "shift_id": shift_id, "source": "manual"},
        headers=headers,
    )
    check("PUT attendance -> 200 with derived figures", r.status_code == 200 and r.json()["worked_minutes"] == 540, r.text[:160])

    last_week = date.today() - timedelta(days=date.today().weekday() + 28)
    r = c.post("/api/v1/attendance/reconcile", json={"on_date": last_week.isoformat()}, headers=headers)
    check("POST /api/v1/attendance/reconcile -> 200", r.status_code == 200, r.text[:160])

    # Timesheet lifecycle (random past Monday avoids collisions on reruns)
    r = c.post(
        "/api/v1/timesheets",
        json={"week_start": week_monday.isoformat(), "employee_id": emp_id},
        headers=headers,
    )
    check("POST /api/v1/timesheets -> 201", r.status_code == 201, r.text[:160])
    timesheet_id = r.json()["id"] if r.status_code == 201 else None
    if timesheet_id:
        r = c.post(
            f"/api/v1/timesheets/{timesheet_id}/entries",
            json={"work_date": week_monday.isoformat(), "activity": "Smoke task",
                  "hours": 8, "project_code": "SMK"},
            headers=headers,
        )
        check("POST timesheet entry -> 201", r.status_code == 201, r.text[:160])
        r = c.post(f"/api/v1/timesheets/{timesheet_id}/submit", json={}, headers=headers)
        check("POST timesheet submit -> 200", r.status_code == 200, r.text[:160])
        r = c.post(f"/api/v1/timesheets/{timesheet_id}/decision",
                   json={"decision": "approve", "comment": "smoke"}, headers=headers)
        check("POST timesheet decision approve -> 200", r.status_code == 200 and r.json()["status"] == "approved", r.text[:160])

    # Biometric device + HMAC-signed punch
    r = c.post(
        "/api/v1/biometric-devices",
        json={"name": "Smoke Gate", "device_code": f"SMK-{SUFFIX[:6]}", "location": "Lab"},
        headers=headers,
    )
    check("POST /api/v1/biometric-devices -> 201 with secret", r.status_code == 201 and bool(r.json().get("secret")), r.text[:160])
    if r.status_code == 201:
        secret = r.json()["secret"]
        code = r.json()["device_code"]
        body = json.dumps({"punches": [{"employee_no": emp_no,
                                        "attendance_date": punch_day.isoformat(),
                                        "check_in": f"{punch_day.isoformat()}T08:45:00{ist}"}]}).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = c.post("/api/v1/devices/punches", content=body,
                   headers={"X-Device-Code": code, "X-Signature": sig,
                            "Content-Type": "application/json"})
        check("POST /api/v1/devices/punches (HMAC) -> 200", r.status_code == 200 and r.json().get("punches_applied") == 1, r.text[:160])

    # Jobs queue
    r = c.post(
        "/api/v1/jobs/enqueue",
        json={"name": "attendance_reconciliation", "payload": {"on_date": last_week.isoformat()}},
        headers=headers,
    )
    check("POST /api/v1/jobs/enqueue -> 201", r.status_code == 201, r.text[:160])
    r = c.get("/api/v1/jobs", headers=headers)
    check("GET /api/v1/jobs -> 200 list", r.status_code == 200 and isinstance(r.json(), list))

    # Calendar
    month = date.today().strftime("%Y-%m")
    r = c.get(f"/api/v1/calendar/team?month={month}", headers=headers)
    check("GET /api/v1/calendar/team -> 200 merged grid", r.status_code == 200 and "employees" in r.json(), r.text[:160])

    if failures:
        print(f"\nTime & attendance smoke checks FAILED: {', '.join(failures)}")
        return 1
    print("\nAll time & attendance smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

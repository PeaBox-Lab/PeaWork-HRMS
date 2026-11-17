from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import (
    Employee,
    LeaveBalance,
    LeaveType,
    Shift,
)
from peawork.services.attendance import derive
from peawork.services import jobs as jobs_service
from tests.v2.helpers import (
    api_headers,
    login_admin,
    login_employee,
    login_manager,
)

TZ = ZoneInfo("Asia/Kolkata")


def _emp_id(employee_no: str = "EMP-0002") -> str:
    with session_scope() as db:
        employee = db.scalar(select(Employee).where(Employee.employee_no == employee_no))
        assert employee is not None
        return str(employee.id)


def _shift_model() -> Shift:
    return Shift(
        name="General Shift",
        code="GEN",
        start_time=time(9, 30),
        end_time=time(18, 30),
        break_minutes=60,
        grace_minutes=10,
        minimum_hours=Decimal("8"),
    )


def _loc(d: date, h: int, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _punch_day() -> date:
    return date.today() - timedelta(days=3)


# --- Rule engine (pure) ---


def test_derive_present_on_time():
    day = _punch_day()
    shift = _shift_model()
    worked, overtime, late, status = derive(
        _loc(day, 9, 15), _loc(day, 18, 30), shift, TZ
    )
    assert late == 0
    assert worked == 8 * 60 + 15
    assert overtime == 15
    assert status == "present"


def test_derive_late_beyond_grace():
    day = _punch_day()
    shift = _shift_model()
    _, _, late, status = derive(_loc(day, 10, 0), _loc(day, 18, 30), shift, TZ)
    assert late == 20  # 09:30 + 10min grace = 09:40; 10:00 - 09:40
    assert status == "late"


def test_derive_half_day():
    day = _punch_day()
    shift = _shift_model()
    worked, _, _, status = derive(_loc(day, 9, 30), _loc(day, 12, 30), shift, TZ)
    assert worked == 120  # 180 - 60 break
    assert status == "half_day"


def test_derive_no_shift_defaults_8h():
    day = _punch_day()
    check_in = _loc(day, 9, 0)
    check_out = _loc(day, 17, 30)
    worked, overtime, late, status = derive(check_in, check_out, None, TZ)
    assert worked == 510
    assert overtime == 30  # beyond default 480
    assert late == 0
    assert status == "present"


# --- Attendance API ---


def _ist(d: date, h: int, m: int = 0) -> str:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).isoformat()


def test_attendance_upsert_derives_worked_and_overtime(client):
    login_admin(client)
    headers = api_headers(client)
    emp = _emp_id()
    day = _punch_day()
    resp = client.put(
        f"/api/v1/attendance/{emp}/{day.isoformat()}",
        json={
            "check_in": _ist(day, 9, 0),
            "check_out": _ist(day, 19, 0),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # shift absent -> 10h worked with 0 break, 120 overtime over default 8h
    assert body["worked_minutes"] == 600
    assert body["overtime_minutes"] == 120
    assert body["source"] == "manual"


def test_attendance_upsert_rejects_naive_datetime(client):
    login_admin(client)
    headers = api_headers(client)
    emp = _emp_id()
    day = _punch_day()
    resp = client.put(
        f"/api/v1/attendance/{emp}/{day.isoformat()}",
        json={"check_in": f"{day.isoformat()}T09:00:00"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_attendance_upsert_rejects_future_punch(client):
    login_admin(client)
    headers = api_headers(client)
    emp = _emp_id()
    day = (date.today() + timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    resp = client.put(
        f"/api/v1/attendance/{emp}/{day}",
        json={"check_in": future},
        headers=headers,
    )
    assert resp.status_code == 422


def test_attendance_upsert_rejects_out_before_in(client):
    login_admin(client)
    headers = api_headers(client)
    emp = _emp_id()
    day = _punch_day()
    resp = client.put(
        f"/api/v1/attendance/{emp}/{day.isoformat()}",
        json={"check_in": _ist(day, 18, 0), "check_out": _ist(day, 9, 0)},
        headers=headers,
    )
    assert resp.status_code == 422


def test_machine_punch_merging_first_in_last_out(client):
    login_admin(client)
    headers = api_headers(client)
    emp = _emp_id()
    day = _punch_day()
    url = f"/api/v1/attendance/{emp}/{day.isoformat()}"
    client.put(
        url, headers=headers,
        json={"check_in": _ist(day, 9, 30), "check_out": _ist(day, 18, 0), "source": "biometric"},
    )
    resp = client.put(
        url, headers=headers,
        json={"check_in": _ist(day, 8, 45), "check_out": _ist(day, 19, 15), "source": "biometric"},
    )
    body = resp.json()
    # earliest check-in wins (08:45 = 03:15Z over 09:30 = 04:00Z), last check-out wins
    assert body["check_in"] == day.isoformat() + "T03:15:00+00:00"
    assert body["check_out"] == day.isoformat() + "T13:45:00+00:00"  # 19:15 IST


def test_attendance_import_csv_all_or_nothing(client):
    login_admin(client)
    headers = api_headers(client)
    day = _punch_day()
    content = (
        "employee_no,attendance_date,check_in,check_out,status,notes\n"
        f"EMP-0002,{day.isoformat()},{_ist(day, 9, 0)},{_ist(day, 18, 0)},,\n"
        f"EMP-9999,{day.isoformat()},{_ist(day, 9, 0)},{_ist(day, 18, 0)},,\n"
    )
    resp = client.post(
        "/api/v1/attendance/import",
        files={"file": ("att.csv", content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 422
    # nothing imported — all-or-nothing contract
    resp = client.get(
        f"/api/v1/attendance?start={day.isoformat()}&end={day.isoformat()}",
        headers=headers,
    )
    assert resp.json() == []


def test_attendance_import_csv_success(client):
    login_admin(client)
    headers = api_headers(client)
    day = _punch_day()
    content = (
        "employee_no,attendance_date,check_in,check_out,status,notes\n"
        f"EMP-0002,{day.isoformat()},{_ist(day, 9, 0)},{_ist(day, 17, 0)},,import\n"
    )
    resp = client.post(
        "/api/v1/attendance/import",
        files={"file": ("att.csv", content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows_imported"] == 1


def test_biometric_device_secret_and_hmac_punch(client):
    login_admin(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/biometric-devices",
        json={"name": "Gate Kiosk", "device_code": "GATE-01", "location": "Main gate"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    secret = resp.json()["secret"]
    assert secret

    day = _punch_day()
    body = json.dumps(
        {"punches": [{"employee_no": "EMP-0002", "attendance_date": day.isoformat(),
                      "check_in": _ist(day, 9, 5)}]}
    ).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/api/v1/devices/punches",
        content=body,
        headers={"X-Device-Code": "GATE-01", "X-Signature": sig,
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["punches_applied"] == 1


def test_biometric_punch_rejected_with_bad_signature(client):
    login_admin(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/biometric-devices",
        json={"name": "Garage Kiosk", "device_code": "GATE-02"},
        headers=headers,
    )
    assert resp.status_code == 201
    body = json.dumps(
        {"punches": [{"employee_no": "EMP-0002",
                      "attendance_date": _punch_day().isoformat()}]}
    ).encode()
    resp = client.post(
        "/api/v1/devices/punches",
        content=body,
        headers={"X-Device-Code": "GATE-02", "X-Signature": "0" * 64,
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_biometric_punch_rejected_unknown_device(client):
    body = json.dumps(
        {"punches": [{"employee_no": "EMP-0002",
                      "attendance_date": _punch_day().isoformat()}]}
    ).encode()
    resp = client.post(
        "/api/v1/devices/punches",
        content=body,
        headers={"X-Device-Code": "NOPE-99", "X-Signature": "0" * 64,
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_attendance_reconcile_idempotent(client):
    login_admin(client)
    headers = api_headers(client)
    # A Monday in the past (not a holiday) so demo employees get ABSENT
    monday = date.today() - timedelta(days=date.today().weekday() + 21)
    resp = client.post(
        "/api/v1/attendance/reconcile",
        json={"on_date": monday.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    first = resp.json()["counts"]
    assert first["ABSENT"] >= 2

    resp = client.post(
        "/api/v1/attendance/reconcile",
        json={"on_date": monday.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 200
    second = resp.json()["counts"]
    assert second["ABSENT"] == 0
    assert second["existing"] >= 2


# --- Timesheets ---


def _week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _create_submitted_timesheet(client, headers) -> str:
    monday = _week_monday().isoformat()
    resp = client.post("/api/v1/timesheets", json={"week_start": monday}, headers=headers)
    assert resp.status_code == 201, resp.text
    timesheet_id = resp.json()["id"]
    resp = client.post(
        f"/api/v1/timesheets/{timesheet_id}/entries",
        json={"work_date": monday, "activity": "Development", "hours": 8,
              "project_code": "PEA"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(f"/api/v1/timesheets/{timesheet_id}/submit", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    return timesheet_id


def test_timesheet_create_requires_monday(client):
    login_employee(client)
    headers = api_headers(client)
    not_monday = _week_monday() + timedelta(days=1)
    resp = client.post(
        "/api/v1/timesheets",
        json={"week_start": not_monday.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 422


def test_timesheet_flow_submit_then_manager_approves(client, second_client):
    login_employee(client)
    headers = api_headers(client)
    timesheet_id = _create_submitted_timesheet(client, headers)

    # entries frozen once submitted
    monday = _week_monday().isoformat()
    resp = client.post(
        f"/api/v1/timesheets/{timesheet_id}/entries",
        json={"work_date": monday, "activity": "More work", "hours": 1},
        headers=headers,
    )
    assert resp.status_code == 422

    login_manager(second_client)
    mgr_headers = api_headers(second_client)
    resp = second_client.post(
        f"/api/v1/timesheets/{timesheet_id}/decision",
        json={"decision": "approve", "comment": "LGTM"},
        headers=mgr_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    # manager may not edit the employee's timesheet at all
    resp = second_client.post(
        f"/api/v1/timesheets/{timesheet_id}/entries",
        json={"work_date": monday, "activity": "Too late", "hours": 1},
        headers=mgr_headers,
    )
    assert resp.status_code == 403


def test_timesheet_manager_cannot_decide_own(client):
    login_manager(client)
    headers = api_headers(client)
    timesheet_id = _create_submitted_timesheet(client, headers)
    resp = client.post(
        f"/api/v1/timesheets/{timesheet_id}/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_timesheet_employee_cannot_create_for_others(client):
    login_employee(client)
    headers = api_headers(client)
    manager_emp = _emp_id("EMP-0001")
    resp = client.post(
        "/api/v1/timesheets",
        json={"week_start": _week_monday().isoformat(), "employee_id": manager_emp},
        headers=headers,
    )
    assert resp.status_code == 403


def test_timesheet_entry_hours_cap(client):
    login_employee(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/timesheets",
        json={"week_start": _week_monday().isoformat()},
        headers=headers,
    )
    timesheet_id = resp.json()["id"]
    resp = client.post(
        f"/api/v1/timesheets/{timesheet_id}/entries",
        json={"work_date": _week_monday().isoformat(), "activity": "Overwork", "hours": 25},
        headers=headers,
    )
    assert resp.status_code == 422


def test_timesheet_submit_requires_entries(client):
    login_employee(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/timesheets",
        json={"week_start": _week_monday().isoformat()},
        headers=headers,
    )
    timesheet_id = resp.json()["id"]
    resp = client.post(f"/api/v1/timesheets/{timesheet_id}/submit", json={}, headers=headers)
    assert resp.status_code == 422


# --- Jobs queue ---


def test_jobs_enqueue_rejects_unknown_type(client):
    login_admin(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/jobs/enqueue",
        json={"name": "bogus_job", "payload": {}, "idempotency_key": "bogus-1"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_jobs_enqueue_validates_accrual_payload(client):
    login_admin(client)
    headers = api_headers(client)
    resp = client.post(
        "/api/v1/jobs/enqueue",
        json={"name": "leave_accrual", "payload": {"period": "not-a-month"}},
        headers=headers,
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/v1/jobs/enqueue",
        json={"name": "leave_accrual", "payload": {"period": "2026-11"}},
        headers=headers,
    )
    assert resp.status_code == 422  # missing leave_type_id


def test_jobs_enqueue_idempotent_and_accrual_exactly_once(client):
    login_admin(client)
    headers = api_headers(client)
    with session_scope() as db:
        leave_type = db.scalar(select(LeaveType).limit(1))
        assert leave_type is not None
        entitlement = Decimal(str(leave_type.annual_entitlement))
        years_before = {
            b.employee_id: b.accrued
            for b in db.scalars(select(LeaveBalance).where(LeaveBalance.leave_type_id == leave_type.id))
        }
        leave_type_id = str(leave_type.id)

    payload = {
        "name": "leave_accrual",
        "payload": {"period": "2026-11", "leave_type_id": leave_type_id},
    }
    resp1 = client.post("/api/v1/jobs/enqueue", json=payload, headers=headers)
    assert resp1.status_code == 201, resp1.text
    resp2 = client.post("/api/v1/jobs/enqueue", json=payload, headers=headers)
    assert resp2.status_code == 201
    assert resp1.json()["id"] == resp2.json()["id"]  # same idempotency key

    with session_scope() as db:
        result = jobs_service.run_pending_jobs(db)
    assert result["succeeded"] == 1

    expected = (entitlement / Decimal(12)).quantize(Decimal("0.01"))
    with session_scope() as db:
        balances = db.scalars(
            select(LeaveBalance).where(LeaveBalance.leave_type_id == leave_type.id)
        ).all()
        for balance in balances:
            before = years_before.get(balance.employee_id, Decimal(0))
            assert Decimal(str(balance.accrued)) == Decimal(str(before)) + expected

    # Enqueue the same accrual under a different key: handler must skip (no double credit).
    payload["idempotency_key"] = "accrual-manual-retry"
    resp3 = client.post("/api/v1/jobs/enqueue", json=payload, headers=headers)
    assert resp3.status_code == 201
    with session_scope() as db:
        result = jobs_service.run_pending_jobs(db)
    assert result["succeeded"] == 1
    with session_scope() as db:
        for balance in db.scalars(
            select(LeaveBalance).where(LeaveBalance.leave_type_id == leave_type.id)
        ).all():
            before = years_before.get(balance.employee_id, Decimal(0))
            assert Decimal(str(balance.accrued)) == Decimal(str(before)) + expected


def test_jobs_dead_letter_and_retry(client):
    login_admin(client)
    headers = api_headers(client)
    with session_scope() as db:
        anchor = db.scalar(select(Employee).limit(1))
        assert anchor is not None
        job = jobs_service.enqueue(
            db,
            org_id=anchor.org_id,
            actor=None,
            name="unhandled_type",
            payload={},
            idempotency_key="dead-letter-test-1",
            max_attempts=1,
        )
        job_id = str(job.id)

    with session_scope() as db:
        result = jobs_service.run_pending_jobs(db)
    assert result["dead_lettered"] == 1

    resp = client.post(f"/api/v1/jobs/{job_id}/retry", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
    assert resp.json()["attempts"] == 0


def test_jobs_list_requires_admin_role(client):
    login_employee(client)
    headers = api_headers(client)
    resp = client.get("/api/v1/jobs", headers=headers)
    assert resp.status_code == 403


def test_attendance_reconciliation_job(client):
    login_admin(client)
    headers = api_headers(client)
    monday = (date.today() - timedelta(days=date.today().weekday() + 28)).isoformat()
    resp = client.post(
        "/api/v1/jobs/enqueue",
        json={"name": "attendance_reconciliation", "payload": {"on_date": monday}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    with session_scope() as db:
        result = jobs_service.run_pending_jobs(db)
    assert result["succeeded"] == 1


# --- Team calendar ---


def test_calendar_scoping_employee_vs_admin(client, second_client):
    login_employee(client)
    month = date.today().strftime("%Y-%m")
    resp = client.get(f"/api/v1/calendar/team?month={month}", headers=api_headers(client))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["employees"]) == 1

    login_admin(second_client)
    resp = second_client.get(
        f"/api/v1/calendar/team?month={month}", headers=api_headers(second_client)
    )
    assert resp.status_code == 200
    assert len(resp.json()["employees"]) == 2  # both demo employees
    for row in resp.json()["employees"]:
        first = date(int(month[:4]), int(month[5:]), 1)
        assert len(row["days"]) >= 28
        assert row["days"][0]["date"] == first.isoformat()


def test_employee_attendance_scope_ignores_other_employee(client):
    login_employee(client)
    headers = api_headers(client)
    manager_emp = _emp_id("EMP-0001")
    today = date.today()
    resp = client.get(
        f"/api/v1/attendance?start={today.isoformat()}&end={today.isoformat()}&employee_id={manager_emp}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []

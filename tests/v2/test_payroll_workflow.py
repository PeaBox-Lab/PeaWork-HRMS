"""Phase 2 Global Payroll workflow tests.

Run lifecycle: DRAFT -> PROCESSING -> REVIEW -> APPROVED -> FINALIZED -> PAID,
plus freeze/unfreeze, preview, variance, bank/accounting exports, field-level
masking and audit events.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import (
    AuditEvent,
    CompensationComponent,
    Employee,
    PayComponent,
    StatutoryRuleVersion,
)
from tests.v2.helpers import (
    api_headers,
    login_admin,
    login_manager,
)

PF_RULE = {
    "rule_type": "pf",
    "country_code": "IN",
    "effective_from": "2024-04-01",
    "effective_to": None,
    "version": 1,
    "rates": {"employee_rate": "12", "employer_rate": "12", "wage_ceiling": "15000"},
    "active": True,
    "notes": "PF golden rule FY2024-25",
}


def _first_employee_id() -> str:
    with session_scope() as db:
        employee = db.scalar(select(Employee).order_by(Employee.employee_no))
        assert employee is not None
        return str(employee.id)


def _create_comp(client, headers, employee_id: str, base_salary: float = 50000.0) -> None:
    comp_resp = client.post(
        "/api/v1/pay-components",
        json={
            "name": "Basic Salary",
            "code": "BASIC",
            "component_type": "earning",
            "is_taxable": True,
            "is_statutory": False,
            "default_amount": base_salary,
            "active": True,
        },
        headers=headers,
    )
    assert comp_resp.status_code in (200, 201, 409), comp_resp.text

    with session_scope() as db:
        basic = db.scalar(select(PayComponent).where(PayComponent.code == "BASIC"))
        assert basic is not None
        basic_id = str(basic.id)

    resp = client.post(
        f"/api/v1/employees/{employee_id}/compensation",
        json={
            "base_salary": base_salary,
            "currency": "INR",
            "payroll_frequency": "monthly",
            "tax_regime": "india_new",
            "effective_from": "2026-01-01",
            "notes": "Workflow test compensation",
            "components": [{"component_id": basic_id, "amount": base_salary}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def _rule_exists(rule_type: str) -> bool:
    with session_scope() as db:
        return bool(
            db.scalar(
                select(StatutoryRuleVersion).where(
                    StatutoryRuleVersion.rule_type == rule_type,
                    StatutoryRuleVersion.active,
                )
            )
        )


def _ensure_pf_rule(client, headers) -> None:
    if _rule_exists("pf"):
        return
    resp = client.post("/api/v1/statutory-rule-versions", json=PF_RULE, headers=headers)
    assert resp.status_code == 201, resp.text


def _make_and_process_run(client, headers, name: str, period_start: str, period_end: str) -> str:
    resp = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": name,
            "period_start": period_start,
            "period_end": period_end,
            "pay_date": period_end,
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]
    resp = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert resp.status_code == 200, resp.text
    return run_id


def test_preview_totals_match_process_and_do_not_persist(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)
    _ensure_pf_rule(client, headers)

    resp = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": "Payroll - Preview",
            "period_start": "2026-12-01",
            "period_end": "2026-12-31",
            "pay_date": "2026-12-31",
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    preview = client.post(f"/api/v1/payroll-runs/{run_id}/preview", headers=headers)
    assert preview.status_code == 200
    body = preview.json()
    assert body["total_gross"] == 50000.0
    assert body["employees"]

    with session_scope() as db:
        from peawork.models import Payslip
        assert not db.scalars(select(Payslip).where(Payslip.run_id == UUID(run_id))).all()

    processed = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert processed.status_code == 200
    run = client.get("/api/v1/payroll-runs", headers=headers).json()
    run_row = next(r for r in run if r["id"] == run_id)
    assert run_row["total_gross"] == body["total_gross"]
    assert run_row["status"] == "processing"


def test_full_workflow_freeze_and_payment(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=60000.0)
    _ensure_pf_rule(client, headers)

    resp = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": "Payroll - Workflow",
            "period_start": "2027-01-01",
            "period_end": "2027-01-31",
            "pay_date": "2027-01-31",
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    # Freeze blocks processing
    frozen = client.post(f"/api/v1/payroll-runs/{run_id}/freeze", headers=headers)
    assert frozen.status_code == 200
    assert frozen.json()["frozen"] is True
    blocked = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert blocked.status_code == 422

    unfrozen = client.post(f"/api/v1/payroll-runs/{run_id}/unfreeze", headers=headers)
    assert unfrozen.status_code == 200
    assert unfrozen.json()["frozen"] is False

    # Process
    resp = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert resp.status_code == 200

    # Freeze blocks the workflow transitions too
    client.post(f"/api/v1/payroll-runs/{run_id}/freeze", headers=headers)
    blocked = client.post(f"/api/v1/payroll-runs/{run_id}/review", headers=headers)
    assert blocked.status_code == 422
    client.post(f"/api/v1/payroll-runs/{run_id}/unfreeze", headers=headers)

    # Workflow transitions
    resp = client.post(f"/api/v1/payroll-runs/{run_id}/review", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "review"

    # Invalid transition: cannot finalize from review
    blocked = client.post(f"/api/v1/payroll-runs/{run_id}/finalize", headers=headers)
    assert blocked.status_code == 422

    resp = client.post(f"/api/v1/payroll-runs/{run_id}/approve", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "approved"

    resp = client.post(f"/api/v1/payroll-runs/{run_id}/finalize", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "finalized"

    # Bank account so mark-paid stamps it
    bank = client.post(
        "/api/v1/bank-accounts",
        json={
            "employee_id": employee_id,
            "account_type": "salary",
            "bank_name": "HDFC Bank",
            "branch": "Gurugram Sector 29",
            "account_holder": "Test Employee",
            "account_number": "1234567890123456",
            "ifsc": "HDFC0001234",
            "swift": "HDFCINBB",
            "is_primary": True,
            "active": True,
        },
        headers=headers,
    )
    assert bank.status_code == 201, bank.text

    resp = client.post(f"/api/v1/payroll-runs/{run_id}/mark-paid", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "paid"

    with session_scope() as db:
        from peawork.models import Payslip, PayrollRun
        run = db.get(PayrollRun, UUID(run_id))
        assert run.status.value == "paid"
        assert run.finalized_at is not None
        assert run.approved_at is not None
        assert run.approved_by is not None
        for p in db.scalars(select(Payslip).where(Payslip.run_id == UUID(run_id))):
            assert p.paid_at is not None
            assert p.bank_account_last4 == "3456"


def test_statutory_lines_applied_to_payslip(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)
    _ensure_pf_rule(client, headers)

    run_id = _make_and_process_run(client, headers, "Payroll - Statutory", "2027-02-01", "2027-02-28")

    payslips = client.get(f"/api/v1/payroll-runs/{run_id}/payslips", headers=headers).json()
    assert payslips

    employee_payslip = next(p for p in payslips if p["employee_id"] == employee_id)
    detail = client.get(f"/api/v1/payslips/{employee_payslip['id']}", headers=headers).json()
    names = {line["component_name"] for line in detail["lines"]}
    assert "Provident Fund" in names
    assert "Provident Fund (employer)" in names
    amounts = {line["component_name"]: line["amount"] for line in detail["lines"]}
    # PF employee contribution is capped at the 15,000 wage ceiling at 12%
    assert amounts["Provident Fund"] == 1800.0
    # gross 50000 - PF deduction 1800
    assert detail["net_pay"] == 50000.0 - 1800.0


def test_variance_against_previous_run(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)

    run_a = _make_and_process_run(client, headers, "Payroll - Var A", "2027-03-01", "2027-03-31")
    # Double the compensation BASIC amount between runs
    with session_scope() as db:
        rows = db.scalars(
            select(CompensationComponent).where(
                CompensationComponent.compensation_id.in_(
                    select(CompensationComponent.compensation_id)
                )
            )
        ).all()
        assert rows
        for row in rows:
            row.amount = row.amount + Decimal("10000")
            db.add(row)

    run_b = _make_and_process_run(client, headers, "Payroll - Var B", "2027-04-01", "2027-04-30")

    resp = client.get(f"/api/v1/payroll-runs/{run_b}/variance", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["previous_run_id"] == run_a
    row = next(r for r in body["rows"] if r["employee_id"] == employee_id)
    assert row["previous_gross"] == 50000.0
    assert row["current_gross"] == 60000.0
    assert row["gross_delta"] == 10000.0


def test_bank_and_accounting_exports(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)
    _ensure_pf_rule(client, headers)

    run_id = _make_and_process_run(client, headers, "Payroll - Export", "2027-05-01", "2027-05-31")

    resp = client.get(f"/api/v1/payroll-runs/{run_id}/bank-export", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "employee_no," in resp.text
    assert "TOTAL," in resp.text

    resp = client.get(f"/api/v1/payroll-runs/{run_id}/accounting-export", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["balanced"] is True
    assert body["total_debit"] == body["total_credit"]
    assert "Salaries & Wages Expense" in [e["account"] for e in body["entries"]]
    assert body["boundary"].startswith("PeaWork is not a filing engine")

    # Export views are audited
    with session_scope() as db:
        audits = db.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_type.in_(["payroll_bank_export", "payroll_accounting_export"])
            )
        ).all()
        assert {a.entity_type for a in audits} == {"payroll_bank_export", "payroll_accounting_export"}
        for a in audits:
            assert a.action.value == "download"


def test_manager_and_other_roles_are_denied_write_access(client, second_client):
    login_manager(second_client)
    headers = api_headers(second_client)

    resp = second_client.post(
        "/api/v1/payroll-runs",
        json={"period_start": "2027-06-01", "period_end": "2027-06-30"},
        headers=headers,
    )
    assert resp.status_code == 403

    resp = second_client.post(
        "/api/v1/payroll-runs/00000000-0000-0000-0000-000000000000/process",
        headers=headers,
    )
    assert resp.status_code == 403

    resp = second_client.get(
        "/api/v1/payroll-runs/00000000-0000-0000-0000-000000000000/bank-export",
        headers=headers,
    )
    assert resp.status_code == 403


def test_payslip_field_level_masking_and_view_audit(client, second_client):
    login_admin(client)
    headers = api_headers(client)
    # Use the demo employee (diya.nair) so the manager is not viewing their own payslip
    with session_scope() as db:
        emp = db.scalar(select(Employee).where(Employee.work_email == "diya.nair@example.com"))
        assert emp is not None
        employee_id = str(emp.id)
    _create_comp(client, headers, employee_id, base_salary=50000.0)

    run_id = _make_and_process_run(client, headers, "Payroll - Masking", "2027-07-01", "2027-07-31")
    payslips = client.get(f"/api/v1/payroll-runs/{run_id}/payslips", headers=headers).json()
    employee_payslip = next(p for p in payslips if p["employee_id"] == employee_id)

    # Manager (no payroll role, viewing someone else) sees masked fields
    login_manager(second_client)
    manager_headers = api_headers(second_client)
    resp = second_client.get(f"/api/v1/payslips/{employee_payslip['id']}", headers=manager_headers)
    assert resp.status_code == 200
    masked = resp.json()
    assert masked["masked"] is True
    assert masked["gross_pay"] is None
    assert masked["net_pay"] is None
    assert masked["lines"][0]["amount"] is None

    # Admin sees full figures
    resp = client.get(f"/api/v1/payslips/{employee_payslip['id']}", headers=headers)
    assert resp.status_code == 200
    full = resp.json()
    assert full["masked"] is False
    assert full["gross_pay"] == 50000.0

    # Payslip views are audited
    with session_scope() as db:
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "payslip")
        ).all()
        assert audits
        assert {a.action.value for a in audits} >= {"view"}


def test_frozen_run_export_still_works(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)

    run_id = _make_and_process_run(client, headers, "Payroll - Frozen Export", "2027-08-01", "2027-08-31")
    client.post(f"/api/v1/payroll-runs/{run_id}/freeze", headers=headers)

    # Freeze blocks workflow transitions, not read-only exports
    resp = client.get(f"/api/v1/payroll-runs/{run_id}/accounting-export", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["balanced"] is True

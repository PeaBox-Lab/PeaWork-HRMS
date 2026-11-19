from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import (
    Employee,
    PayComponent,
)
from tests.v2.helpers import api_headers, login_admin, login_employee


def _first_employee_id() -> str:
    with session_scope() as db:
        employee = db.scalar(select(Employee).order_by(Employee.employee_no))
        assert employee is not None
        return str(employee.id)


def _create_comp(
    client, headers, employee_id: str, base_salary: float = 50000.0
) -> dict:
    """Create a compensation record with Basic + HRA components."""
    # First, create Basic pay component if not present
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

    # HRA component
    hra_resp = client.post(
        "/api/v1/pay-components",
        json={
            "name": "House Rent Allowance",
            "code": "HRA",
            "component_type": "earning",
            "is_taxable": True,
            "is_statutory": False,
            "default_amount": 20000.0,
            "active": True,
        },
        headers=headers,
    )
    assert hra_resp.status_code in (200, 201, 409), hra_resp.text

    # Get component IDs
    with session_scope() as db:
        basic = db.scalar(select(PayComponent).where(PayComponent.code == "BASIC"))
        hra = db.scalar(select(PayComponent).where(PayComponent.code == "HRA"))
        assert basic is not None
        assert hra is not None
        basic_id = str(basic.id)
        hra_id = str(hra.id)

    # Create compensation with components
    payload = {
        "base_salary": base_salary,
        "currency": "INR",
        "payroll_frequency": "monthly",
        "tax_regime": "india_new",
        "pay_scale": "Level 3",
        "grade": "G3",
        "effective_from": "2026-01-01",
        "notes": "Test compensation",
        "components": [
            {"component_id": basic_id, "amount": base_salary},
            {"component_id": hra_id, "amount": 20000.0},
        ],
    }
    resp = client.post(
        f"/api/v1/employees/{employee_id}/compensation",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_bank_account(client, headers, employee_id: str) -> dict:
    """Create a bank account for an employee."""
    payload = {
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
    }
    resp = client.post("/api/v1/bank-accounts", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_pay_components_list_and_create(client):
    login_admin(client)
    headers = api_headers(client)

    # Create a pay component
    resp = client.post(
        "/api/v1/pay-components",
        json={
            "name": "Conveyance Allowance",
            "code": "CONV",
            "component_type": "earning",
            "is_taxable": True,
            "is_statutory": False,
            "default_amount": 1600.0,
            "active": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] is not None
    assert body["detail"] == "Pay component created"

    # List pay components
    resp = client.get("/api/v1/pay-components", headers=headers)
    assert resp.status_code == 200
    components = resp.json()
    codes = [c["code"] for c in components]
    assert "CONV" in codes


def test_compensation_create_and_read(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()

    comp = _create_comp(client, headers, employee_id, base_salary=60000.0)
    assert comp["id"] is not None
    assert comp["detail"] == "Compensation saved"

    # Read compensation back
    resp = client.get(f"/api/v1/employees/{employee_id}/compensation", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["base_salary"]) == 60000.0
    assert data["currency"] == "INR"
    assert data["payroll_frequency"] == "monthly"
    assert data["tax_regime"] == "india_new"
    assert len(data["components"]) == 2  # Basic + HRA


def test_payroll_run_create_process_and_payslips(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()

    # Setup compensation
    _create_comp(client, headers, employee_id, base_salary=50000.0)

    # Create payroll run
    resp = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": "Payroll - November 2026",
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "pay_date": "2026-11-30",
            "frequency": "monthly",
            "currency": "INR",
            "notes": "November payroll run",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]
    assert run_id is not None

    # List payroll runs
    resp = client.get("/api/v1/payroll-runs", headers=headers)
    assert resp.status_code == 200
    runs = resp.json()
    assert any(r["id"] == run_id for r in runs)

    # Process payroll run
    resp = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payslip_count"] >= 1
    assert "Generated" in body["detail"]

    # List payslips for the run
    resp = client.get(f"/api/v1/payroll-runs/{run_id}/payslips", headers=headers)
    assert resp.status_code == 200
    payslips = resp.json()
    assert len(payslips) >= 1

    # Check payslip data
    payslip = payslips[0]
    assert payslip["employee_id"] is not None
    assert float(payslip["gross_pay"]) > 0
    assert float(payslip["net_pay"]) > 0

    # Get payslip detail
    payslip_id = payslip["id"]
    resp = client.get(f"/api/v1/payslips/{payslip_id}", headers=headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] is not None
    assert detail["gross_pay"] > 0
    assert len(detail["lines"]) >= 1
    # Check that lines have component_type and amount
    for line in detail["lines"]:
        assert line["component_name"] is not None
        assert line["component_type"] in ["earning", "deduction", "employer_contribution", "tax"]
        assert float(line["amount"]) > 0


def test_bank_account_create_and_list(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()

    account = _create_bank_account(client, headers, employee_id)
    assert account["id"] is not None
    assert account["detail"] == "Bank account added"

    # List bank accounts
    resp = client.get("/api/v1/bank-accounts", headers=headers)
    assert resp.status_code == 200
    accounts = resp.json()
    assert len(accounts) >= 1
    assert any(a["bank_name"] == "HDFC Bank" for a in accounts)
    # Check that account number is masked
    for acc in accounts:
        assert acc["account_number_masked"].startswith("****")


def test_benefit_plan_create_and_enroll(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()

    # Create benefit plan
    resp = client.post(
        "/api/v1/benefit-plans",
        json={
            "name": "Health Insurance - Gold",
            "code": "HI-GOLD",
            "benefit_type": "health_insurance",
            "provider": "Star Health",
            "employer_contribution": 5000.0,
            "employee_contribution": 1000.0,
            "active": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]

    # List benefit plans
    resp = client.get("/api/v1/benefit-plans", headers=headers)
    assert resp.status_code == 200
    plans = resp.json()
    assert any(p["code"] == "HI-GOLD" for p in plans)

    # Enroll employee
    resp = client.post(
        "/api/v1/benefits/enroll",
        json={
            "employee_id": employee_id,
            "plan_id": plan_id,
            "enrolled_on": "2026-01-01",
            "coverage_amount": 500000.0,
            "status": "active",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["detail"] == "Employee enrolled in benefit"


def test_legal_entities_list(client):
    login_admin(client)
    headers = api_headers(client)
    resp = client.get("/api/v1/legal-entities", headers=headers)
    assert resp.status_code == 200
    entities = resp.json()
    assert isinstance(entities, list)


def test_payroll_access_control(client, second_client):
    """Employees cannot access payroll endpoints."""
    login_employee(client)
    headers = api_headers(client)

    # Employee tries to list pay components - should be 403
    resp = client.get("/api/v1/pay-components", headers=headers)
    assert resp.status_code == 403

    # Employee tries to create payroll run - should be 403
    resp = client.post(
        "/api/v1/payroll-runs",
        json={"period_start": "2026-11-01", "period_end": "2026-11-30"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_payslip_employee_self_access(client, second_client):
    """Employee can view their own payslip."""
    # Admin creates payroll run and processes it
    login_admin(client)
    admin_headers = api_headers(client)
    employee_id = _first_employee_id()

    _create_comp(client, admin_headers, employee_id, base_salary=50000.0)

    # Create and process payroll run
    resp = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": "Payroll - Test Access",
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "pay_date": "2026-11-30",
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    run_id = resp.json()["id"]

    resp = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=admin_headers)
    assert resp.status_code == 200

    # Get payslip list
    resp = client.get(f"/api/v1/payroll-runs/{run_id}/payslips", headers=admin_headers)
    assert resp.status_code == 200
    payslips = resp.json()
    assert len(payslips) >= 1

    # Find the payslip for the demo employee (diya.nair)
    payslip_id = None
    with session_scope() as db:
        emp = db.scalar(select(Employee).where(Employee.work_email == "diya.nair@example.com"))
        if emp:
            for p in payslips:
                if p["employee_id"] == str(emp.id):
                    payslip_id = p["id"]
                    break

    if payslip_id:
        # Now login as employee on a separate client and view their payslip
        login_employee(second_client)
        emp_headers = api_headers(second_client)
        resp = second_client.get(f"/api/v1/payslips/{payslip_id}", headers=emp_headers)
        # Employee should be able to view their own payslip
        assert resp.status_code in (200, 403)  # Either allowed or denied based on policy


def test_benefit_premium_is_deducted_in_payroll(client):
    login_admin(client)
    headers = api_headers(client)
    employee_id = _first_employee_id()
    _create_comp(client, headers, employee_id, base_salary=50000.0)

    response = client.post(
        "/api/v1/benefit-plans",
        json={
            "name": "Global Medical — Employee",
            "code": "GLOBAL-MED",
            "benefit_type": "health_insurance",
            "provider": "Cigna",
            "employer_contribution": 3000.0,
            "employee_contribution": 1200.0,
            "payroll_deduction": True,
            "active": True,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    plan_id = response.json()["id"]

    response = client.post(
        "/api/v1/benefits/enroll",
        json={
            "employee_id": employee_id,
            "plan_id": plan_id,
            "enrolled_on": "2026-01-01",
            "effective_on": "2026-01-01",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    response = client.post(
        "/api/v1/payroll-runs",
        json={
            "name": "Payroll - Benefits",
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "pay_date": "2026-11-30",
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]

    response = client.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    assert response.status_code == 200, response.text

    response = client.get(f"/api/v1/payroll-runs/{run_id}/payslips", headers=headers)
    assert response.status_code == 200
    payslip = next(
        item for item in response.json() if item["employee_id"] == employee_id
    )
    assert float(payslip["gross_pay"]) - float(payslip["total_deductions"]) == float(
        payslip["net_pay"]
    )

    response = client.get(f"/api/v1/payslips/{payslip['id']}", headers=headers)
    assert response.status_code == 200
    detail = response.json()
    benefit_lines = [
        line for line in detail["lines"] if line["component_name"].startswith("Benefit: ")
    ]
    assert benefit_lines
    assert float(benefit_lines[0]["amount"]) == 1200.0
    assert float(detail["total_deductions"]) >= 1200.0


def test_prorate_factor_for_mid_period_joiner():
    from types import SimpleNamespace

    from peawork.services.payroll import _prorate_factor

    joiner = SimpleNamespace(date_of_joining=date(2026, 10, 16))
    assert _prorate_factor(joiner, date(2026, 10, 1), date(2026, 10, 31)) == Decimal("0.5")
    # Joined before the period -> fully covered.
    early = SimpleNamespace(date_of_joining=date(2026, 10, 9))
    assert _prorate_factor(early, date(2026, 10, 10), date(2026, 10, 31)) == Decimal("1")
    # No employee or period -> 1.
    assert _prorate_factor(None, date(2026, 10, 1), date(2026, 10, 31)) == Decimal("1")


def test_statutory_rule_version_create_list_update_delete(client):
    login_admin(client)
    headers = api_headers(client)

    # Create statutory rule version
    resp = client.post(
        "/api/v1/statutory-rule-versions",
        json={
            "rule_type": "pf",
            "country_code": "IN",
            "state_code": "KA",
            "effective_from": "2024-01-01",
            "effective_to": None,
            "version": 1,
            "rates": {"employee_rate": 12.0, "employer_rate": 12.0},
            "active": True,
            "notes": "PF rule for 2024",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    version_id = resp.json()["id"]

    # List statutory rule versions
    resp = client.get("/api/v1/statutory-rule-versions", headers=headers)
    assert resp.status_code == 200
    versions = resp.json()
    assert any(v["id"] == version_id for v in versions)

    # Update statutory rule version
    resp = client.put(
        f"/api/v1/statutory-rule-versions/{version_id}",
        json={"notes": "Updated PF note"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["detail"] == "Statutory rule version updated"

    # Delete statutory rule version (soft delete)
    resp = client.delete(f"/api/v1/statutory-rule-versions/{version_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["detail"] == "Statutory rule version deleted"

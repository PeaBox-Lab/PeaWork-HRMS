from __future__ import annotations

from sqlalchemy import select

from peawork.db import session_scope
from peawork.models import Employee
from tests.v2.helpers import api_headers, csrf, login_admin, login_employee


def _employees(count: int = 2) -> list[Employee]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(Employee).order_by(Employee.employee_no).limit(count)
            ).all()
        )


def test_country_catalog_and_benefit_metadata(client):
    login_admin(client)
    headers = api_headers(client)

    response = client.get("/api/v1/metadata/countries?region=Europe", headers=headers)
    assert response.status_code == 200, response.text
    countries = response.json()
    codes = {item["code"] for item in countries}
    assert {"GB", "DE", "FR", "NL", "ES", "IT", "PL"} <= codes
    assert all(item["reference_only"] is True for item in countries)

    response = client.get("/api/v1/metadata/countries/DE", headers=headers)
    assert response.status_code == 200, response.text
    germany = response.json()
    assert germany["currency"] == "EUR"
    assert germany["schemes"]

    response = client.get("/api/v1/metadata/countries/ZZ", headers=headers)
    assert response.status_code == 404

    response = client.get("/api/v1/metadata/benefit-catalog", headers=headers)
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert "health_insurance" in catalog["benefit_types"]
    assert "family" in catalog["coverage_tiers"]
    assert "spouse" in catalog["dependent_relationships"]


def test_country_plan_defaults_currency_and_checks_eligibility(client):
    login_admin(client)
    headers = api_headers(client)
    employee, other = _employees(2)

    # Employee country drives country-scoped eligibility.
    response = client.patch(
        f"/api/v1/employees/{employee.id}",
        json={"country_code": "DE"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    response = client.patch(
        f"/api/v1/employees/{other.id}",
        json={"country_code": "FR"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    response = client.post(
        "/api/v1/benefit-plans",
        json={
            "name": "European Medical — Family",
            "code": "EU-MED-FAM",
            "benefit_type": "health_insurance",
            "country_code": "DE",
            "provider": "Allianz",
            "policy_number": "AZ-2026-001",
            "coverage_tier": "family",
            "employer_contribution": 420.0,
            "employee_contribution": 95.5,
            "waiting_period_days": 0,
            "eligibility": {"countries": ["DE", "NL"], "employment_types": ["full_time"]},
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    plan_id = response.json()["id"]
    assert plan["currency"] == "EUR"
    assert plan["country_code"] == "DE"
    assert plan["coverage_tier"] == "family"

    # List reflects the enriched plan payload.
    response = client.get("/api/v1/benefit-plans", headers=headers)
    assert response.status_code == 200
    listed = next(item for item in response.json() if item["id"] == plan_id)
    assert listed["employee_contribution"] == 95.5
    assert listed["eligibility"]["countries"] == ["DE", "NL"]

    # Enroll the German employee with a spouse and child.
    response = client.post(
        "/api/v1/benefits/enroll",
        json={
            "employee_id": str(employee.id),
            "plan_id": plan_id,
            "enrolled_on": "2026-01-01",
            "coverage_tier": "family",
            "dependents": [
                {"full_name": "Anna Beispiel", "relationship": "spouse"},
                {
                    "full_name": "Lukas Beispiel",
                    "relationship": "child",
                    "date_of_birth": "2016-04-12",
                },
            ],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    enrollment = response.json()["enrollment"]
    assert enrollment["currency"] == "EUR"
    assert enrollment["employee_premium"] == 95.5
    assert enrollment["employer_premium"] == 420.0
    assert len(enrollment["dependents"]) == 2

    enrollment_id = enrollment["id"]
    response = client.post(
        f"/api/v1/benefits/{enrollment_id}/dependents",
        json={"full_name": "Mia Beispiel", "relationship": "child"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["dependent"]["relationship"] == "child"

    response = client.get(
        f"/api/v1/benefits?employee_id={employee.id}", headers=headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # French employee is outside the plan's country scope.
    response = client.post(
        "/api/v1/benefits/enroll",
        json={
            "employee_id": str(other.id),
            "plan_id": plan_id,
            "enrolled_on": "2026-01-01",
            "coverage_tier": "family",
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "outside the plan" in response.json()["detail"]


def test_employee_can_view_own_benefits(client, second_client):
    login_employee(second_client)
    headers = api_headers(second_client)
    response = second_client.get("/api/v1/benefits/me", headers=headers)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


def test_web_benefits_page_creates_country_plan(client):
    login_admin(client)

    page = client.get("/admin/benefits")
    assert page.status_code == 200, page.text
    assert "Benefits and insurance" in page.text
    assert "Country packs" in page.text

    token = csrf(client)
    response = client.post(
        "/admin/benefits",
        data={
            "_csrf": token,
            "name": "UK Private Medical — Gold",
            "code": "UK-PMI-GOLD",
            "benefit_type": "health_insurance",
            "country_code": "GB",
            "provider": "Bupa",
            "policy_number": "BUPA-001",
            "coverage_tier": "family",
            "currency": "",
            "employer_contribution": "350",
            "employee_contribution": "75",
            "payroll_deduction": "1",
            "waiting_period_days": "0",
            "active": "1",
        },
    )
    assert response.status_code == 303, response.text

    page = client.get("/admin/benefits")
    assert page.status_code == 200
    assert "UK Private Medical" in page.text
    assert "GBP 75.00" in page.text

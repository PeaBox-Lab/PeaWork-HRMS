"""Golden-case tests for the per-country payroll engines (UK, Germany)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from peawork.db import session_scope
from peawork.localization import reference_rule_templates
from peawork.models import Employee, LegalEntity, Organization, User
from peawork.payroll_engines import (
    StatutoryRule,
    banded_contribution,
    compute_country_payroll,
    filings_for,
    get_payroll_engine,
    list_payroll_engines,
    money,
    progressive_tax,
    submission_for_filing,
    tapered_allowance,
)
from peawork.services import payroll as payroll_service

from tests.v2.conftest import ADMIN_EMAIL


def _rules(country: str) -> list[StatutoryRule]:
    return [
        StatutoryRule(
            rule_type=template["rule_type"],
            country_code=template["country_code"],
            rates=template["rates"],
        )
        for template in reference_rule_templates(country)
    ]


def _line(result, code: str):
    for line in result.lines:
        if line.code == code:
            return line
    raise AssertionError(f"missing line {code!r} in {[line.code for line in result.lines]}")


def test_engine_registry_selects_country_engines():
    assert get_payroll_engine("GB").country_code == "GB"
    assert get_payroll_engine("de").country_code == "DE"
    assert get_payroll_engine("IN").country_code == "*"
    assert get_payroll_engine(None).country_code == "*"

    engines = {engine["engine"] for engine in list_payroll_engines()}
    assert {"gb_paye", "de_lohnsteuer", "generic"} <= engines


def test_uk_monthly_payslip_golden():
    result = compute_country_payroll(
        country_code="GB",
        gross=Decimal("5000"),
        currency="GBP",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=_rules("GB"),
    )

    paye = _line(result, "paye")
    ni = _line(result, "national_insurance")

    # Annual 60,000: 20% on 37,700 + 40% on 9,730, divided by 12.
    assert paye.employee_amount == Decimal("952.67")
    assert paye.employer_amount == Decimal("0")
    # NI: 8% between 12,570 and 50,270 plus 2% above; employer 15% above 5,000.
    assert ni.employee_amount == Decimal("267.55")
    assert ni.employer_amount == Decimal("687.50")
    assert result.warnings == ()


def test_uk_personal_allowance_taper():
    # Allowance is fully tapered away at 125,140 annual gross.
    assert tapered_allowance(
        Decimal("125140"), Decimal("12570"), Decimal("100000"), Decimal("50")
    ) == Decimal("0")
    # 110,000: allowance reduced by 5,000.
    assert tapered_allowance(
        Decimal("110000"), Decimal("12570"), Decimal("100000"), Decimal("50")
    ) == Decimal("7570")


def test_germany_monthly_payslip_golden():
    result = compute_country_payroll(
        country_code="DE",
        gross=Decimal("5000"),
        currency="EUR",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=_rules("DE"),
    )

    assert _line(result, "lohnsteuer").employee_amount == Decimal("1201.95")
    assert _line(result, "kv").employee_amount == Decimal("427.50")
    assert _line(result, "kv").employer_amount == Decimal("365.00")
    assert _line(result, "pv").employee_amount == Decimal("120.00")
    assert _line(result, "rv").employer_amount == Decimal("465.00")
    assert _line(result, "av").employee_amount == Decimal("65.00")
    # Solidarity surcharge is zero below its free limit.
    assert result.employee_total == Decimal("2279.45")


def test_germany_social_insurance_respects_ceilings():
    result = compute_country_payroll(
        country_code="DE",
        gross=Decimal("10000"),
        currency="EUR",
        pay_frequency="monthly",
        as_of=date(2026, 6, 30),
        rules=_rules("DE"),
    )
    # Annual 120,000 is above both ceilings: KV/PV capped at 66,150,
    # RV/AV capped at 96,600.
    kv = _line(result, "kv")
    rv = _line(result, "rv")
    assert kv.base == Decimal("10000")
    assert kv.employee_amount == Decimal("471.32")  # 66,150 * 8.55% / 12
    assert rv.employee_amount == Decimal("748.65")  # 96,600 * 9.30% / 12


def test_progressive_and_banded_helpers():
    bands = [
        {"up_to": "100", "rate": "10"},
        {"up_to": None, "rate": "20"},
    ]
    assert progressive_tax(Decimal("50"), bands) == Decimal("5")
    assert progressive_tax(Decimal("200"), bands) == Decimal("30")
    assert progressive_tax(Decimal("0"), bands) == Decimal("0")

    ni_bands = [
        {"up_to": "100", "rate": "0"},
        {"up_to": "200", "rate": "10"},
        {"up_to": None, "rate": "2"},
    ]
    assert banded_contribution(Decimal("150"), ni_bands) == Decimal("5")
    assert banded_contribution(Decimal("250"), ni_bands) == Decimal("11")


def test_uk_filings_descriptions():
    result, docs = filings_for(
        country_code="GB",
        gross=Decimal("5000"),
        as_of=date(2026, 11, 30),
        rules=_rules("GB"),
        currency="GBP",
        employee_id="emp-gb",
    )
    assert [doc.doc_type for doc in docs] == ["FPS", "P60"]
    fps = docs[0]
    assert fps.country_code == "GB"
    assert fps.payload["L4_national_insurance"] == Decimal("267.55")
    assert fps.payload["L5_employee_deductions"] == Decimal("952.67") + Decimal("267.55")
    assert fps.payload["L7_non_pi_tax_free"] == Decimal("5000")
    assert fps.notes

    p60 = docs[1]
    assert p60.payload["tax_year"] == "2025-2026"
    assert p60.payload["total_tax"] == result.employee_total - p60.payload["total_ni"]


def test_uk_termination_p45():
    _result, docs = filings_for(
        country_code="GB",
        gross=Decimal("5000"),
        as_of=date(2026, 11, 30),
        rules=_rules("GB"),
        employee_id="emp-gb",
        status="termination",
    )
    assert [doc.doc_type for doc in docs] == ["P45"]
    assert docs[0].payload["total_tax"] == Decimal("952.67")


def test_germany_duevent_filing():
    result, docs = filings_for(
        country_code="DE",
        gross=Decimal("5000"),
        as_of=date(2026, 11, 30),
        rules=_rules("DE"),
        employee_id="emp-de",
    )
    assert [doc.doc_type for doc in docs] == ["DEUV"]
    assert docs[0].payload["social_insurance_contributions"] == result.employer_total


def test_uk_filing_submissions():
    result, filings = filings_for(
        country_code="GB",
        gross=Decimal("5000"),
        as_of=date(2026, 11, 30),
        rules=_rules("GB"),
        employee_id="emp-gb",
    )
    submission = submission_for_filing(get_payroll_engine("GB"), filings[0])
    assert submission.country_code == "GB"
    assert submission.doc_type == "FPS"
    assert submission.method == "POST"
    assert "hmrc.gov.uk" in submission.url
    assert "fullPaymentSubmissions" in submission.body

    de_result, de_filings = filings_for(
        country_code="DE",
        gross=Decimal("5000"),
        as_of=date(2026, 11, 30),
        rules=_rules("DE"),
        employee_id="emp-de",
    )
    de_submission = submission_for_filing(get_payroll_engine("DE"), de_filings[0])
    assert de_submission.doc_type == "DEUV"
    assert de_submission.method == "POST"
    assert de_submission.body["social_insurance_contributions"] == de_result.employer_total


def test_payroll_filing_submit_endpoint(client):
    from tests.v2.helpers import api_headers, login_admin

    login_admin(client)
    headers = api_headers(client)
    response = client.post(
        "/api/v1/payroll-filings/submit",
        headers=headers,
        json={"country_code": "GB", "gross": "5000", "period_end": "2026-11-30"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["country_code"] == "GB"
    assert payload["doc_type"] == "FPS"
    assert payload["method"] == "POST"

    germany = client.post(
        "/api/v1/payroll-filings/submit",
        headers=headers,
        json={"country_code": "DE", "gross": "5000", "period_end": "2026-11-30"},
    )
    assert germany.status_code == 200, germany.text
    assert germany.json()["doc_type"] == "DEUV"


def test_germany_tax_classes():
    base_result = compute_country_payroll(
        country_code="DE",
        gross=Decimal("5000"),
        currency="EUR",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=_rules("DE"),
    )
    class1 = _line(base_result, "lohnsteuer").employee_amount

    for tax_class in ("2", "3"):
        rules = _rules("DE")
        for rule in rules:
            if rule.rule_type == "income_tax":
                rule.rates["tax_class"] = tax_class
        result = compute_country_payroll(
            country_code="DE",
            gross=Decimal("5000"),
            currency="EUR",
            pay_frequency="monthly",
            as_of=date(2026, 11, 30),
            rules=rules,
        )
        assert _line(result, "lohnsteuer").employee_amount == class1

    # Class 6 keeps the personal allowance allowed for A2/A3 and drops the
    # basic allowance, so Lohnsteuer rises.
    rules = _rules("DE")
    for rule in rules:
        if rule.rule_type == "income_tax":
            rule.rates["tax_class"] = "6"
    result = compute_country_payroll(
        country_code="DE",
        gross=Decimal("5000"),
        currency="EUR",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=rules,
    )
    assert _line(result, "lohnsteuer").employee_amount > class1


def test_germany_church_tax():
    rules = _rules("DE")
    for rule in rules:
        if rule.rule_type == "income_tax":
            rule.rates["church_tax_rate"] = "8"
            rule.rates["church_tax_label"] = "Kirchensteuer"
    result = compute_country_payroll(
        country_code="DE",
        gross=Decimal("5000"),
        currency="EUR",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=rules,
    )
    church_tax = _line(result, "church_tax")
    lohnsteuer = _line(result, "lohnsteuer").employee_amount
    assert church_tax.employee_amount == money(lohnsteuer * Decimal("0.08"))
    assert church_tax.category == "tax"


def test_missing_country_rules_produce_warnings():
    result = compute_country_payroll(
        country_code="GB",
        gross=Decimal("5000"),
        currency="GBP",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=[],
    )
    assert result.lines == ()
    assert any("income_tax" in warning for warning in result.warnings)
    assert any("social_security" in warning for warning in result.warnings)


def test_india_rules_still_use_generic_engine():
    rule = StatutoryRule(
        rule_type="pf",
        country_code="IN",
        rates={"employee_rate": "12", "employer_rate": "12", "wage_ceiling": "15000"},
    )
    result = compute_country_payroll(
        country_code="IN",
        gross=Decimal("20000"),
        currency="INR",
        pay_frequency="monthly",
        as_of=date(2026, 11, 30),
        rules=[rule],
    )
    assert result.engine == "generic"
    assert _line(result, "pf").employee_amount == Decimal("1800.00")


def test_country_rules_are_loaded_per_jurisdiction(client):
    with session_scope() as db:
        org_id = db.scalar(select(Organization.id).limit(1))
        admin = db.scalar(select(User).where(User.email == ADMIN_EMAIL))

        for country in ("GB", "DE"):
            for template in reference_rule_templates(country):
                payroll_service.create_statutory_rule_version(
                    db, org_id=org_id, actor=admin, data=template
                )

        items, result = payroll_service.country_statutory_items(
            db,
            org_id,
            country="GB",
            gross=Decimal("5000"),
            as_of=date(2026, 11, 30),
            pay_frequency="monthly",
            currency="GBP",
        )
        assert result.engine == "gb_paye"
        assert {item["rule_type"] for item in items} == {"paye", "national_insurance"}

        # The same organisation can hold DE rules without a uniqueness clash.
        items_de, result_de = payroll_service.country_statutory_items(
            db,
            org_id,
            country="DE",
            gross=Decimal("5000"),
            as_of=date(2026, 11, 30),
            pay_frequency="monthly",
            currency="EUR",
        )
        assert result_de.engine == "de_lohnsteuer"
        assert "lohnsteuer" in {item["rule_type"] for item in items_de}


def test_employee_country_drives_engine_selection(client):
    with session_scope() as db:
        org_id = db.scalar(select(Organization.id).limit(1))
        gb_employee = Employee(
            org_id=org_id,
            employee_no="T-GB-1",
            first_name="Ada",
            last_name="Lovelace",
            country_code="GB",
        )
        db.add(gb_employee)
        db.flush()

        assert payroll_service.resolve_employee_country(db, org_id, gb_employee) == "GB"
        assert payroll_service.resolve_employee_country(db, org_id, None) == "IN"


def test_legal_entity_country_takes_precedence(client):
    with session_scope() as db:
        org_id = db.scalar(select(Organization.id).limit(1))
        entity = LegalEntity(
            org_id=org_id,
            name="PeaWork Deutschland GmbH",
            code="DE01",
            country_code="DE",
            currency="EUR",
        )
        db.add(entity)
        db.flush()
        employee = Employee(
            org_id=org_id,
            employee_no="T-DE-1",
            first_name="Konrad",
            last_name="Zuse",
            country_code="IN",
            legal_entity_id=entity.id,
        )
        db.add(employee)
        db.flush()

        assert payroll_service.resolve_employee_country(db, org_id, employee) == "DE"


def test_country_metadata_exposes_engine_and_reference_rules(client):
    from tests.v2.helpers import api_headers, login_admin

    login_admin(client)
    headers = api_headers(client)

    engines = client.get("/api/v1/metadata/payroll-engines", headers=headers)
    assert engines.status_code == 200, engines.text
    assert {item["engine"] for item in engines.json()} >= {"gb_paye", "de_lohnsteuer"}

    gb = client.get("/api/v1/metadata/countries/GB", headers=headers)
    assert gb.status_code == 200, gb.text
    payload = gb.json()
    assert payload["payroll_engine"]["engine"] == "gb_paye"
    assert {rule["rule_type"] for rule in payload["reference_rules"]} == {
        "income_tax",
        "social_security",
    }

    india = client.get("/api/v1/metadata/countries/IN", headers=headers)
    assert india.status_code == 200
    assert india.json()["payroll_engine"]["implemented"] is True

"""Golden-case suite for statutory payroll calculations.

PeaWork is NOT a filing engine; these figures are the payroll engine's
arithmetic contract, to be validated and signed off by a qualified payroll
professional before customer go-live (see docs/ROADMAP.md R3).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from peawork.domain import StatutoryRuleType
from peawork.services.payroll import compute_statutory_item

PF = StatutoryRuleType.PF
ESI = StatutoryRuleType.ESI
PT = StatutoryRuleType.PT
TDS = StatutoryRuleType.TDS

WAGES = [
    Decimal("0"), Decimal("1000"), Decimal("9999.99"), Decimal("14000"),
    Decimal("14999.99"), Decimal("15000"), Decimal("15000.01"), Decimal("18000"),
    Decimal("21000"), Decimal("21000.01"), Decimal("25000"), Decimal("33333.33"),
    Decimal("50000"), Decimal("75000"), Decimal("100000"), Decimal("150000"),
    Decimal("250000"), Decimal("500000"),
]


def pf_expected(wages: Decimal, rate: str, ceiling: str | None) -> Decimal:
    if wages <= 0:
        return Decimal("0")
    base = wages
    if ceiling:
        cap = Decimal(ceiling)
        if cap > 0 and wages > cap:
            base = cap
    return (base * Decimal(rate) / Decimal(100)).quantize(Decimal("0.01"))


def esi_expected(wages: Decimal, rate: str, ceiling: str | None) -> Decimal:
    if wages <= 0:
        return Decimal("0")
    if ceiling:
        cap = Decimal(ceiling)
        if wages > cap:
            return Decimal("0")
    return (wages * Decimal(rate) / Decimal(100)).quantize(Decimal("0.01"))


PF_CASES = [
    (wages, rate, ceiling)
    for wages in WAGES
    for rate in ("10", "12")
    for ceiling in ("15000", None)
]

ESI_CASES = [
    (wages, rate, ceiling)
    for wages in WAGES
    for rate in ("0.75", "1.75")
    for ceiling in ("21000", None)
]


@pytest.mark.parametrize("wages,rate,ceiling", PF_CASES)
def test_pf_golden(wages, rate, ceiling):
    rates = {"employee_rate": rate, "employer_rate": rate, "wage_ceiling": ceiling or 0}
    item = compute_statutory_item(PF, wages, rates)
    expected = pf_expected(wages, rate, ceiling)
    if expected == 0:
        assert item is None
    else:
        assert item is not None
        assert item["employee_amount"] == expected
        assert item["employer_amount"] == expected
        assert item["line_type"].value == "deduction"
        assert item["base"] == (min(wages, Decimal(ceiling)) if ceiling else wages)


@pytest.mark.parametrize("wages,rate,ceiling", ESI_CASES)
def test_esi_golden(wages, rate, ceiling):
    rates = {"employee_rate": rate, "employer_rate": "3.25", "wage_ceiling": ceiling or 0, "ceiling_mode": "cutoff"}
    item = compute_statutory_item(ESI, wages, rates)
    expected = esi_expected(wages, rate, ceiling)
    if expected == 0:
        # Employee contribution zero; employer may still apply if within ceiling.
        if item is not None:
            assert item["employee_amount"] == Decimal("0")
        return
    assert item is not None
    assert item["employee_amount"] == expected
    assert item["employer_amount"] == esi_expected(wages, "3.25", ceiling)


PT_CASES = [
    (wages, flat)
    for wages in (Decimal("15000"), Decimal("20000"), Decimal("33333.33"), Decimal("50000"), Decimal("100000"), Decimal("500"))
    for flat in ("150", "200", "250")
]


@pytest.mark.parametrize("wages,flat", PT_CASES)
def test_pt_golden(wages, flat):
    item = compute_statutory_item(PT, wages, {"flat_amount": flat})
    assert item is not None
    assert item["employee_amount"] == Decimal(flat).quantize(Decimal("0.01"))
    assert item["employer_amount"] == Decimal("0.00")
    assert item["line_type"].value == "deduction"


TDS_CASES = [
    (wages, rate)
    for wages in (Decimal("41667"), Decimal("62500"), Decimal("100000"), Decimal("125000"))
    for rate in ("5", "10", "15", "20", "30")
]


@pytest.mark.parametrize("wages,rate", TDS_CASES)
def test_tds_golden(wages, rate):
    item = compute_statutory_item(TDS, wages, {"employee_rate": rate})
    assert item is not None
    assert item["employee_amount"] == (wages * Decimal(rate) / Decimal(100)).quantize(Decimal("0.01"))
    assert item["employer_amount"] == Decimal("0.00")
    assert item["line_type"].value == "tax"


def test_zero_wages_yields_no_item():
    assert compute_statutory_item(PF, Decimal("0"), {"employee_rate": "12"}) is None
    assert compute_statutory_item(ESI, Decimal("0"), {"employee_rate": "0.75"}) is None
    assert compute_statutory_item(PT, Decimal("0"), {"flat_amount": "200"}) is None
    assert compute_statutory_item(TDS, Decimal("0"), {"employee_rate": "10"}) is None


def test_empty_rates_yield_no_item():
    assert compute_statutory_item(PF, Decimal("50000"), {}) is None
    assert compute_statutory_item(ESI, Decimal("21000"), {}) is None
    assert compute_statutory_item(TDS, Decimal("100000"), {"employee_rate": "0"}) is None


def test_half_even_rounding():
    assert compute_statutory_item(PF, Decimal("14166.66"), {"employee_rate": "12"})["employee_amount"] == Decimal("1700.00")
    assert compute_statutory_item(TDS, Decimal("10000.25"), {"employee_rate": "10"})["employee_amount"] == Decimal("1000.02")
    assert compute_statutory_item(TDS, Decimal("10000.35"), {"employee_rate": "10"})["employee_amount"] == Decimal("1000.04")


def test_pf_cap_mode():
    # cap mode: wages above the ceiling contribute on the capped base
    assert compute_statutory_item(PF, Decimal("100000"), {"employee_rate": "12", "wage_ceiling": "15000"})["employee_amount"] == Decimal("1800.00")


def test_esi_cutoff_mode():
    # cutoff mode: above-ceiling wages are not covered at all
    assert compute_statutory_item(ESI, Decimal("100000"), {"employee_rate": "0.75", "wage_ceiling": "21000", "ceiling_mode": "cutoff"}) is None


def test_label_override_used():
    item = compute_statutory_item(PF, Decimal("15000"), {"employee_rate": "12", "label": "PF 2026-27"})
    assert item["name"] == "PF 2026-27"


def test_negative_wages_yield_no_item():
    assert compute_statutory_item(PF, Decimal("-100"), {"employee_rate": "12"}) is None


def test_esci_ceiling_boundary_exact():
    item = compute_statutory_item(ESI, Decimal("21000"), {"employee_rate": "0.75", "wage_ceiling": "21000", "ceiling_mode": "cutoff"})
    assert item is not None
    assert item["employee_amount"] == Decimal("157.50")


def test_esci_above_ceiling_zero():
    item = compute_statutory_item(ESI, Decimal("21000.01"), {"employee_rate": "0.75", "wage_ceiling": "21000", "ceiling_mode": "cutoff"})
    assert item is None


def test_pf_ceiling_exact_and_above():
    assert compute_statutory_item(PF, Decimal("15000"), {"employee_rate": "12", "wage_ceiling": "15000"})["employee_amount"] == Decimal("1800.00")
    assert compute_statutory_item(PF, Decimal("15000.01"), {"employee_rate": "12", "wage_ceiling": "15000"})["employee_amount"] == Decimal("1800.00")
    assert compute_statutory_item(PF, Decimal("100000"), {"employee_rate": "12", "wage_ceiling": "15000"})["base"] == Decimal("15000")


def test_pf_no_ceiling_uncapped():
    item = compute_statutory_item(PF, Decimal("100000"), {"employee_rate": "12"})
    assert item["employee_amount"] == Decimal("12000.00")

"""Per-country payroll engines.

Country payroll is *not* one calculator. Each jurisdiction has its own wage
bases, thresholds, allowance taper, contribution ceilings and filing rules, so
the engine is selected by country and reads its numbers from versioned
``StatutoryRuleVersion`` rows (JSON ``rates``) rather than hard-coding rates in
Python.

Design contract
---------------
* ``PayrollContext`` carries the period gross, pay frequency, tax year and the
  effective-dated rules that apply to the employee's country.
* ``PayrollCountryEngine.compute`` returns ``PayrollLine`` objects with
  employee and employer amounts already scaled back to the payroll period.
* Engines only consume the rule types they declare in ``consumed_rule_types``.
  Anything else falls through to ``generic_items`` so company-specific PF/ESI
  or pension rows keep working.
* The reference rates shipped in ``peawork.localization`` and in the tests are
  planning defaults, not tax advice. Production numbers must be reviewed by a
  qualified payroll professional for the relevant tax year.

Rule schema (``rates`` JSON)
----------------------------
Income tax (``income_tax`` / ``tds``)::

    {
      "label": "PAYE Income Tax",
      "personal_allowance": "12570",
      "allowance_taper_threshold": "100000",
      "allowance_taper_rate": "50",
      "bands": [
        {"up_to": "37700", "rate": "20"},
        {"up_to": "112570", "rate": "40"},
        {"up_to": null, "rate": "45"}
      ]
    }

``bands`` are cumulative thresholds on *taxable* income (after allowance)
unless ``band_basis`` is ``"gross"``, in which case thresholds are gross
income thresholds and the allowance is subtracted first. ``start_rate`` makes
the marginal rate ramp linearly across a band, which is how the German tariff
progression zone is represented.

Social insurance (``social_security``)::

    {
      "label": "National Insurance",
      "employee": [{"up_to": "12570", "rate": "0"}, ...],
      "employer": [{"up_to": "5000", "rate": "0"}, {"up_to": null, "rate": "15"}]
    }

or, for multi-branch systems (Germany), ``components``::

    {"components": [
        {"code": "kv", "name": "Health (KV)", "employee_rate": "8.55",
         "employer_rate": "7.30", "ceiling": "66150"}
    ]}
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Protocol, runtime_checkable

from .domain import StatutoryRuleType
from .localization import get_country_profile, normalize_country

ZERO = Decimal("0")
CENT = Decimal("0.01")

PERIODS_PER_YEAR: dict[str, int] = {
    "annual": 1,
    "monthly": 12,
    "semi_monthly": 24,
    "bi_weekly": 26,
    "fortnightly": 26,
    "weekly": 52,
    "daily": 260,
}

STATUTORY_LABELS: dict[StatutoryRuleType, str] = {
    StatutoryRuleType.PF: "Provident Fund",
    StatutoryRuleType.ESI: "Employee State Insurance",
    StatutoryRuleType.PT: "Professional Tax",
    StatutoryRuleType.TDS: "Tax Deducted at Source",
    StatutoryRuleType.INCOME_TAX: "Income Tax",
    StatutoryRuleType.SOCIAL_SECURITY: "Social Security",
    StatutoryRuleType.PENSION: "Pension",
}


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value))


def money(amount: Decimal) -> Decimal:
    """Round a monetary amount to two places, half-up."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def periods_per_year(pay_frequency: str | None) -> int:
    return PERIODS_PER_YEAR.get((pay_frequency or "monthly").lower(), 12)


# ---------------------------------------------------------------------------
# Pure calculation helpers
# ---------------------------------------------------------------------------


def progressive_tax(income: Decimal, bands: Sequence[Mapping[str, Any]]) -> Decimal:
    """Apply cumulative progressive bands to ``income``.

    Bands are ``{"up_to": threshold|None, "rate": percent, "start_rate": percent}``
    where ``up_to`` is cumulative income (not band width). When ``start_rate``
    is present the marginal rate ramps linearly from ``start_rate`` at the
    bottom of the band to ``rate`` at its top, which lets a jurisdiction model
    a progressive tariff zone with a single band.
    """
    if income <= 0:
        return ZERO
    tax = ZERO
    lower = ZERO
    for band in bands:
        if income <= lower:
            break
        upper_raw = band.get("up_to")
        upper = None if upper_raw in (None, "") else _dec(upper_raw)
        rate = _dec(band.get("rate")) / Decimal("100")
        start_rate = _dec(band.get("start_rate")) / Decimal("100") if band.get("start_rate") not in (None, "") else rate
        top = income if upper is None else min(income, upper)
        width = top - lower
        if width > 0:
            if upper is not None and start_rate != rate and (upper - lower) > 0:
                rise = (rate - start_rate) * (width / (upper - lower))
                average_rate = start_rate + rise / Decimal("2")
            else:
                average_rate = rate
            tax += width * average_rate
        if upper is None:
            break
        lower = upper
    return tax


def banded_contribution(
    income: Decimal, bands: Sequence[Mapping[str, Any]], key: str = "rate"
) -> Decimal:
    """Apply banded rates (e.g. National Insurance) to ``income``."""
    if income <= 0:
        return ZERO
    total = ZERO
    lower = ZERO
    for band in bands:
        upper_raw = band.get("up_to")
        upper = None if upper_raw in (None, "") else _dec(upper_raw)
        rate = _dec(band.get(key)) / Decimal("100")
        top = income if upper is None else min(income, upper)
        if top > lower:
            total += (top - lower) * rate
        if upper is None:
            break
        lower = upper
    return total


def tapered_allowance(
    gross: Decimal,
    allowance: Decimal,
    taper_threshold: Decimal,
    taper_rate: Decimal,
) -> Decimal:
    """Reduce the personal allowance above the taper threshold."""
    if allowance <= 0:
        return ZERO
    if taper_threshold <= 0 or gross <= taper_threshold:
        return allowance
    reduction = (gross - taper_threshold) * taper_rate / Decimal("100")
    return max(ZERO, allowance - reduction)


def shift_bands_to_taxable(
    bands: Sequence[Mapping[str, Any]], allowance: Decimal
) -> list[dict[str, Any]]:
    """Convert gross thresholds into taxable-income thresholds."""
    shifted: list[dict[str, Any]] = []
    for band in bands:
        upper_raw = band.get("up_to")
        shifted.append(
            {
                **band,
                "up_to": None
                if upper_raw in (None, "")
                else str(_dec(upper_raw) - allowance),
            }
        )
    return shifted


def generic_items(
    rules: Sequence["StatutoryRule"], wages: Decimal
) -> list["PayrollLine"]:
    """Legacy flat/ceiling semantics (PF/ESI/PT/TDS and any custom rule).

    This mirrors the long-standing ``compute_statutory_item`` behaviour so
    India keeps working unchanged while other countries gain dedicated engines.
    """
    items: list[PayrollLine] = []
    for rule in rules:
        rates = rule.rates or {}
        if wages <= 0:
            continue
        ceiling = _dec(rates.get("wage_ceiling"))
        if ceiling > 0 and rates.get("ceiling_mode") == "cutoff" and wages > ceiling:
            continue
        base = min(wages, ceiling) if ceiling > 0 else wages

        flat = _dec(rates.get("flat_amount"))
        employee_rate = _dec(rates.get("employee_rate"))
        employer_rate = _dec(rates.get("employer_rate"))
        if flat > 0:
            employee_amount, employer_amount = money(flat), ZERO
        else:
            employee_amount = money(base * employee_rate / Decimal("100"))
            employer_amount = money(base * employer_rate / Decimal("100"))

        if employee_amount == 0 and employer_amount == 0:
            continue
        try:
            rule_type = StatutoryRuleType(rule.rule_type)
        except ValueError:
            rule_type = None
        name = str(rates.get("label") or (STATUTORY_LABELS.get(rule_type) if rule_type else rule.rule_type))
        items.append(
            PayrollLine(
                code=rule.rule_type,
                name=name,
                category="tax" if rule_type in {StatutoryRuleType.TDS, StatutoryRuleType.INCOME_TAX} else "contribution",
                employee_amount=employee_amount,
                employer_amount=employer_amount,
                base=base,
            )
        )
    return items


# ---------------------------------------------------------------------------
# Context and result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatutoryRule:
    """A configured, effective-dated statutory rule row."""

    rule_type: str
    country_code: str
    rates: Mapping[str, Any] = field(default_factory=dict)
    effective_from: date | None = None
    effective_to: date | None = None
    state_code: str | None = None


@dataclass(frozen=True)
class PayrollLine:
    code: str
    name: str
    category: str  # "tax" | "contribution"
    employee_amount: Decimal
    employer_amount: Decimal
    base: Decimal = ZERO
    taxable: bool = True


@dataclass(frozen=True)
class PayrollResult:
    country_code: str
    engine: str
    lines: tuple[PayrollLine, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def employee_total(self) -> Decimal:
        return money(sum((line.employee_amount for line in self.lines), ZERO))

    @property
    def employer_total(self) -> Decimal:
        return money(sum((line.employer_amount for line in self.lines), ZERO))


@dataclass(frozen=True)
class PayrollContext:
    country_code: str
    gross: Decimal
    currency: str
    pay_frequency: str
    tax_year: int
    tax_year_start: date
    period_start: date | None = None
    period_end: date | None = None
    tax_regime: str | None = None
    rules: tuple[StatutoryRule, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def periods(self) -> int:
        return periods_per_year(self.pay_frequency)

    @property
    def annual_gross(self) -> Decimal:
        return money(self.gross * self.periods)

    def rule(self, rule_type: StatutoryRuleType) -> StatutoryRule | None:
        wanted = rule_type.value if hasattr(rule_type, "value") else str(rule_type)
        for rule in self.rules:
            if rule.rule_type == wanted:
                return rule
        return None

    def leftover_rules(self, consumed: frozenset[str]) -> list[StatutoryRule]:
        return [rule for rule in self.rules if rule.rule_type not in consumed]


@runtime_checkable
class PayrollCountryEngine(Protocol):
    """Contract implemented by each country payroll engine."""

    country_code: str
    name: str
    consumed_rule_types: frozenset[str]

    def compute(self, context: PayrollContext) -> PayrollResult:  # pragma: no cover - protocol
        ...

    def capabilities(self) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class FilingSubmission:
    """A prepared legal filing ready to send to the local tax authority.

    ``method`` is "POST" for APIs and "PDF" for employee documents. ``url`` is
    the target API endpoint (empty for PDF downloads). Live submission is
    always a separate, reviewed step: the payloads here are skeletons that
    still need the authority's account setup and possibly real signatures.
    """

    country_code: str
    doc_type: str
    method: str
    url: str
    body: Mapping[str, Any]
    notes: str = ""


def submission_for_filing(engine: PayrollCountryEngine, filing: FilingDocument) -> FilingSubmission:
    """Ask the engine for a submission payload for a filing document."""
    handler = getattr(engine, "submission", None)
    if handler is None:
        return FilingSubmission(
            country_code=filing.country_code,
            doc_type=filing.doc_type,
            method="PDF",
            url="",
            body=filing.payload,
            notes="Filing document; real submission not supported for this country yet.",
        )
    return handler(filing)


@dataclass(frozen=True)
class FilingDocument:
    """A country filing (FPS/P60/P45 for the UK, DEÜV for Germany).

    These are reference descriptions built from engine line amounts; they are
    not filings against HMRC or Finanzamt — real submissions go to the local
    tax authority and need a submission account and staff appointments.
    """

    doc_type: str
    country_code: str
    period_start: date | None
    period_end: date | None
    payload: Mapping[str, Any]
    notes: str = ""


def employee_amount_of(result: PayrollResult, code: str) -> Decimal:
    return sum(
        (line.employee_amount for line in result.lines if line.code == code), ZERO
    )


def _filter_lines(result: PayrollResult) -> list[PayrollLine]:
    return [line for line in result.lines if line.employee_amount != 0]


def united_kingdom_filings(
    *,
    context: PayrollContext,
    result: PayrollResult,
    employee_id: str | None,
    status: str = "active",
    tax_code: str | None = None,
) -> list[FilingDocument]:
    """UK Full Payment Submission, P60 and P45 descriptions."""
    tax = sum(
        (line.employee_amount for line in _filter_lines(result) if line.category == "tax"),
        ZERO,
    )
    ni = sum(
        (line.employee_amount for line in _filter_lines(result) if line.category == "contribution"),
        ZERO,
    )
    gross = context.gross
    year_end = date(context.tax_year, 4, 5)

    documents: list[FilingDocument] = []
    if status == "termination":
        documents.append(
            FilingDocument(
                doc_type="P45",
                country_code="GB",
                period_start=context.period_start,
                period_end=context.period_end,
                payload={
                    "employee_id": employee_id,
                    "tax_code": tax_code or "1257L",
                    "gross": money(gross),
                    "total_tax": money(tax),
                    "total_ni": money(ni),
                    "days_terminated": 0,
                },
                notes="P45: statement of last pay paid and deducted at termination.",
            )
        )
        return documents

    documents.append(
        FilingDocument(
            doc_type="FPS",
            country_code="GB",
            period_start=context.period_start,
            period_end=context.period_end,
            payload={
                "employee_id": employee_id,
                "tax_code": tax_code or "1257L",
                "gross_pay": money(gross),
                "L4_national_insurance": money(ni),
                "L5_employee_deductions": money(tax + ni),
                "L6_employee_deductions_other": ZERO,
                "L7_non_pi_tax_free": money(gross),
            },
            notes="Full Payment Submission (FPS) via RTI.",
        )
    )

    # P60 is due at the end of each calendar/tax year once it has been paid.
    tax_year_label = context.tax_year - 1
    if (
        context.period_end is not None
        and context.period_end.year == year_end.year
        and context.period_end >= year_end
    ):
        documents.append(
            FilingDocument(
                doc_type="P60",
                country_code="GB",
                period_start=date(year_end.year - 1, 6, 1),
                period_end=context.period_end,
                payload={
                    "employee_id": employee_id,
                    "tax_year": f"{tax_year_label}-{tax_year_label + 1}",
                    "gross_pay": money(gross),
                    "total_tax": money(tax),
                    "total_ni": money(ni),
                },
                notes="P60: statement of totals paid and deducted in the tax year.",
            )
        )
    return documents


def german_duevent_filings(
    *, context: PayrollContext, result: PayrollResult, employee_id: str | None,
    employers_id: str | None = None, number_of_employees: int = 1,
) -> list[FilingDocument]:
    """German DEÜV employer summary (proof of social insurance contributions)."""
    contributions = sum(
        (line.employer_amount for line in result.lines if line.employer_amount > 0), ZERO
    )
    return [
        FilingDocument(
            doc_type="DEUV",
            country_code="DE",
            period_start=context.period_start,
            period_end=context.period_end,
            payload={
                "arbeitgeberkennummer": employers_id or "",
                "employee_id": employee_id,
                "number_of_employees": number_of_employees,
                "social_insurance_contributions": money(contributions),
            },
            notes="DEÜV (GZS1): summary to the health funds of employer contributions.",
        )
    ]


def filings_for(
    *,
    country_code: str,
    gross: Decimal,
    as_of: date,
    rules: Sequence[StatutoryRule],
    currency: str = "EUR",
    pay_frequency: str = "monthly",
    employee_id: str | None = None,
    status: str = "active",
) -> tuple[PayrollResult, list[FilingDocument]]:
    """Compute the result and filing documents for a country/period."""
    start, year = tax_year_window(country_code, as_of)
    engine = get_payroll_engine(country_code)
    context = PayrollContext(
        country_code=normalize_country(country_code),
        gross=gross,
        currency=currency,
        pay_frequency=pay_frequency,
        tax_year=year,
        tax_year_start=start,
        period_end=as_of,
        rules=tuple(rules),
    )
    result = engine.compute(context)
    return result, filing_documents(engine, context, result, employee_id=employee_id, status=status)


def filing_documents(
    engine: PayrollCountryEngine,
    context: PayrollContext,
    result: PayrollResult,
    *,
    employee_id: str | None = None,
    status: str = "active",
) -> list[FilingDocument]:
    """Dispatch filings through the engine when it implements them."""
    handler = getattr(engine, "filing_documents", None)
    if handler is None:
        return []
    return handler(context, result, employee_id=employee_id, status=status)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


class GenericRuleEngine:
    """Fallback engine: effective-dated flat/ceiling rules, no country logic."""

    country_code = "*"
    name = "Generic statutory rules"
    consumed_rule_types: frozenset[str] = frozenset()

    def compute(self, context: PayrollContext) -> PayrollResult:
        lines = generic_items(context.rules, context.gross)
        return PayrollResult(
            country_code=context.country_code, engine="generic", lines=tuple(lines)
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "engine": "generic",
            "name": self.name,
            "implemented": True,
            "supported_rule_types": [],
            "notes": "Applies configured flat/ceiling rules; no progressive country logic.",
        }


class UnitedKingdomEngine:
    """UK PAYE income tax plus Class 1 National Insurance.

    Income tax uses the personal allowance, the 60% effective taper above
    £100,000 and the basic/higher/additional bands from the configured rule.
    National Insurance uses annual banded rates and is reported per pay period.
    """

    country_code = "GB"
    name = "UK PAYE and National Insurance"
    consumed_rule_types = frozenset(
        {StatutoryRuleType.INCOME_TAX.value, StatutoryRuleType.SOCIAL_SECURITY.value}
    )

    def compute(self, context: PayrollContext) -> PayrollResult:
        lines: list[PayrollLine] = []
        warnings: list[str] = []
        annual = context.annual_gross

        tax_rule = context.rule(StatutoryRuleType.INCOME_TAX)
        if tax_rule is None:
            warnings.append("No UK income_tax rule configured; PAYE not calculated.")
        else:
            rates = tax_rule.rates or {}
            allowance = tapered_allowance(
                annual,
                _dec(rates.get("personal_allowance")),
                _dec(rates.get("allowance_taper_threshold")),
                _dec(rates.get("allowance_taper_rate")),
            )
            taxable = max(ZERO, annual - allowance)
            bands = list(rates.get("bands") or [])
            if rates.get("band_basis") == "gross":
                bands = shift_bands_to_taxable(bands, allowance)
            annual_tax = progressive_tax(taxable, bands)
            period_tax = money(annual_tax / context.periods)
            if period_tax > 0:
                lines.append(
                    PayrollLine(
                        code="paye",
                        name=str(rates.get("label") or "PAYE Income Tax"),
                        category="tax",
                        employee_amount=period_tax,
                        employer_amount=ZERO,
                        base=context.gross,
                    )
                )

        ni_rule = context.rule(StatutoryRuleType.SOCIAL_SECURITY)
        if ni_rule is None:
            warnings.append(
                "No UK social_security rule configured; National Insurance not calculated."
            )
        else:
            rates = ni_rule.rates or {}
            employee_ni = banded_contribution(annual, list(rates.get("employee") or []))
            employer_ni = banded_contribution(annual, list(rates.get("employer") or []))
            employee_period = money(employee_ni / context.periods)
            employer_period = money(employer_ni / context.periods)
            if employee_period > 0 or employer_period > 0:
                lines.append(
                    PayrollLine(
                        code="national_insurance",
                        name=str(rates.get("label") or "National Insurance"),
                        category="contribution",
                        employee_amount=employee_period,
                        employer_amount=employer_period,
                        base=context.gross,
                    )
                )

        lines.extend(generic_items(context.leftover_rules(self.consumed_rule_types), context.gross))
        return PayrollResult(
            country_code=context.country_code,
            engine="gb_paye",
            lines=tuple(lines),
            warnings=tuple(warnings),
        )

    def filing_documents(self, context, result, *, employee_id=None, status="active"):
        if self.country_code != "GB":
            return []
        tax_code = None
        for line in result.lines:
            if line.category == "tax":
                tax_code = context.metadata.get("uk_tax_code") or None
                break
        return united_kingdom_filings(
            context=context, result=result, employee_id=employee_id, status=status,
            tax_code=tax_code,
        )

    def submission(self, filing: FilingDocument) -> FilingSubmission:
        if filing.doc_type == "FPS":
            return FilingSubmission(
                country_code="GB",
                doc_type="FPS",
                method="POST",
                url="https://test-api.service.hmrc.gov.uk/forms/eps/1.0/submit/full-payment-submission",
                body={
                    "schemaVersion": 1,
                    "epsVersion": 1.0,
                    "fullPaymentSubmissions": [
                        {"formType": "FPS", **filing.payload}
                    ],
                },
                notes=(
                    "HMRC EPS full-payment submission. Needs an HMRC API account and "
                    "HMRC OAuth/digital signing; live submission is pending HMRC setup."
                ),
            )
        if filing.doc_type in {"P45", "P60"}:
            return FilingSubmission(
                country_code="GB",
                doc_type=filing.doc_type,
                method="PDF",
                url="",
                body=dict(filing.payload),
                notes=(
                    "Export as PDF; P60s are employee documents, P45s for leavers "
                    "are accepted by HMRC via the online services portal."
                ),
            )
        return FilingSubmission(
            country_code="GB",
            doc_type=filing.doc_type,
            method="PDF",
            url="",
            body=dict(filing.payload),
            notes="No HMRC submission path for this filing type yet.",
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "engine": "gb_paye",
            "name": self.name,
            "implemented": True,
            "supported_rule_types": sorted(self.consumed_rule_types),
            "filings": ["FPS", "P60", "P45"],
            "notes": "FPS submission payload via HMRC EPS; P60/P45 PDF descriptions; live HMRC submission pending.",
        }


class GermanyEngine:
    """German Lohnsteuer approximation plus social insurance branches.

    The tariff uses the configured progression zones (``start_rate`` gives the
    linear ramp of the German tariff), a solidarity surcharge with its free
    limit, and per-branch social insurance with contribution ceilings.
    """

    country_code = "DE"
    name = "German Lohnsteuer and social insurance"
    consumed_rule_types = frozenset(
        {StatutoryRuleType.INCOME_TAX.value, StatutoryRuleType.SOCIAL_SECURITY.value}
    )

    def compute(self, context: PayrollContext) -> PayrollResult:
        lines: list[PayrollLine] = []
        warnings: list[str] = []
        annual = context.annual_gross

        tax_rule = context.rule(StatutoryRuleType.INCOME_TAX)
        if tax_rule is None:
            warnings.append("No German income_tax rule configured; Lohnsteuer not calculated.")
        else:
            rates = tax_rule.rates or {}
            allowance = self._tax_class_allowance(rates)
            taxable = max(ZERO, annual - allowance)
            bands = list(rates.get("bands") or [])
            if rates.get("band_basis", "gross") == "gross":
                bands = shift_bands_to_taxable(bands, allowance)
            annual_tax = progressive_tax(taxable, bands)
            surcharge = self._solidarity_surcharge(annual_tax, rates)
            period_tax = money((annual_tax + surcharge) / context.periods)
            if period_tax > 0:
                lines.append(
                    PayrollLine(
                        code="lohnsteuer",
                        name=str(rates.get("label") or "Lohnsteuer"),
                        category="tax",
                        employee_amount=period_tax,
                        employer_amount=ZERO,
                        base=context.gross,
                    )
                )
                church_rate = _dec(rates.get("church_tax_rate"))
                if church_rate > 0 and annual_tax > 0:
                    church_amount = money(annual_tax * church_rate / Decimal("100") / context.periods)
                    if church_amount > 0:
                        lines.append(
                            PayrollLine(
                                code="church_tax",
                                name=str(rates.get("church_tax_label") or "Kirchensteuer"),
                                category="tax",
                                employee_amount=church_amount,
                                employer_amount=ZERO,
                                base=context.gross,
                            )
                        )

        social_rule = context.rule(StatutoryRuleType.SOCIAL_SECURITY)
        if social_rule is None:
            warnings.append(
                "No German social_security rule configured; contributions not calculated."
            )
        else:
            rates = social_rule.rates or {}
            for component in rates.get("components") or []:
                ceiling = _dec(component.get("ceiling"))
                base = min(annual, ceiling) if ceiling > 0 else annual
                employee_rate = _dec(component.get("employee_rate"))
                employer_rate = _dec(component.get("employer_rate"))
                employee_amount = money(base * employee_rate / Decimal("100") / context.periods)
                employer_amount = money(base * employer_rate / Decimal("100") / context.periods)
                if employee_amount == 0 and employer_amount == 0:
                    continue
                lines.append(
                    PayrollLine(
                        code=str(component.get("code") or "social"),
                        name=str(
                            component.get("name")
                            or rates.get("label")
                            or "Social insurance"
                        ),
                        category="contribution",
                        employee_amount=employee_amount,
                        employer_amount=employer_amount,
                        base=context.gross,
                    )
                )

        lines.extend(generic_items(context.leftover_rules(self.consumed_rule_types), context.gross))
        return PayrollResult(
            country_code=context.country_code,
            engine="de_lohnsteuer",
            lines=tuple(lines),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _tax_class_allowance(rates: Mapping[str, Any]) -> Decimal:
        """Annual taxable-free amount for the configured German tax class.

        Classes A2 and A3 retain the 1x Grundfreibetrag personal allowance;
        A4/A5/A6 are family/single-parent classes where the allowance is set
        explicitly via ``class_allowance`` (defaults to 0).
        """
        tax_class = str(rates.get("tax_class") or "1").upper().lstrip("A")
        if tax_class in ("1", "2", "3"):
            return _dec(rates.get("personal_allowance") or rates.get("basic_allowance"))
        return _dec(rates.get("class_allowance"))

    @staticmethod
    def _solidarity_surcharge(annual_tax: Decimal, rates: Mapping[str, Any]) -> Decimal:
        rate = _dec(rates.get("solidarity_rate"))
        if rate <= 0 or annual_tax <= 0:
            return ZERO
        free_limit = _dec(rates.get("solidarity_free_limit"))
        if free_limit > 0 and annual_tax <= free_limit:
            return ZERO
        return annual_tax * rate / Decimal("100")

    def filing_documents(self, context, result, *, employee_id=None, status="active"):
        if self.country_code != "DE":
            return []
        return german_duevent_filings(
            context=context,
            result=result,
            employee_id=employee_id,
            number_of_employees=1,
        )

    def submission(self, filing: FilingDocument) -> FilingSubmission:
        if filing.doc_type == "DEUV":
            return FilingSubmission(
                country_code="DE",
                doc_type="DEUV",
                method="POST",
                url="https://www.g-icas.net/",
                body=dict(filing.payload),
                notes=(
                    "DEÜV summary to the health funds via GZS1-style API; requires "
                    "a provider account and PIN authentication. Live submission "
                    "is pending provider setup; the payload is for review."
                ),
            )
        return FilingSubmission(
            country_code="DE",
            doc_type=filing.doc_type,
            method="PDF",
            url="",
            body=dict(filing.payload),
            notes="No real submission path for this filing type yet.",
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "engine": "de_lohnsteuer",
            "name": self.name,
            "implemented": True,
            "supported_rule_types": sorted(self.consumed_rule_types),
            "filings": ["DEUV"],
            "tax_classes": ["A1", "A2", "A3", "A4", "A5", "A6"],
            "notes": "DEUV submission payload via GZS1-style API; tax classes A1-A3 share the personal allowance, A4/A5/A6 use class_allowance; church tax modelled. Tax class W and ELStAM are not modelled.",
        }


GENERIC_ENGINE = GenericRuleEngine()

_ENGINES: dict[str, PayrollCountryEngine] = {
    "GB": UnitedKingdomEngine(),
    "DE": GermanyEngine(),
}


def get_payroll_engine(country_code: str | None) -> PayrollCountryEngine:
    """Return the engine for a country, falling back to the generic engine."""
    return _ENGINES.get(normalize_country(country_code), GENERIC_ENGINE)


def list_payroll_engines() -> list[dict[str, Any]]:
    """Describe every implemented engine plus the generic fallback."""
    payloads = [engine.capabilities() for engine in (UnitedKingdomEngine(), GermanyEngine())]
    payloads.append(GENERIC_ENGINE.capabilities())
    return payloads


def tax_year_window(country_code: str | None, as_of: date) -> tuple[date, int]:
    """Return the start date and label of the tax year containing ``as_of``."""
    profile = get_country_profile(country_code)
    month, day = profile.tax_year_start if profile else (1, 1)
    start = date(as_of.year, month, day)
    if as_of < start:
        start = date(as_of.year - 1, month, day)
    return start, start.year


def compute_country_payroll(
    *,
    country_code: str,
    gross: Decimal,
    currency: str,
    pay_frequency: str,
    as_of: date,
    rules: Sequence[StatutoryRule],
    tax_regime: str | None = None,
) -> PayrollResult:
    """Build the context and run the country engine (pure, DB-free)."""
    normalised = normalize_country(country_code)
    start, year = tax_year_window(normalised, as_of)
    context = PayrollContext(
        country_code=normalised,
        gross=gross,
        currency=currency,
        pay_frequency=pay_frequency,
        tax_year=year,
        tax_year_start=start,
        period_end=as_of,
        tax_regime=tax_regime,
        rules=tuple(rules),
    )
    return get_payroll_engine(normalised).compute(context)


__all__ = [
    "PERIODS_PER_YEAR",
    "STATUTORY_LABELS",
    "FilingDocument",
    "FilingSubmission",
    "GenericRuleEngine",
    "GermanyEngine",
    "PayrollContext",
    "PayrollCountryEngine",
    "PayrollLine",
    "PayrollResult",
    "StatutoryRule",
    "UnitedKingdomEngine",
    "banded_contribution",
    "compute_country_payroll",
    "filing_documents",
    "filings_for",
    "generic_items",
    "get_payroll_engine",
    "list_payroll_engines",
    "money",
    "periods_per_year",
    "progressive_tax",
    "shift_bands_to_taxable",
    "submission_for_filing",
    "tapered_allowance",
    "tax_year_window",
]

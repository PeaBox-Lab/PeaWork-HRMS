"""Country profiles for global HR, payroll and benefits.

This module is the single registry for jurisdiction metadata. It deliberately
separates *structure* (which schemes and filings exist) from *rates* (which are
versioned in ``statutory_rule_versions`` by payroll administrators).

The ``reference_*`` rates below are **planning defaults only**. They are
approximate 2026 social-insurance rates intended to make a new country usable
for demos and testing; they are not tax advice and must be reviewed against
current local law before production payroll. Income tax is deliberately not
modelled here: progressive bands and allowances belong in versioned
``StatutoryRuleVersion`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

REFERENCE_YEAR = "2026-reference"


@dataclass(frozen=True)
class StatutoryScheme:
    """A statutory contribution scheme exposed by a country profile."""

    code: str
    name: str
    employee_rate: Decimal | None = None
    employer_rate: Decimal | None = None
    wage_ceiling: Decimal | None = None
    basis: str = "gross"
    note: str = ""


@dataclass(frozen=True)
class CountryProfile:
    code: str
    name: str
    region: str
    currency: str
    timezone: str
    languages: tuple[str, ...]
    tax_year_start: tuple[int, int]
    pay_frequencies: tuple[str, ...]
    statutory_leave_days: int
    schemes: tuple[StatutoryScheme, ...] = field(default_factory=tuple)
    filings: tuple[str, ...] = field(default_factory=tuple)
    benefits_note: str = ""
    payroll_note: str = ""

    @property
    def is_europe(self) -> bool:
        return self.region == "Europe"


def _eur(
    code: str,
    name: str,
    employee: str,
    employer: str,
    *,
    timezone: str = "Europe/Brussels",
    languages: tuple[str, ...] = ("en",),
    statutory_leave_days: int = 20,
    filings: tuple[str, ...] = (),
    benefits_note: str = "Statutory health insurance and pension enrolment apply.",
    payroll_note: str = "Reference social rate; income tax bands and allowances must be configured.",
) -> CountryProfile:
    """Build a euro-area European profile with a combined social scheme."""
    scheme = StatutoryScheme(
        code="social_security",
        name=f"{name} social security",
        employee_rate=Decimal(employee),
        employer_rate=Decimal(employer),
    )
    income_tax = StatutoryScheme(
        code="income_tax",
        name=f"{name} income tax",
        note="Progressive bands; configure in statutory rule versions.",
    )
    return CountryProfile(
        code=code,
        name=name,
        region="Europe",
        currency="EUR",
        timezone=timezone,
        languages=languages,
        tax_year_start=(1, 1),
        pay_frequencies=("monthly",),
        statutory_leave_days=statutory_leave_days,
        schemes=(scheme, income_tax),
        filings=filings,
        benefits_note=benefits_note,
        payroll_note=payroll_note,
    )


# ---------------------------------------------------------------------------
# Europe — the 12 largest payroll markets by GDP/population
# ---------------------------------------------------------------------------

EUROPE: dict[str, CountryProfile] = {
    "GB": CountryProfile(
        code="GB",
        name="United Kingdom",
        region="Europe",
        currency="GBP",
        timezone="Europe/London",
        languages=("en",),
        tax_year_start=(4, 6),
        pay_frequencies=("monthly", "weekly", "fortnightly"),
        statutory_leave_days=28,
        schemes=(
            StatutoryScheme(
                code="national_insurance",
                name="National Insurance",
                employee_rate=Decimal("8.00"),
                employer_rate=Decimal("15.00"),
                note="Reference main rates; upper earnings limits and categories apply.",
            ),
            StatutoryScheme(
                code="income_tax",
                name="PAYE income tax",
                note="Bands, personal allowance and Scottish rates require rule versions.",
            ),
        ),
        filings=("RTI FPS/EPS", "P60", "P11D", "PAYE settlement"),
        benefits_note="Employer-provided private medical insurance is a common benefit-in-kind; pension auto-enrolment is statutory.",
        payroll_note="Tax year starts 6 April. RTI reporting is mandatory on or before payday.",
    ),
    "DE": CountryProfile(
        code="DE",
        name="Germany",
        region="Europe",
        currency="EUR",
        timezone="Europe/Berlin",
        languages=("de", "en"),
        tax_year_start=(1, 1),
        pay_frequencies=("monthly",),
        statutory_leave_days=20,
        schemes=(
            StatutoryScheme(
                code="social_security",
                name="Social insurance (KV/PV/RV/AV)",
                employee_rate=Decimal("20.40"),
                employer_rate=Decimal("19.90"),
                note="Combined reference rate; individual funds and ceilings apply.",
            ),
            StatutoryScheme(
                code="income_tax",
                name="Lohnsteuer",
                note="Progressive bands, solidarity surcharge and church tax require rule versions.",
            ),
        ),
        filings=("DEÜV", "Lohnsteuer-Anmeldung", "ELStAM", "Lohnausweis"),
        benefits_note="Statutory health insurance is universal; employer supplementary plans are common.",
        payroll_note="Works councils may require payroll and benefit change consultation.",
    ),
    "FR": _eur(
        "FR",
        "France",
        "22.00",
        "42.00",
        timezone="Europe/Paris",
        languages=("fr", "en"),
        statutory_leave_days=25,
        filings=("DSN", "URSSAF", "PAS"),
        benefits_note="Statutory health cover plus employer top-up insurance (mutuelle) is standard.",
        payroll_note="DSN is the single monthly social declaration.",
    ),
    "IT": _eur(
        "IT",
        "Italy",
        "9.50",
        "30.00",
        timezone="Europe/Rome",
        languages=("it", "en"),
        statutory_leave_days=20,
        filings=("UniEmens", "F24", "CU"),
    ),
    "ES": _eur(
        "ES",
        "Spain",
        "6.50",
        "30.00",
        timezone="Europe/Madrid",
        languages=("es", "en"),
        statutory_leave_days=22,
        filings=("Sistema RED", "TC2", "Modelo 111"),
    ),
    "NL": _eur(
        "NL",
        "Netherlands",
        "27.00",
        "20.00",
        timezone="Europe/Amsterdam",
        languages=("nl", "en"),
        statutory_leave_days=20,
        filings=("Loonaangifte", "Jaaropgave"),
    ),
    "CH": CountryProfile(
        code="CH",
        name="Switzerland",
        region="Europe",
        currency="CHF",
        timezone="Europe/Zurich",
        languages=("de", "fr", "it", "en"),
        tax_year_start=(1, 1),
        pay_frequencies=("monthly",),
        statutory_leave_days=20,
        schemes=(
            StatutoryScheme(
                code="social_security",
                name="AHV/IV/EO/ALV",
                employee_rate=Decimal("13.00"),
                employer_rate=Decimal("13.00"),
                note="Combined reference rate; BVG pension and accident insurance are separate.",
            ),
            StatutoryScheme(
                code="income_tax",
                name="Cantonal and federal income tax",
                note="Varies by canton; configure in rule versions.",
            ),
        ),
        filings=("AHV/AVS declaration", "Lohnausweis", "BVG reporting"),
        benefits_note="Mandatory accident insurance and occupational pension (BVG) complement statutory health insurance.",
    ),
    "PL": CountryProfile(
        code="PL",
        name="Poland",
        region="Europe",
        currency="PLN",
        timezone="Europe/Warsaw",
        languages=("pl", "en"),
        tax_year_start=(1, 1),
        pay_frequencies=("monthly",),
        statutory_leave_days=20,
        schemes=(
            StatutoryScheme(
                code="social_security",
                name="ZUS social insurance",
                employee_rate=Decimal("13.71"),
                employer_rate=Decimal("20.48"),
                note="Combined reference rate; pension, disability, sickness and accident funds.",
            ),
            StatutoryScheme(
                code="income_tax",
                name="PIT",
                note="Progressive bands and reliefs require rule versions.",
            ),
        ),
        filings=("ZUS DRA", "PIT-11", "PIT-4R"),
        benefits_note="Statutory health contribution and employer group medical plans are common.",
    ),
    "SE": CountryProfile(
        code="SE",
        name="Sweden",
        region="Europe",
        currency="SEK",
        timezone="Europe/Stockholm",
        languages=("sv", "en"),
        tax_year_start=(1, 1),
        pay_frequencies=("monthly",),
        statutory_leave_days=25,
        schemes=(
            StatutoryScheme(
                code="social_security",
                name="Sociala avgifter",
                employee_rate=Decimal("7.00"),
                employer_rate=Decimal("31.42"),
                note="Employer contribution is the dominant cost; reference rate only.",
            ),
            StatutoryScheme(
                code="income_tax",
                name="Municipal and state income tax",
                note="Municipal rates vary; configure in rule versions.",
            ),
        ),
        filings=("Arbetsgivardeklaration", "Kontrolluppgift"),
        benefits_note="Occupational pension (tjänstepension) and private health insurance are standard.",
    ),
    "BE": _eur(
        "BE",
        "Belgium",
        "13.07",
        "25.00",
        timezone="Europe/Brussels",
        languages=("nl", "fr", "de", "en"),
        statutory_leave_days=20,
        filings=("Dimona", "DmfA", "Belcotax"),
        benefits_note="Hospitalisation insurance and group pension are common employer benefits.",
    ),
    "IE": _eur(
        "IE",
        "Ireland",
        "4.00",
        "11.05",
        timezone="Europe/Dublin",
        languages=("en", "ga"),
        statutory_leave_days=20,
        filings=("PAYE Modernisation", "P30", "P60"),
    ),
    "AT": _eur(
        "AT",
        "Austria",
        "18.07",
        "21.48",
        timezone="Europe/Vienna",
        languages=("de", "en"),
        statutory_leave_days=25,
        filings=("ELDA", "L16", "Jahreslohnzettel"),
    ),
}


# ---------------------------------------------------------------------------
# Non-European markets already supported or planned
# ---------------------------------------------------------------------------

INDIA = CountryProfile(
    code="IN",
    name="India",
    region="Asia",
    currency="INR",
    timezone="Asia/Kolkata",
    languages=("en", "hi"),
    tax_year_start=(4, 1),
    pay_frequencies=("monthly",),
    statutory_leave_days=0,
    schemes=(
        StatutoryScheme(
            code="pf",
            name="Provident Fund",
            employee_rate=Decimal("12.00"),
            employer_rate=Decimal("12.00"),
            wage_ceiling=Decimal("15000"),
            note="Reference statutory minimum; EPS/EDLI split applies.",
        ),
        StatutoryScheme(
            code="esi",
            name="Employee State Insurance",
            employee_rate=Decimal("0.75"),
            employer_rate=Decimal("3.25"),
            wage_ceiling=Decimal("21000"),
            basis="gross",
        ),
        StatutoryScheme(code="pt", name="Professional Tax", note="State-specific slabs."),
        StatutoryScheme(code="tds", name="Tax Deducted at Source", note="Progressive FY slabs and exemptions."),
    ),
    filings=("Form 24Q", "Form 16", "PF ECR", "ESI return"),
    benefits_note="Group health, accidental and term-life cover are typical; ESI applies below the wage ceiling.",
    payroll_note="India already has a dedicated PF/ESI/PT/TDS rule path.",
)

USA = CountryProfile(
    code="US",
    name="United States",
    region="Americas",
    currency="USD",
    timezone="America/New_York",
    languages=("en", "es"),
    tax_year_start=(1, 1),
    pay_frequencies=("biweekly", "weekly", "semimonthly", "monthly"),
    statutory_leave_days=0,
    schemes=(
        StatutoryScheme(
            code="fica",
            name="FICA (Social Security and Medicare)",
            employee_rate=Decimal("7.65"),
            employer_rate=Decimal("7.65"),
            wage_ceiling=Decimal("176100"),
            note="Social Security wage base; Medicare has no cap.",
        ),
        StatutoryScheme(code="federal_income_tax", name="Federal income tax withholding", note="W-4 based."),
        StatutoryScheme(code="state_income_tax", name="State income tax", note="State-specific."),
    ),
    filings=("Form 941", "W-2", "W-3", "940", "State unemployment"),
    benefits_note="Employer-sponsored medical, dental, vision, life and 401(k) plans are market standard.",
    payroll_note="FLSA overtime, multi-state allocation and garnishments apply.",
)


COUNTRY_PROFILES: dict[str, CountryProfile] = {
    **EUROPE,
    "IN": INDIA,
    "US": USA,
}

# Ordered list used for UI selectors and API responses.
COUNTRY_ORDER: tuple[str, ...] = tuple(EUROPE) + ("IN", "US")


def normalize_country(code: str | None) -> str:
    """Return an upper-case ISO country code, defaulting to India."""
    return (code or "IN").strip().upper()


def get_country_profile(code: str | None) -> CountryProfile | None:
    """Look up a country profile by ISO-3166 alpha-2 code."""
    return COUNTRY_PROFILES.get(normalize_country(code))


def require_country_profile(code: str | None) -> CountryProfile:
    """Look up a profile or raise a ``ValueError`` for an unknown country."""
    profile = get_country_profile(code)
    if profile is None:
        raise ValueError(f"Unsupported country code: {code}")
    return profile


def list_country_profiles(*, region: str | None = None) -> list[CountryProfile]:
    """List profiles in display order, optionally filtered by region."""
    profiles = [COUNTRY_PROFILES[code] for code in COUNTRY_ORDER]
    if region:
        wanted = region.strip().lower()
        profiles = [p for p in profiles if p.region.lower() == wanted]
    return profiles


def european_countries() -> list[CountryProfile]:
    """The supported European payroll markets."""
    return list_country_profiles(region="Europe")


def country_payload(profile: CountryProfile) -> dict:
    """Serialize a profile for the API."""
    return {
        "code": profile.code,
        "name": profile.name,
        "region": profile.region,
        "currency": profile.currency,
        "timezone": profile.timezone,
        "languages": list(profile.languages),
        "tax_year_start": f"{profile.tax_year_start[0]:02d}-{profile.tax_year_start[1]:02d}",
        "pay_frequencies": list(profile.pay_frequencies),
        "statutory_leave_days": profile.statutory_leave_days,
        "schemes": [
            {
                "code": scheme.code,
                "name": scheme.name,
                "employee_rate": float(scheme.employee_rate) if scheme.employee_rate is not None else None,
                "employer_rate": float(scheme.employer_rate) if scheme.employer_rate is not None else None,
                "wage_ceiling": float(scheme.wage_ceiling) if scheme.wage_ceiling is not None else None,
                "basis": scheme.basis,
                "note": scheme.note,
            }
            for scheme in profile.schemes
        ],
        "filings": list(profile.filings),
        "benefits_note": profile.benefits_note,
        "payroll_note": profile.payroll_note,
        "reference_only": True,
    }


def reference_statutory_items(profile: CountryProfile, gross: Decimal) -> list[dict]:
    """Compute reference social-insurance lines for a country profile.

    Only schemes with explicit rates are emitted. The caller must treat these
    as planning defaults; configured ``StatutoryRuleVersion`` rows always win.
    """
    items: list[dict] = []
    if gross <= 0:
        return items
    for scheme in profile.schemes:
        if scheme.employee_rate is None and scheme.employer_rate is None:
            continue
        base = gross
        if scheme.wage_ceiling is not None and scheme.wage_ceiling > 0:
            base = min(gross, scheme.wage_ceiling)
        employee_amount = (
            base * scheme.employee_rate / Decimal("100")
            if scheme.employee_rate is not None
            else Decimal("0")
        )
        employer_amount = (
            base * scheme.employer_rate / Decimal("100")
            if scheme.employer_rate is not None
            else Decimal("0")
        )
        employee_amount = employee_amount.quantize(Decimal("0.01"))
        employer_amount = employer_amount.quantize(Decimal("0.01"))
        if employee_amount == 0 and employer_amount == 0:
            continue
        items.append(
            {
                "code": scheme.code,
                "name": f"{scheme.name} (reference)",
                "employee_amount": employee_amount,
                "employer_amount": employer_amount,
                "base": base,
                "reference_only": True,
            }
        )
    return items


__all__ = [
    "COUNTRY_PROFILES",
    "COUNTRY_ORDER",
    "CountryProfile",
    "StatutoryScheme",
    "country_payload",
    "european_countries",
    "get_country_profile",
    "list_country_profiles",
    "normalize_country",
    "reference_statutory_items",
    "require_country_profile",
]

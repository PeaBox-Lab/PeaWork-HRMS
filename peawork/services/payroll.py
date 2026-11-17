from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    BankAccount,
    BenefitPlan,
    Compensation,
    CompensationComponent,
    Employee,
    EmployeeBenefit,
    LegalEntity,
    PayComponent,
    PayrollRun,
    Payslip,
    PayslipLine,
    StatutoryRuleVersion,
    User,
)

from ..security import utcnow
from ..domain import (
    AuditAction,
    PayComponentType,
    PayrollFrequency,
    PayrollStatus,
    StatutoryRuleType,
)
from . import audit as audit_service
from . import policy
from .errors import ConflictError, NotFoundError, ValidationError


def _as_uuid(value) -> UUID:
    """Convert a string UUID to UUID object if needed."""
    if isinstance(value, str):
        return UUID(value)
    return value


def _as_date(value):
    """Convert an ISO date string to a date object if needed."""
    if isinstance(value, str):
        from datetime import date as _date
        return _date.fromisoformat(value)
    return value


STATUTORY_LABELS = {
    StatutoryRuleType.PF: "Provident Fund",
    StatutoryRuleType.ESI: "Employee State Insurance",
    StatutoryRuleType.PT: "Professional Tax",
    StatutoryRuleType.TDS: "Tax Deducted at Source",
}


def compute_statutory_item(rule_type: StatutoryRuleType, wages: Decimal, rates: dict) -> dict | None:
    """Pure calculation for one statutory rule.

    Returns a dict with employee/employer amounts, or None when the rule
    evaluates to a zero contribution.

    ``wage_ceiling`` semantics depend on ``ceiling_mode``:
    - ``"cap"`` (default, e.g. PF): contribution is computed on
      min(wages, ceiling).
    - ``"cutoff"`` (e.g. ESI): the rule does not apply at all above the ceiling.
    """
    if wages is None or wages <= 0:
        return None
    rates = rates or {}
    ceiling_raw = rates.get("wage_ceiling")
    ceiling = Decimal(str(ceiling_raw)) if ceiling_raw not in (None, "", 0) else Decimal("0")
    if ceiling > 0 and rates.get("ceiling_mode") == "cutoff" and wages > ceiling:
        return None
    base = min(wages, ceiling) if ceiling > 0 else wages

    flat_raw = rates.get("flat_amount")
    flat = Decimal(str(flat_raw)) if flat_raw not in (None, "", 0) else Decimal("0")
    emp_rate = Decimal(str(rates.get("employee_rate", 0) or 0))
    er_rate = Decimal(str(rates.get("employer_rate", 0) or 0))

    if flat > 0:
        employee_amount = flat.quantize(Decimal("0.01"))
        employer_amount = Decimal("0.00")
    else:
        employee_amount = (base * emp_rate / Decimal("100")).quantize(Decimal("0.01"))
        employer_amount = (base * er_rate / Decimal("100")).quantize(Decimal("0.01"))

    if employee_amount == 0 and employer_amount == 0:
        return None

    line_type = PayComponentType.TAX if rule_type == StatutoryRuleType.TDS else PayComponentType.DEDUCTION
    return {
        "rule_type": rule_type.value,
        "name": str(rates.get("label") or STATUTORY_LABELS[rule_type]),
        "line_type": line_type,
        "employee_amount": employee_amount,
        "employer_amount": employer_amount,
        "base": base,
    }


def statutory_items_for(db: Session, org_id: UUID, wages: Decimal) -> list[dict]:
    """Load active statutory rule versions and compute items for given wages."""
    items: list[dict] = []
    for rule_type in StatutoryRuleType:
        rule = db.scalar(
            select(StatutoryRuleVersion)
            .where(
                StatutoryRuleVersion.org_id == org_id,
                StatutoryRuleVersion.rule_type == rule_type,
                StatutoryRuleVersion.active,
            )
            .order_by(StatutoryRuleVersion.effective_from.desc())
        )
        if rule is None:
            continue
        item = compute_statutory_item(rule_type, wages, rule.rates or {})
        if item is not None:
            items.append(item)
    return items


def next_payroll_number(db: Session, org_id: UUID) -> str:
    """Generate a payroll run name, e.g., PAY-2024-09-001."""
    now = utcnow()
    month = now.strftime("%m")
    year = now.strftime("%Y")
    prefix = f"PAY-{year}-{month}-"
    max_num = 0
    for rec in db.scalars(
        select(PayrollRun.name).where(
            PayrollRun.org_id == org_id, PayrollRun.name.like(f"{prefix}%")
        )
    ).all():
        tail = rec.split("-")[-1]
        if tail.isdigit():
            max_num = max(max_num, int(tail))
    return f"{prefix}{max_num + 1:03d}"


def create_pay_component(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> PayComponent:
    """Create a new pay component (e.g., Basic, HRA, PF)."""
    if actor.role.value not in {"admin", "hr", "payroll"}:
        raise ValidationError("Only HR/payroll roles may create pay components")

    # Check duplicate code within org
    if db.scalar(
        select(PayComponent.id).where(
            PayComponent.org_id == org_id, PayComponent.code == data["code"]
        )
    ):
        raise ConflictError(f"Pay component code {data['code']} already exists")

    pay_component = PayComponent(
        org_id=org_id,
        name=data["name"].strip(),
        code=data["code"].strip().upper(),
        component_type=data["component_type"],
        is_taxable=data.get("is_taxable", True),
        is_statutory=data.get("is_statutory", False),
        default_amount=data.get("default_amount", Decimal("0")),
        active=data.get("active", True),
    )
    db.add(pay_component)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="pay_component",
        entity_id=pay_component.id,
        details=data,
        org_id=org_id,
    )
    return pay_component


def list_pay_components(db: Session, org_id: UUID) -> list[PayComponent]:
    """List all pay components for an org."""
    return list(
        db.scalars(
            select(PayComponent).where(PayComponent.org_id == org_id).order_by(
                PayComponent.name
            )
        ).all()
    )


def get_compensation(
    db: Session, *, org_id: UUID, employee_id: UUID
) -> Compensation | None:
    """Get current compensation for an employee."""
    return db.scalar(
        select(Compensation)
        .where(Compensation.org_id == org_id, Compensation.employee_id == employee_id)
    )


def create_or_update_compensation(
    db: Session, *, org_id: UUID, actor: User, employee_id: UUID, data: dict
) -> Compensation:
    """Create or update compensation for an employee."""
    employee = db.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")

    if not policy.can_manage_employee(actor, employee):
        raise ValidationError("Not authorized to manage compensation")

    comp = get_compensation(db, org_id=org_id, employee_id=employee_id)
    if comp:
        # Update existing
        for field in [
            "base_salary", "currency", "payroll_frequency", "tax_regime",
            "pay_scale", "grade", "effective_to", "notes",
        ]:
            if field in data:
                setattr(comp, field, data[field])
        if "effective_to" not in data:
            comp.effective_to = utcnow().date()
        db.add(comp)
        db.flush()

        audit_service.record(
            db,
            actor=actor,
            action=AuditAction.UPDATE,
            entity_type="compensation",
            entity_id=comp.id,
            details=data,
            org_id=org_id,
        )
    else:
        # Create new
        comp = Compensation(
            org_id=org_id,
            employee_id=employee_id,
            base_salary=data["base_salary"],
            currency=data.get("currency", "INR"),
            payroll_frequency=data.get("payroll_frequency", PayrollFrequency.MONTHLY),
            tax_regime=data.get("tax_regime", "india_new"),
            pay_scale=data.get("pay_scale"),
            grade=data.get("grade"),
            effective_from=_as_date(data["effective_from"]),
            effective_to=_as_date(data.get("effective_to")) if data.get("effective_to") else None,
            notes=data.get("notes"),
        )
        db.add(comp)
        db.flush()

        audit_service.record(
            db,
            actor=actor,
            action=AuditAction.CREATE,
            entity_type="compensation",
            entity_id=comp.id,
            details=data,
            org_id=org_id,
        )

    # Create compensation component lines
    if "components" in data:
        for comp_data in data["components"]:
            comp_line = CompensationComponent(
                compensation_id=comp.id,
                component_id=_as_uuid(comp_data["component_id"]),
                amount=comp_data["amount"],
                effective_from=_as_date(comp_data.get("effective_from", utcnow().date())),
            )
            db.add(comp_line)

    return comp


def create_payroll_run(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> PayrollRun:
    """Create a new payroll run."""
    if actor.role.value not in {"admin", "hr", "payroll"}:
        raise ValidationError("Only HR/payroll roles may create payroll runs")

    # Check for existing run in period
    period_start = _as_date(data["period_start"])
    period_end = _as_date(data["period_end"])
    stmt = select(PayrollRun.id).where(
        PayrollRun.org_id == org_id,
        PayrollRun.period_start == period_start,
        PayrollRun.period_end == period_end,
    )
    if db.scalar(stmt):
        raise ConflictError("A payroll run already exists for this period")

    run_name = data.get("name") or next_payroll_number(db, org_id)
    run = PayrollRun(
        org_id=org_id,
        name=run_name,
        period_start=_as_date(data["period_start"]),
        period_end=_as_date(data["period_end"]),
        pay_date=_as_date(data.get("pay_date")) if data.get("pay_date") else None,
        frequency=data.get("frequency", PayrollFrequency.MONTHLY),
        currency=data.get("currency", "INR"),
        status=PayrollStatus.DRAFT,
        notes=data.get("notes"),
    )
    db.add(run)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="payroll_run",
        entity_id=run.id,
        details=data,
        org_id=org_id,
    )
    return run


def list_payroll_runs(db: Session, org_id: UUID) -> list[PayrollRun]:
    """List payroll runs for an org."""
    return list(
        db.scalars(
            select(PayrollRun).where(PayrollRun.org_id == org_id).order_by(
                PayrollRun.period_start.desc()
            )
        ).all()
    )


def _compute_employee_payslip(
    db: Session, org_id: UUID, employee_id: UUID
) -> tuple[Decimal, Decimal, list[dict]]:
    """Compute gross, deductions and line dicts for one employee (no persistence)."""
    comp = get_compensation(db, org_id=org_id, employee_id=employee_id)
    if not comp:
        return Decimal("0"), Decimal("0"), []

    gross = Decimal("0")
    total_deductions = Decimal("0")
    lines: list[dict] = []

    comp_components = db.scalars(
        select(CompensationComponent).where(CompensationComponent.compensation_id == comp.id)
    ).all()
    for cc in comp_components:
        component = db.get(PayComponent, cc.component_id)
        if not component or not component.active:
            continue
        lines.append({
            "component_id": component.id,
            "component_name": component.name,
            "component_type": component.component_type,
            "amount": cc.amount,
        })
        if component.component_type == PayComponentType.EARNING:
            gross += cc.amount
        else:
            total_deductions += cc.amount

    # Statutory lines (PF/ESI/PT/TDS) from active rule versions.
    for item in statutory_items_for(db, org_id, gross):
        lines.append({
            "component_id": None,
            "component_name": item["name"],
            "component_type": item["line_type"],
            "amount": item["employee_amount"],
        })
        total_deductions += item["employee_amount"]
        if item["employer_amount"] > 0:
            lines.append({
                "component_id": None,
                "component_name": f"{item['name']} (employer)",
                "component_type": PayComponentType.EMPLOYER_CONTRIBUTION,
                "amount": item["employer_amount"],
            })

    return gross, total_deductions, lines


def generate_payslips(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> list[Payslip]:
    """Generate payslips for a payroll run (process payroll)."""
    run = db.get(PayrollRun, run_id)
    if not run or run.org_id != org_id:
        raise NotFoundError("Payroll run not found")
    if run.frozen:
        raise ValidationError("Payroll run is frozen; unfreeze before processing")
    if run.status != PayrollStatus.DRAFT:
        raise ValidationError("Only draft payroll runs may be processed")

    # Get employees in org
    employee_ids = db.scalars(
        select(Employee.id).where(Employee.org_id == org_id)
    ).all()

    payslips: list[Payslip] = []
    for employee_id in employee_ids:
        gross, total_deductions, line_dicts = _compute_employee_payslip(
            db, org_id, employee_id
        )
        if not line_dicts:
            # Skip employees without any payable lines
            continue

        payslip = Payslip(
            org_id=org_id,
            run_id=run_id,
            employee_id=employee_id,
            gross_pay=gross,
            total_deductions=total_deductions,
            net_pay=gross - total_deductions,
            bank_account_last4=None,
            paid_at=None,
        )
        db.add(payslip)
        db.flush()

        for line_dict in line_dicts:
            line = PayslipLine(
                payslip_id=payslip.id,
                component_id=line_dict["component_id"],
                component_name=line_dict["component_name"],
                component_type=line_dict["component_type"],
                amount=line_dict["amount"],
                quantity=None,
                unit=None,
            )
            db.add(line)

        payslips.append(payslip)

    # Update run status to processing
    run.status = PayrollStatus.PROCESSING
    run.total_gross = sum((p.gross_pay for p in payslips), Decimal("0"))
    run.total_net = sum((p.net_pay for p in payslips), Decimal("0"))
    db.add(run)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="payslip_batch",
        entity_id=run.id,
        details={"processed_count": len(payslips), "run_id": str(run.id)},
        org_id=org_id,
    )
    return payslips


def preview_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> dict:
    """Preview payslip figures for a run without persisting anything."""
    run = db.get(PayrollRun, run_id)
    if not run or run.org_id != org_id:
        raise NotFoundError("Payroll run not found")

    employee_ids = db.scalars(
        select(Employee.id).where(Employee.org_id == org_id)
    ).all()

    employees: list[dict] = []
    total_gross = Decimal("0")
    total_deductions = Decimal("0")
    for employee_id in employee_ids:
        gross, deductions, line_dicts = _compute_employee_payslip(db, org_id, employee_id)
        if not line_dicts:
            continue
        total_gross += gross
        total_deductions += deductions
        employees.append({
            "employee_id": str(employee_id),
            "gross_pay": float(gross),
            "total_deductions": float(deductions),
            "net_pay": float(gross - deductions),
        })

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.VIEW,
        entity_type="payroll_run_preview",
        entity_id=run.id,
        details={"employee_count": len(employees)},
        org_id=org_id,
    )
    return {
        "run_id": str(run.id),
        "name": run.name,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "currency": run.currency,
        "employees": employees,
        "total_gross": float(total_gross),
        "total_deductions": float(total_deductions),
        "total_net": float(total_gross - total_deductions),
    }


def list_bank_accounts(db: Session, org_id: UUID) -> list[BankAccount]:
    """List all bank accounts for an org."""
    return list(
        db.scalars(
            select(BankAccount).where(BankAccount.org_id == org_id).order_by(
                BankAccount.is_primary.desc(), BankAccount.bank_name
            )
        ).all()
    )


def create_bank_account(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> BankAccount:
    """Add bank account for an employee."""
    employee_id = _as_uuid(data["employee_id"])
    employee = db.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")

    # Mask account number
    acc_num = data["account_number"].replace(" ", "")
    masked = f"****{acc_num[-4:]}"

    account = BankAccount(
        org_id=org_id,
        employee_id=employee_id,
        account_type=data["account_type"],
        bank_name=data["bank_name"].strip(),
        branch=data.get("branch"),
        account_holder=data["account_holder"].strip(),
        account_number_masked=masked,
        ifsc=data.get("ifsc"),
        swift=data.get("swift"),
        is_primary=data.get("is_primary", False),
        active=data.get("active", True),
    )
    db.add(account)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="bank_account",
        entity_id=account.id,
        details=data,
        org_id=org_id,
    )
    return account


def list_benefit_plans(db: Session, org_id: UUID) -> list[BenefitPlan]:
    """List benefit plans for an org."""
    return list(
        db.scalars(
            select(BenefitPlan).where(BenefitPlan.org_id == org_id).order_by(
                BenefitPlan.name
            )
        ).all()
    )


def create_benefit_plan(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> BenefitPlan:
    """Create a benefit plan (e.g., Health Insurance)."""
    if actor.role.value not in {"admin", "hr"}:
        raise ValidationError("Only HR roles may create benefit plans")

    # Check duplicate code
    if db.scalar(
        select(BenefitPlan.id).where(
            BenefitPlan.org_id == org_id, BenefitPlan.code == data["code"]
        )
    ):
        raise ConflictError(f"Benefit plan code {data['code']} already exists")

    plan = BenefitPlan(
        org_id=org_id,
        name=data["name"].strip(),
        code=data["code"].strip().upper(),
        benefit_type=data["benefit_type"],
        provider=data.get("provider"),
        employer_contribution=data.get("employer_contribution", Decimal("0")),
        employee_contribution=data.get("employee_contribution", Decimal("0")),
        active=data.get("active", True),
    )
    db.add(plan)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="benefit_plan",
        entity_id=plan.id,
        details=data,
        org_id=org_id,
    )
    return plan


def enroll_employee_in_benefit(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> EmployeeBenefit:
    """Enroll employee in a benefit plan."""
    employee_id = _as_uuid(data["employee_id"])
    employee = db.get(Employee, employee_id)
    if not employee or employee.org_id != org_id:
        raise NotFoundError("Employee not found")

    plan_id = _as_uuid(data["plan_id"])
    plan = db.get(BenefitPlan, plan_id)
    if not plan or plan.org_id != org_id:
        raise NotFoundError("Benefit plan not found")

    # Check if already enrolled
    existing = db.scalar(
        select(EmployeeBenefit.id).where(
            EmployeeBenefit.employee_id == employee_id,
            EmployeeBenefit.plan_id == plan_id,
        )
    )
    if existing:
        raise ConflictError("Employee already enrolled in this benefit plan")

    enrollment = EmployeeBenefit(
        org_id=org_id,
        employee_id=employee_id,
        plan_id=plan_id,
        enrolled_on=_as_date(data.get("enrolled_on", utcnow().date())),
        coverage_amount=data.get("coverage_amount"),
        status=data.get("status", "active"),
        ends_on=_as_date(data.get("ends_on")) if data.get("ends_on") else None,
    )
    db.add(enrollment)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="employee_benefit",
        entity_id=enrollment.id,
        details=data,
        org_id=org_id,
    )
    return enrollment


def list_legal_entities(db: Session, org_id: UUID) -> list[LegalEntity]:
    """List legal entities for an org."""
    return list(
        db.scalars(
            select(LegalEntity).where(LegalEntity.org_id == org_id).order_by(
                LegalEntity.name
            )
        ).all()
    )


def list_statutory_rule_versions(db: Session, org_id: UUID) -> list[StatutoryRuleVersion]:
    """List statutory rule versions for an org."""
    return list(
        db.scalars(
            select(StatutoryRuleVersion).where(StatutoryRuleVersion.org_id == org_id).order_by(
                StatutoryRuleVersion.rule_type, StatutoryRuleVersion.effective_from.desc()
            )
        ).all()
    )


def create_statutory_rule_version(
    db: Session, *, org_id: UUID, actor: User, data: dict
) -> StatutoryRuleVersion:
    """Create a statutory rule version (PF, ESI, PT, TDS rates with effective dates)."""
    if actor.role.value not in {"admin", "hr"}:
        raise ValidationError("Only HR roles may create statutory rule versions")

    rule_type = data.get("rule_type")
    if rule_type not in {e.value for e in StatutoryRuleType}:
        raise ConflictError(f"Invalid rule_type. Must be one of: {[e.value for e in StatutoryRuleType]}")

    # Check for existing active version for this rule type and org
    existing = db.scalar(
        select(StatutoryRuleVersion.id).where(
            StatutoryRuleVersion.org_id == org_id,
            StatutoryRuleVersion.rule_type == rule_type,
            StatutoryRuleVersion.active,
        )
    )
    if existing:
        raise ConflictError(f"A active statutory rule version already exists for {rule_type}")

    rule_enum = StatutoryRuleType(rule_type)

    plan = StatutoryRuleVersion(
        org_id=org_id,
        rule_type=rule_enum,
        country_code=data.get("country_code", "IN"),
        state_code=data.get("state_code"),
        effective_from=_as_date(data["effective_from"]),
        effective_to=_as_date(data.get("effective_to")) if data.get("effective_to") else None,
        version=data.get("version", 1),
        rates=data.get("rates", {}),
        active=True,
        notes=data.get("notes"),
    )
    db.add(plan)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="statutory_rule_version",
        entity_id=plan.id,
        details=data,
        org_id=org_id,
    )
    return plan


def update_statutory_rule_version(
    db: Session, *, org_id: UUID, rule_version_id: UUID, actor: User, data: dict
) -> StatutoryRuleVersion:
    """Update a statutory rule version."""
    rule_version = db.get(StatutoryRuleVersion, rule_version_id)
    if not rule_version or rule_version.org_id != org_id:
        raise NotFoundError("Statutory rule version not found")

    for field in ["effective_from", "effective_to", "version", "rates", "active", "notes"]:
        if field in data:
            setattr(rule_version, field, data[field])

    db.add(rule_version)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="statutory_rule_version",
        entity_id=rule_version.id,
        details=data,
        org_id=org_id,
    )
    return rule_version


def delete_statutory_rule_version(
    db: Session, *, org_id: UUID, rule_version_id: UUID, actor: User
) -> None:
    """Delete a statutory rule version (soft delete via active=False)."""
    rule_version = db.get(StatutoryRuleVersion, rule_version_id)
    if not rule_version or rule_version.org_id != org_id:
        raise NotFoundError("Statutory rule version not found")

    rule_version.active = False
    rule_version.effective_to = utcnow().date()
    db.add(rule_version)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.DELETE,
        entity_type="statutory_rule_version",
        entity_id=rule_version.id,
        details={"id": str(rule_version_id)},
        org_id=org_id,
    )


def apply_statutory_rule(
    db: Session, *, org_id: UUID, employee_id: UUID, rule_type: str, salary: Decimal
) -> Decimal:
    """Apply a statutory rule to calculate employee contribution."""
    try:
        rule_enum = StatutoryRuleType(rule_type)
    except ValueError:
        raise ValidationError(f"Unknown rule_type: {rule_type}") from None

    for item in statutory_items_for(db, org_id, salary):
        if item["rule_type"] == rule_enum.value:
            return item["employee_amount"]
    return Decimal("0")


def _get_run(db: Session, org_id: UUID, run_id: UUID) -> PayrollRun:
    run = db.get(PayrollRun, run_id)
    if not run or run.org_id != org_id:
        raise NotFoundError("Payroll run not found")
    return run


def _transition_run(
    db: Session,
    *,
    org_id: UUID,
    actor: User,
    run_id: UUID,
    expected: set[PayrollStatus],
    target: PayrollStatus,
    action: AuditAction,
    details: dict | None = None,
) -> PayrollRun:
    run = _get_run(db, org_id, run_id)
    if run.frozen:
        raise ValidationError("Payroll run is frozen; unfreeze first")
    if run.status not in expected:
        raise ValidationError(
            f"Payroll run is {run.status.value}; cannot transition to {target.value}"
        )
    run.status = target
    db.add(run)
    db.flush()
    audit_service.record(
        db,
        actor=actor,
        action=action,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name, **(details or {})},
        org_id=org_id,
    )
    return run


def submit_payroll_run_for_review(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """PROCESSING -> REVIEW (variance review gate)."""
    return _transition_run(
        db, org_id=org_id, actor=actor, run_id=run_id,
        expected={PayrollStatus.PROCESSING},
        target=PayrollStatus.REVIEW,
        action=AuditAction.UPDATE,
    )


def approve_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """REVIEW -> APPROVED; records approver and immutable approval timestamp."""
    run = _get_run(db, org_id, run_id)
    if run.frozen:
        raise ValidationError("Payroll run is frozen; unfreeze first")
    if run.status != PayrollStatus.REVIEW:
        raise ValidationError(f"Payroll run is {run.status.value}; approval requires review status")
    run.status = PayrollStatus.APPROVED
    run.approved_by = actor.id
    run.approved_at = utcnow()
    db.add(run)
    db.flush()
    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name},
        org_id=org_id,
    )
    return run


def finalize_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """APPROVED -> FINALIZED; payslips become an immutable snapshot."""
    run = _get_run(db, org_id, run_id)
    if run.frozen:
        raise ValidationError("Payroll run is frozen; unfreeze first")
    if run.status != PayrollStatus.APPROVED:
        raise ValidationError(f"Payroll run is {run.status.value}; finalize requires approved status")
    run.status = PayrollStatus.FINALIZED
    run.finalized_at = utcnow()
    db.add(run)
    db.flush()
    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name, "finalized_at": run.finalized_at.isoformat()},
        org_id=org_id,
    )
    return run


def mark_paid_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """FINALIZED -> PAID; stamps paid_at and primary bank last4 on each payslip."""
    run = _get_run(db, org_id, run_id)
    if run.frozen:
        raise ValidationError("Payroll run is frozen; unfreeze first")
    if run.status != PayrollStatus.FINALIZED:
        raise ValidationError(f"Payroll run is {run.status.value}; mark paid requires finalized status")

    payslips = db.scalars(select(Payslip).where(Payslip.run_id == run.id)).all()
    now = utcnow()
    for payslip in payslips:
        account = db.scalar(
            select(BankAccount)
            .where(
                BankAccount.org_id == org_id,
                BankAccount.employee_id == payslip.employee_id,
                BankAccount.active,
                BankAccount.is_primary,
            )
        )
        if account is not None:
            payslip.bank_account_last4 = account.account_number_masked[-4:]
        payslip.paid_at = now
        db.add(payslip)
    run.status = PayrollStatus.PAID
    run.pay_date = now.date()
    db.add(run)
    db.flush()

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name, "paid_count": len(payslips)},
        org_id=org_id,
    )
    return run


def freeze_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """Freeze a run; blocks processing/transitions until unfrozen."""
    run = _get_run(db, org_id, run_id)
    if run.frozen:
        raise ValidationError("Payroll run is already frozen")
    run.frozen = True
    db.add(run)
    db.flush()
    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name, "frozen": True},
        org_id=org_id,
    )
    return run


def unfreeze_payroll_run(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> PayrollRun:
    """Unfreeze a run; allows processing/transitions again."""
    run = _get_run(db, org_id, run_id)
    if not run.frozen:
        raise ValidationError("Payroll run is not frozen")
    run.frozen = False
    db.add(run)
    db.flush()
    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="payroll_run",
        entity_id=run.id,
        details={"run_name": run.name, "frozen": False},
        org_id=org_id,
    )
    return run


def payroll_run_variance(
    db: Session, *, org_id: UUID, actor: User, run_id: UUID
) -> dict:
    """Compare a run's payslips against the previous run with payslips."""
    run = _get_run(db, org_id, run_id)

    previous = db.scalar(
        select(PayrollRun)
        .where(
            PayrollRun.org_id == org_id,
            PayrollRun.id != run.id,
            PayrollRun.period_start < run.period_start,
            PayrollRun.status.notin_([PayrollStatus.DRAFT, PayrollStatus.CANCELLED]),
        )
        .order_by(PayrollRun.period_start.desc())
    )

    current_payslips = db.scalars(
        select(Payslip).where(Payslip.run_id == run.id)
    ).all()
    current_by_employee = {p.employee_id: p for p in current_payslips}

    previous_by_employee = {}
    if previous is not None:
        previous_payslips = db.scalars(
            select(Payslip).where(Payslip.run_id == previous.id)
        ).all()
        previous_by_employee = {p.employee_id: p for p in previous_payslips}

    rows: list[dict] = []
    for employee_id, current in current_by_employee.items():
        prev = previous_by_employee.get(employee_id)
        prev_gross = prev.gross_pay if prev else None
        prev_net = prev.net_pay if prev else None
        rows.append({
            "employee_id": str(employee_id),
            "previous_gross": float(prev_gross) if prev_gross is not None else None,
            "current_gross": float(current.gross_pay),
            "gross_delta": float(current.gross_pay - prev_gross) if prev_gross is not None else None,
            "previous_net": float(prev_net) if prev_net is not None else None,
            "current_net": float(current.net_pay),
            "net_delta": float(current.net_pay - prev_net) if prev_net is not None else None,
        })

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.VIEW,
        entity_type="payroll_run_variance",
        entity_id=run.id,
        details={"employee_count": len(rows), "previous_run_id": str(previous.id) if previous else None},
        org_id=org_id,
    )
    return {
        "run_id": str(run.id),
        "previous_run_id": str(previous.id) if previous else None,
        "currency": run.currency,
        "rows": rows,
    }


def bank_export(db: Session, *, org_id: UUID, actor: User, run_id: UUID) -> dict:
    """Build a salary disbursement export with a control total."""
    run = _get_run(db, org_id, run_id)

    payslips = db.scalars(
        select(Payslip).where(Payslip.run_id == run.id)
    ).all()

    rows: list[dict] = []
    total_net = Decimal("0")
    for payslip in payslips:
        employee = db.get(Employee, payslip.employee_id)
        account = db.scalar(
            select(BankAccount)
            .where(
                BankAccount.org_id == org_id,
                BankAccount.employee_id == payslip.employee_id,
                BankAccount.active,
                BankAccount.is_primary,
            )
        )
        if account is None:
            account = db.scalar(
                select(BankAccount).where(
                    BankAccount.org_id == org_id,
                    BankAccount.employee_id == payslip.employee_id,
                    BankAccount.active,
                ).order_by(BankAccount.created_at)
            )
        total_net += payslip.net_pay
        rows.append({
            "employee_no": employee.employee_no if employee else None,
            "name": f"{employee.first_name} {employee.last_name}" if employee else None,
            "bank_name": account.bank_name if account else None,
            "account_number_masked": account.account_number_masked if account else None,
            "ifsc": account.ifsc if account else None,
            "net_pay": float(payslip.net_pay),
            "currency": run.currency,
        })

    control_total = total_net.quantize(Decimal("0.01"))
    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.DOWNLOAD,
        entity_type="payroll_bank_export",
        entity_id=run.id,
        details={"row_count": len(rows), "control_total": str(control_total)},
        org_id=org_id,
    )
    return {
        "run_id": str(run.id),
        "name": run.name,
        "currency": run.currency,
        "rows": rows,
        "control_total": float(control_total),
        "balanced": control_total == run.total_net,
        "note": "Account numbers are masked; unmasked disbursement files require treasury approval.",
    }


def accounting_export(db: Session, *, org_id: UUID, actor: User, run_id: UUID) -> dict:
    """Build balanced journal entries with a debit-credit proof."""
    run = _get_run(db, org_id, run_id)

    payslips = db.scalars(
        select(Payslip).where(Payslip.run_id == run.id)
    ).all()
    payslip_ids = [p.id for p in payslips]

    lines = db.scalars(
        select(PayslipLine).where(PayslipLine.payslip_id.in_(payslip_ids))
    ).all() if payslip_ids else []

    gross = sum((p.gross_pay for p in payslips), Decimal("0"))
    net = sum((p.net_pay for p in payslips), Decimal("0"))
    employee_statutory: dict[str, Decimal] = {}
    employer_statutory: dict[str, Decimal] = {}
    for line in lines:
        if line.component_type in (PayComponentType.DEDUCTION, PayComponentType.TAX):
            employee_statutory[line.component_name] = (
                employee_statutory.get(line.component_name, Decimal("0")) + line.amount
            )
        elif line.component_type == PayComponentType.EMPLOYER_CONTRIBUTION:
            employer_statutory[line.component_name] = (
                employer_statutory.get(line.component_name, Decimal("0")) + line.amount
            )

    entries: list[dict] = []
    entries.append({
        "account": "Salaries & Wages Expense", "debit": float(gross), "credit": 0.0,
    })
    for name, amount in sorted(employee_statutory.items()):
        base_name = name.replace(" Tax", "").replace("Provident Fund", "PF")
        entries.append({
            "account": f"{base_name} Payable", "debit": 0.0, "credit": float(amount),
        })
    if employer_statutory:
        entries.append({
            "account": "Employer Statutory Contribution Expense",
            "debit": float(sum(employer_statutory.values(), Decimal("0"))),
            "credit": 0.0,
        })
        entries.append({
            "account": "Employer Statutory Contribution Payable",
            "debit": 0.0,
            "credit": float(sum(employer_statutory.values(), Decimal("0"))),
        })
    entries.append({
        "account": f"Bank / Salary Clearing ({run.currency})",
        "debit": 0.0, "credit": float(net),
    })

    total_debit = sum((Decimal(str(e["debit"])) for e in entries), Decimal("0")).quantize(Decimal("0.01"))
    total_credit = sum((Decimal(str(e["credit"])) for e in entries), Decimal("0")).quantize(Decimal("0.01"))

    audit_service.record(
        db,
        actor=actor,
        action=AuditAction.DOWNLOAD,
        entity_type="payroll_accounting_export",
        entity_id=run.id,
        details={"entry_count": len(entries), "balanced": total_debit == total_credit},
        org_id=org_id,
    )
    return {
        "run_id": str(run.id),
        "name": run.name,
        "currency": run.currency,
        "entries": entries,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "balanced": total_debit == total_credit,
        "boundary": "PeaWork is not a filing engine; statutory returns must be filed via the certified statutory portal.",
    }
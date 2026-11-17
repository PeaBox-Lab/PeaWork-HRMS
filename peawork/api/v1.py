from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_guard, require_user
from ..domain import AuditAction, DocumentCategory, Role
from ..models import AuditEvent, Department, Employee, LeaveType, PayrollRun, Position, User
from ..schemas import (
    AuditEventOut,
    DepartmentCreate,
    DepartmentOut,
    DocumentOut,
    EmployeeCreate,
    EmployeeOut,
    EmployeeUpdate,
    LeaveBalanceOut,
    LeaveDecision,
    LeaveRequestCreate,
    LeaveRequestOut,
    LeaveTypeOut,
    MeOut,
    OrganizationOut,
    Page,
    PasswordChange,
    PositionCreate,
    PositionOut,
    UserOut,
)
from ..services import audit
from ..services.auth import change_password
from ..services.documents import (
    acknowledge_document,
    file_path,
    get_document,
    list_documents,
    save_document,
)
from ..services.employees import (
    create_employee,
    get_employee_for,
    list_employees,
    update_employee,
)
from ..services.errors import ConflictError, ValidationError
from ..services.leave import (
    balances_for,
    cancel_leave,
    decide_leave,
    get_leave,
    list_leave_requests,
    request_leave,
)
from ..services import payroll as payroll_service
from ..security import password_problems, verify_password

router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(csrf_guard)])


@router.get("/health", tags=["meta"])
def api_health():
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
def me(
    request: Request,
    user: User = Depends(require_user(allow_password_change=True)),
):
    org = getattr(request.state, "org", None)
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        organization=OrganizationOut.model_validate(org) if org else None,
    )


@router.post("/me/password", status_code=204)
def change_my_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(allow_password_change=True)),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise ValidationError("Current password is incorrect")
    problems = password_problems(payload.new_password)
    if problems:
        raise ValidationError("Password " + "; ".join(problems))
    change_password(db, user, payload.new_password)
    return None


@router.get("/employees", response_model=Page[EmployeeOut])
def api_employees(
    search: str = "",
    department_id: UUID | None = None,
    status: str = "",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    employees, total = list_employees(
        db,
        user=user,
        search=search or None,
        department_id=department_id,
        status=status or None,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[EmployeeOut.model_validate(e) for e in employees],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def api_employee_create(
    payload: EmployeeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    employee = create_employee(
        db, org_id=user.org_id, actor=user, data=payload, request=request
    )
    return EmployeeOut.model_validate(employee)


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
def api_employee(
    employee_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return EmployeeOut.model_validate(get_employee_for(db, user, employee_id))


@router.patch("/employees/{employee_id}", response_model=EmployeeOut)
def api_employee_update(
    employee_id: UUID,
    payload: EmployeeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    employee = get_employee_for(db, user, employee_id)
    update_employee(db, actor=user, employee=employee, data=payload, request=request)
    return EmployeeOut.model_validate(employee)


@router.get("/departments", response_model=list[DepartmentOut])
def api_departments(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(Department)
            .where(Department.org_id == user.org_id, Department.active.is_(True))
            .order_by(Department.name)
        ).all()
    )


@router.post("/departments", response_model=DepartmentOut, status_code=201)
def api_department_create(
    payload: DepartmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    if db.scalar(
        select(Department.id).where(
            Department.org_id == user.org_id, Department.code == payload.code
        )
    ):
        raise ConflictError(f"Department code {payload.code} already exists")
    department = Department(
        org_id=user.org_id,
        name=payload.name,
        code=payload.code,
        parent_id=payload.parent_id,
        manager_employee_id=payload.manager_employee_id,
        cost_center=payload.cost_center,
    )
    db.add(department)
    db.flush()
    audit.record(
        db,
        actor=user,
        action=AuditAction.CREATE,
        entity_type="department",
        entity_id=department.id,
        details={"code": department.code},
        request=request,
    )
    return DepartmentOut.model_validate(department)


@router.get("/positions", response_model=list[PositionOut])
def api_positions(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(Position)
            .where(Position.org_id == user.org_id, Position.active.is_(True))
            .order_by(Position.title)
        ).all()
    )


@router.post("/positions", response_model=PositionOut, status_code=201)
def api_position_create(
    payload: PositionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    position = Position(
        org_id=user.org_id,
        title=payload.title,
        code=payload.code,
        department_id=payload.department_id,
        grade=payload.grade,
        level=payload.level,
    )
    db.add(position)
    db.flush()
    audit.record(
        db,
        actor=user,
        action=AuditAction.CREATE,
        entity_type="position",
        entity_id=position.id,
        details={"code": position.code},
        request=request,
    )
    return PositionOut.model_validate(position)


@router.get("/leave-types", response_model=list[LeaveTypeOut])
def api_leave_types(
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list(
        db.scalars(
            select(LeaveType).where(LeaveType.org_id == user.org_id, LeaveType.active.is_(True))
        ).all()
    )


@router.get("/leave/balances", response_model=list[LeaveBalanceOut])
def api_leave_balances(
    employee_id: UUID | None = None,
    year: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    target_id = employee_id or user.employee_id
    if target_id is None:
        raise ValidationError("No employee record is linked to this account")
    if employee_id and employee_id != user.employee_id:
        get_employee_for(db, user, employee_id)
    balances = balances_for(
        db, org_id=user.org_id, employee_id=target_id, year=year or date.today().year
    )
    return [LeaveBalanceOut.model_validate(balance) for balance in balances]


@router.get("/leave-requests", response_model=Page[LeaveRequestOut])
def api_leave_requests(
    status: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    leaves, total = list_leave_requests(
        db, actor=user, status=status or None, limit=limit, offset=offset
    )
    return Page(
        items=[LeaveRequestOut.model_validate(leave) for leave in leaves],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
def api_leave_create(
    payload: LeaveRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    leave = request_leave(db, actor=user, data=payload, request=request)
    return LeaveRequestOut.model_validate(leave)


@router.get("/leave-requests/{leave_id}", response_model=LeaveRequestOut)
def api_leave(
    leave_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return LeaveRequestOut.model_validate(get_leave(db, user, leave_id))


@router.post("/leave-requests/{leave_id}/decision", response_model=LeaveRequestOut)
def api_leave_decision(
    leave_id: UUID,
    payload: LeaveDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.MANAGER)),
):
    leave = decide_leave(db, actor=user, leave_id=leave_id, decision=payload, request=request)
    return LeaveRequestOut.model_validate(leave)


@router.post("/leave-requests/{leave_id}/cancel", response_model=LeaveRequestOut)
def api_leave_cancel(
    leave_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return LeaveRequestOut.model_validate(
        cancel_leave(db, actor=user, leave_id=leave_id, request=request)
    )


@router.get("/documents", response_model=list[DocumentOut])
def api_documents(
    employee_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    return list_documents(db, actor=user, employee_id=employee_id)


@router.post("/documents", response_model=DocumentOut, status_code=201)
def api_document_upload(
    request: Request,
    employee_id: UUID = Form(...),
    title: str = Form(...),
    category: DocumentCategory = Form(DocumentCategory.OTHER),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    document = save_document(
        db,
        actor=user,
        employee_id=employee_id,
        title=title,
        category=category,
        upload=file,
        request=request,
    )
    return DocumentOut.model_validate(document)


@router.get("/documents/{document_id}/download")
def api_document_download(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    document = get_document(db, actor=user, document_id=document_id)
    path = file_path(document)
    audit.record(
        db,
        actor=user,
        action=AuditAction.DOWNLOAD,
        entity_type="document",
        entity_id=document.id,
        details={"filename": document.filename},
        request=request,
    )
    return FileResponse(
        path, media_type=document.content_type, filename=document.filename
    )


@router.post("/documents/{document_id}/acknowledge")
def api_acknowledge_document(
    document_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    """Acknowledge reading a document."""
    acknowledge_document(db, actor=user, document_id=document_id, request=request)
    return {"detail": "Document acknowledged"}


@router.get("/audit", response_model=Page[AuditEventOut])
def api_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.AUDITOR)),
):
    from sqlalchemy import func

    stmt = select(AuditEvent).where(AuditEvent.org_id == user.org_id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = list(
        db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    )
    return Page(
        items=[AuditEventOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- Payroll Components ---
@router.get("/pay-components", response_model=list[dict])
def api_pay_components(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    components = payroll_service.list_pay_components(db, user.org_id)
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "code": c.code,
            "component_type": c.component_type.value,
            "is_taxable": c.is_taxable,
            "is_statutory": c.is_statutory,
            "default_amount": float(c.default_amount),
            "active": c.active,
        }
        for c in components
    ]


@router.post("/pay-components", status_code=201)
def api_pay_component_create(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    component = payroll_service.create_pay_component(
        db, org_id=user.org_id, actor=user, data=payload
    )
    return {"id": str(component.id), "detail": "Pay component created"}


# --- Compensation ---
@router.get("/employees/{employee_id}/compensation", response_model=dict)
def api_compensation(
    employee_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    comp = payroll_service.get_compensation(db, org_id=user.org_id, employee_id=employee_id)
    if not comp:
        return {"detail": "No compensation record found"}
    # Get components
    from ..models import CompensationComponent
    comp_components = db.scalars(
        select(CompensationComponent).where(CompensationComponent.compensation_id == comp.id)
    ).all()
    components_data = []
    for cc in comp_components:
        from ..models import PayComponent
        pc = db.get(PayComponent, cc.component_id)
        if pc:
            components_data.append({
                "id": str(cc.id),
                "component_id": str(pc.id),
                "component_name": pc.name,
                "component_type": pc.component_type.value,
                "amount": float(cc.amount),
                "effective_from": cc.effective_from.isoformat(),
            })
    return {
        "id": str(comp.id),
        "base_salary": float(comp.base_salary),
        "currency": comp.currency,
        "payroll_frequency": comp.payroll_frequency.value,
        "tax_regime": comp.tax_regime.value,
        "pay_scale": comp.pay_scale,
        "grade": comp.grade,
        "effective_from": comp.effective_from.isoformat(),
        "effective_to": comp.effective_to.isoformat() if comp.effective_to else None,
        "notes": comp.notes,
        "components": components_data,
    }


@router.post("/employees/{employee_id}/compensation", status_code=201)
def api_compensation_create(
    employee_id: UUID,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    comp = payroll_service.create_or_update_compensation(
        db, org_id=user.org_id, actor=user, employee_id=employee_id, data=payload
    )
    return {"id": str(comp.id), "detail": "Compensation saved"}


# --- Payroll Runs ---
@router.get("/payroll-runs", response_model=list[dict])
def api_payroll_runs(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    runs = payroll_service.list_payroll_runs(db, user.org_id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "pay_date": r.pay_date.isoformat() if r.pay_date else None,
            "frequency": r.frequency.value,
            "currency": r.currency,
            "status": r.status.value,
            "total_gross": float(r.total_gross),
            "total_net": float(r.total_net),
            "frozen": bool(r.frozen),
            "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
            "notes": r.notes,
        }
        for r in runs
    ]


@router.post("/payroll-runs", status_code=201)
def api_payroll_run_create(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.create_payroll_run(db, org_id=user.org_id, actor=user, data=payload)
    return {"id": str(run.id), "name": run.name, "detail": "Payroll run created"}


@router.post("/payroll-runs/{run_id}/process", status_code=200)
def api_payroll_run_process(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    payslips = payroll_service.generate_payslips(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"detail": f"Generated {len(payslips)} payslips", "payslip_count": len(payslips)}


@router.post("/payroll-runs/{run_id}/preview", status_code=200)
def api_payroll_run_preview(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    return payroll_service.preview_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)


@router.post("/payroll-runs/{run_id}/review", status_code=200)
def api_payroll_run_review(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.submit_payroll_run_for_review(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "status": run.status.value, "detail": "Payroll run submitted for review"}


@router.post("/payroll-runs/{run_id}/approve", status_code=200)
def api_payroll_run_approve(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.approve_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "status": run.status.value, "detail": "Payroll run approved"}


@router.post("/payroll-runs/{run_id}/finalize", status_code=200)
def api_payroll_run_finalize(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.finalize_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "status": run.status.value, "detail": "Payroll run finalized; payslips are immutable"}


@router.post("/payroll-runs/{run_id}/mark-paid", status_code=200)
def api_payroll_run_mark_paid(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.mark_paid_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "status": run.status.value, "detail": "Payroll run marked paid"}


@router.post("/payroll-runs/{run_id}/freeze", status_code=200)
def api_payroll_run_freeze(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.freeze_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "frozen": bool(run.frozen), "detail": "Payroll run frozen"}


@router.post("/payroll-runs/{run_id}/unfreeze", status_code=200)
def api_payroll_run_unfreeze(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    run = payroll_service.unfreeze_payroll_run(db, org_id=user.org_id, actor=user, run_id=run_id)
    return {"id": str(run.id), "frozen": bool(run.frozen), "detail": "Payroll run unfrozen"}


@router.get("/payroll-runs/{run_id}/variance", response_model=dict)
def api_payroll_run_variance(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    return payroll_service.payroll_run_variance(db, org_id=user.org_id, actor=user, run_id=run_id)


@router.get("/payroll-runs/{run_id}/bank-export", response_class=Response)
def api_payroll_bank_export(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    result = payroll_service.bank_export(db, org_id=user.org_id, actor=user, run_id=run_id)
    header = "employee_no,name,bank_name,account_number_masked,ifsc,net_pay,currency\n"
    body = "\n".join(
        ",".join(
            [
                str(row["employee_no"] or ""),
                f'"{row["name"] or ""}"',
                str(row["bank_name"] or ""),
                str(row["account_number_masked"] or ""),
                str(row["ifsc"] or ""),
                f'{row["net_pay"]:.2f}',
                row["currency"] or "",
            ]
        )
        for row in result["rows"]
    )
    csv = header + body + f"\nTOTAL,,,,{result['control_total']:.2f},{result['currency']}\n"
    return Response(content=csv, media_type="text/csv")


@router.get("/payroll-runs/{run_id}/accounting-export", response_model=dict)
def api_payroll_accounting_export(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.PAYROLL)),
):
    return payroll_service.accounting_export(db, org_id=user.org_id, actor=user, run_id=run_id)


@router.get("/payroll-runs/{run_id}/payslips", response_model=list[dict])
def api_payroll_payslips(
    run_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    run = db.get(PayrollRun, run_id)
    if not run or run.org_id != user.org_id:
        from ..services.errors import NotFoundError
        raise NotFoundError("Payroll run not found")

    from ..models import Payslip
    payslips = db.scalars(
        select(Payslip).where(Payslip.run_id == run_id)
    ).all()
    audit.record(
        db,
        actor=user,
        action=AuditAction.VIEW,
        entity_type="payroll_run_payslips",
        entity_id=run.id,
        details={"payslip_count": len(payslips)},
        org_id=user.org_id,
        request=request,
    )
    return [
        {
            "id": str(p.id),
            "employee_id": str(p.employee_id),
            "gross_pay": float(p.gross_pay),
            "total_deductions": float(p.total_deductions),
            "net_pay": float(p.net_pay),
            "bank_account_last4": p.bank_account_last4,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for p in payslips
    ]


@router.get("/payslips/{payslip_id}", response_model=dict)
def api_payslip(
    payslip_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user()),
):
    from ..models import Payslip, PayslipLine
    payslip = db.get(Payslip, payslip_id)
    if not payslip or payslip.org_id != user.org_id:
        from ..services.errors import NotFoundError
        raise NotFoundError("Payslip not found")
    # Check access
    if user.employee_id and payslip.employee_id != user.employee_id:
        from ..services import policy
        employee = db.get(Employee, payslip.employee_id)
        if employee is None or not policy.can_view_employee(user, employee):
            from ..services.errors import ForbiddenError
            raise ForbiddenError("Not authorized to view this payslip")

    # Field-level masking: only payroll/admin see amounts; employee sees own.
    masked = user.role.value not in {"admin", "payroll"} and (
        not user.employee_id or payslip.employee_id != user.employee_id
    )
    lines = db.scalars(select(PayslipLine).where(PayslipLine.payslip_id == payslip_id)).all()
    audit.record(
        db,
        actor=user,
        action=AuditAction.VIEW,
        entity_type="payslip",
        entity_id=payslip.id,
        details={"masked": masked},
        org_id=user.org_id,
        request=request,
    )
    return {
        "id": str(payslip.id),
        "run_id": str(payslip.run_id),
        "employee_id": str(payslip.employee_id),
        "gross_pay": float(payslip.gross_pay) if not masked else None,
        "total_deductions": float(payslip.total_deductions) if not masked else None,
        "net_pay": float(payslip.net_pay) if not masked else None,
        "masked": masked,
        "bank_account_last4": payslip.bank_account_last4 if not masked else None,
        "paid_at": payslip.paid_at.isoformat() if payslip.paid_at else None,
        "lines": [
            {
                "id": str(line.id),
                "component_name": line.component_name,
                "component_type": line.component_type.value,
                "amount": float(line.amount) if not masked else None,
                "quantity": float(line.quantity) if line.quantity else None,
                "unit": line.unit,
            }
            for line in lines
        ],
    }


# --- Bank Accounts ---
@router.get("/bank-accounts", response_model=list[dict])
def api_bank_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    accounts = payroll_service.list_bank_accounts(db, user.org_id)
    return [
        {
            "id": str(a.id),
            "employee_id": str(a.employee_id),
            "account_type": a.account_type.value,
            "bank_name": a.bank_name,
            "branch": a.branch,
            "account_holder": a.account_holder,
            "account_number_masked": a.account_number_masked,
            "ifsc": a.ifsc,
            "swift": a.swift,
            "is_primary": a.is_primary,
            "active": a.active,
        }
        for a in accounts
    ]


@router.post("/bank-accounts", status_code=201)
def api_bank_account_create(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR, Role.PAYROLL)),
):
    account = payroll_service.create_bank_account(db, org_id=user.org_id, actor=user, data=payload)
    return {"id": str(account.id), "detail": "Bank account added"}


# --- Benefit Plans ---
@router.get("/benefit-plans", response_model=list[dict])
def api_benefit_plans(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    plans = payroll_service.list_benefit_plans(db, user.org_id)
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "code": p.code,
            "benefit_type": p.benefit_type.value,
            "provider": p.provider,
            "employer_contribution": float(p.employer_contribution),
            "employee_contribution": float(p.employee_contribution),
            "active": p.active,
        }
        for p in plans
    ]


@router.post("/benefit-plans", status_code=201)
def api_benefit_plan_create(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    plan = payroll_service.create_benefit_plan(db, org_id=user.org_id, actor=user, data=payload)
    return {"id": str(plan.id), "detail": "Benefit plan created"}


# --- Employee Benefits ---
@router.post("/benefits/enroll", status_code=201)
def api_benefit_enroll(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    enrollment = payroll_service.enroll_employee_in_benefit(
        db, org_id=user.org_id, actor=user, data=payload
    )
    return {"id": str(enrollment.id), "detail": "Employee enrolled in benefit"}


# --- Legal Entities ---
@router.get("/legal-entities", response_model=list[dict])
def api_legal_entities(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    entities = payroll_service.list_legal_entities(db, user.org_id)
    return [
        {
            "id": str(e.id),
            "name": e.name,
            "code": e.code,
            "country_code": e.country_code,
            "currency": e.currency,
            "tax_id": e.tax_id,
            "address": e.address,
            "active": e.active,
        }
        for e in entities
    ]


# --- Statutory Rule Versions ---
@router.get("/statutory-rule-versions", response_model=list[dict])
def api_statutory_rule_versions(
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    versions = payroll_service.list_statutory_rule_versions(db, user.org_id)
    return [
        {
            "id": str(v.id),
            "rule_type": v.rule_type.value,
            "country_code": v.country_code,
            "state_code": v.state_code,
            "effective_from": v.effective_from.isoformat(),
            "effective_to": v.effective_to.isoformat() if v.effective_to else None,
            "version": v.version,
            "rates": v.rates,
            "active": v.active,
            "notes": v.notes,
        }
        for v in versions
    ]


@router.post("/statutory-rule-versions", status_code=201)
def api_statutory_rule_version_create(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    version = payroll_service.create_statutory_rule_version(db, org_id=user.org_id, actor=user, data=payload)
    return {"id": str(version.id), "detail": "Statutory rule version created"}


@router.put("/statutory-rule-versions/{rule_version_id}", status_code=200)
def api_statutory_rule_version_update(
    rule_version_id: UUID,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    version = payroll_service.update_statutory_rule_version(
        db, org_id=user.org_id, rule_version_id=rule_version_id, actor=user, data=payload
    )
    return {"id": str(version.id), "detail": "Statutory rule version updated"}


@router.delete("/statutory-rule-versions/{rule_version_id}", status_code=200)
def api_statutory_rule_version_delete(
    rule_version_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(Role.ADMIN, Role.HR)),
):
    payroll_service.delete_statutory_rule_version(db, org_id=user.org_id, rule_version_id=rule_version_id, actor=user)
    return {"detail": "Statutory rule version deleted"}


__all__ = ["router"]

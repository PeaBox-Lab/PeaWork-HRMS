from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..cookies import clear_auth_cookies, set_auth_cookies
from ..db import get_db
from ..deps import csrf_guard, request_ip, require_user
from ..domain import AuditAction
from ..models import Employee, User
from ..security import password_problems, verify_password
from ..services import audit
from ..services.auth import authenticate, change_password, create_session, revoke_session
from ..services.errors import ValidationError
from ..services.leave import balances_for
from ..templating import render
from .forms import text

router = APIRouter(dependencies=[Depends(csrf_guard)])


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@router.get("/login")
def login_page(request: Request, next: str = ""):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", next=safe_next(next))


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    ip = request_ip(request)
    existing = getattr(request.state, "session", None)
    if existing is not None:
        revoke_session(db, existing)

    user = authenticate(
        db, email=email, password=password, ip=ip, user_agent=request.headers.get("user-agent")
    )
    if user is None:
        return RedirectResponse("/login?error=Invalid+email+or+password", status_code=303)

    raw_token, csrf_token, _ = create_session(
        db, user=user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    response = RedirectResponse(safe_next(next), status_code=303)
    set_auth_cookies(
        response, settings, session_token=raw_token, csrf_token=csrf_token
    )
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    session = getattr(request.state, "session", None)
    user = getattr(request.state, "user", None)
    if session is not None:
        revoke_session(db, session)
        if user is not None:
            audit.record(
                db,
                actor=user,
                action=AuditAction.LOGOUT,
                entity_type="auth",
                request=request,
            )
    response = RedirectResponse("/login?msg=Signed+out", status_code=303)
    clear_auth_cookies(response, settings)
    return response


@router.get("/profile")
def profile(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user(allow_password_change=True)),
):
    employee = db.get(Employee, user.employee_id) if user.employee_id else None
    balances = (
        balances_for(db, org_id=user.org_id, employee_id=employee.id, year=date.today().year)
        if employee
        else []
    )
    return render(request, "profile.html", employee=employee, balances=balances)


@router.post("/profile")
def profile_update(
    request: Request,
    phone: str = Form(""),
    personal_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(allow_password_change=True)),
):
    if not user.employee_id:
        return RedirectResponse("/profile?error=Your+account+is+not+linked+to+an+employee", status_code=303)
    employee = db.get(Employee, user.employee_id)
    if employee is None or employee.org_id != user.org_id:
        return RedirectResponse("/profile?error=Employee+record+not+found", status_code=303)

    employee.phone = text(phone)
    employee.personal_email = text(personal_email)
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="employee",
        entity_id=employee.id,
        details={"changed": ["phone", "personal_email"], "self_service": True},
        request=request,
    )
    return RedirectResponse("/profile?msg=Profile+updated", status_code=303)


@router.post("/profile/password")
def password_change(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user(allow_password_change=True)),
):
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/profile?error=Current+password+is+incorrect", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/profile?error=New+passwords+do+not+match", status_code=303)
    problems = password_problems(new_password)
    if problems:
        return RedirectResponse(
            "/profile?error=" + quote("; ".join(problems)), status_code=303
        )
    try:
        change_password(db, user, new_password)
    except ValidationError as exc:
        return RedirectResponse(f"/profile?error={exc.message}", status_code=303)
    audit.record(
        db,
        actor=user,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        details={"password_changed": True},
        request=request,
    )
    settings = get_settings()
    response = RedirectResponse("/login?msg=Password+changed.+Sign+in+again.", status_code=303)
    clear_auth_cookies(response, settings)
    return response


__all__ = ["router", "safe_next"]

"""Billing & Subscription management for PeaWork HRMS (commercial SaaS path).

Provides:
- Plan definitions with limits (users, storage, API calls).
- Usage tracking per tenant.
- Overage detection and alerts.
- Integration points for Stripe / Paddle webhooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .config import get_settings


@dataclass
class Plan:
    """Subscription plan definition."""

    code: str
    name: str
    price_monthly: Decimal
    price_annual: Decimal | None
    currency: str
    limits: dict[str, int]  # users, storage_mb, api_calls_per_day, etc.
    features: list[str]


DEFAULT_PLANS: dict[str, Plan] = {
    "free": Plan(
        code="free",
        name="Free",
        price_monthly=Decimal("0"),
        price_annual=None,
        currency="INR",
        limits={"users": 10, "storage_mb": 100, "api_calls_per_day": 1000},
        features=["core_hr", "leave", "documents"],
    ),
    "starter": Plan(
        code="starter",
        name="Starter",
        price_monthly=Decimal("999"),
        price_annual=Decimal("9999"),
        currency="INR",
        limits={"users": 50, "storage_mb": 1000, "api_calls_per_day": 10000},
        features=["core_hr", "leave", "documents", "payroll", "attendance", "talent"],
    ),
    "pro": Plan(
        code="pro",
        name="Professional",
        price_monthly=Decimal("4999"),
        price_annual=Decimal("49999"),
        currency="INR",
        limits={"users": 200, "storage_mb": 5000, "api_calls_per_day": 50000},
        features=["core_hr", "leave", "documents", "payroll", "attendance", "talent", "analytics", "sso", "api"],
    ),
    "enterprise": Plan(
        code="enterprise",
        name="Enterprise",
        price_monthly=Decimal("19999"),
        price_annual=Decimal("199999"),
        currency="INR",
        limits={"users": 2000, "storage_mb": 50000, "api_calls_per_day": 200000},
        features=["core_hr", "leave", "documents", "payroll", "attendance", "talent", "analytics", "sso", "api", "custom_integrations", "dedicated_support"],
    ),
}


def get_plan(code: str) -> Plan | None:
    """Get a plan by code."""
    return DEFAULT_PLANS.get(code)


def list_plans() -> list[Plan]:
    """List all available plans."""
    return list(DEFAULT_PLANS.values())


@dataclass
class Usage:
    """Current usage for a tenant."""

    tenant_id: str
    plan_code: str
    users: int = 0
    storage_mb: int = 0
    api_calls_today: int = 0
    last_reset: str = ""  # date ISO


def check_limit(usage: Usage, metric: str) -> tuple[bool, int, int]:
    """Check if usage exceeds plan limit for a metric.

    Returns: (within_limit, current_usage, limit)
    """
    plan = get_plan(usage.plan_code)
    if not plan:
        return False, 0, 0
    limit = plan.limits.get(metric, 0)
    current = getattr(usage, metric, 0)
    return current < limit, current, limit


def is_overage(usage: Usage) -> dict[str, Any]:
    """Check all limits and return overage info."""
    overages = {}
    for metric in ("users", "storage_mb", "api_calls_today"):
        ok, current, limit = check_limit(usage, metric)
        if not ok:
            overages[metric] = {"current": current, "limit": limit, "overage": current - limit}
    return {"has_overage": len(overages) > 0, "details": overages}


def format_usage(usage: Usage) -> dict[str, Any]:
    """Format usage for API response."""
    plan = get_plan(usage.plan_code)
    return {
        "tenant_id": usage.tenant_id,
        "plan": usage.plan_code,
        "plan_name": plan.name if plan else "Unknown",
        "limits": plan.limits if plan else {},
        "current": {
            "users": usage.users,
            "storage_mb": usage.storage_mb,
            "api_calls_today": usage.api_calls_today,
        },
        "overage": is_overage(usage),
    }


# ---------------------------------------------------------------------------
# Subscription state (would be persisted in DB for real multi-tenant)
# ---------------------------------------------------------------------------

_SUBSCRIPTIONS: dict[str, Usage] = {}


def get_usage(tenant_id: str) -> Usage:
    """Get usage for a tenant (creates default if not exists)."""
    if tenant_id not in _SUBSCRIPTIONS:
        _SUBSCRIPTIONS[tenant_id] = Usage(
            tenant_id=tenant_id,
            plan_code="free",
            last_reset=date.today().isoformat(),
        )
    return _SUBSCRIPTIONS[tenant_id]


def set_plan(tenant_id: str, plan_code: str) -> Usage:
    """Change tenant's plan."""
    usage = get_usage(tenant_id)
    if plan_code not in DEFAULT_PLANS:
        raise ValueError(f"Unknown plan: {plan_code}")
    usage.plan_code = plan_code
    return usage


def increment_usage(tenant_id: str, metric: str, amount: int = 1) -> Usage:
    """Increment a usage metric."""
    usage = get_usage(tenant_id)
    if metric == "users":
        usage.users += amount
    elif metric == "storage_mb":
        usage.storage_mb += amount
    elif metric == "api_calls_today":
        usage.api_calls_today += amount
    return usage


def reset_daily_counters() -> None:
    """Reset daily API call counters (call from scheduled job)."""
    today = date.today().isoformat()
    for usage in _SUBSCRIPTIONS.values():
        if usage.last_reset != today:
            usage.api_calls_today = 0
            usage.last_reset = today


__all__ = [
    "Plan",
    "Usage",
    "get_plan",
    "list_plans",
    "check_limit",
    "is_overage",
    "format_usage",
    "get_usage",
    "set_plan",
    "increment_usage",
    "reset_daily_counters",
    "DEFAULT_PLANS",
]
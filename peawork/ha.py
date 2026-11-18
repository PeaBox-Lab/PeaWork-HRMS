"""High Availability & zero-downtime migration support for PeaWork HRMS.

This module provides:
- A heartbeat/health-check registry for HA nodes.
- A readiness probe that gates traffic during deployments.
- Helpers for zero-downtime Alembic migrations (add-then-backfill-then-drop).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings


@dataclass
class NodeHealth:
    """Health state of a single HA node."""

    node_id: str
    last_heartbeat: float
    status: str = "unknown"  # healthy | unhealthy | unknown

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


class HAManager:
    """Manages HA node registration, heartbeats, and readiness gating."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._nodes: dict[str, NodeHealth] = {}
        self._ready: bool = True
        self._ready_reason: str = "ready"
        self._lock = asyncio.Lock()

    async def register_node(self, node_id: str) -> None:
        """Register a new HA node."""
        async with self._lock:
            self._nodes[node_id] = NodeHealth(
                node_id=node_id,
                last_heartbeat=time.time(),
                status="healthy",
            )

    async def heartbeat(self, node_id: str) -> None:
        """Record a heartbeat from a node."""
        async with self._lock:
            if node_id in self._nodes:
                self._nodes[node_id].last_heartbeat = time.time()
                self._nodes[node_id].status = "healthy"

    async def mark_unhealthy(self, node_id: str, reason: str = "") -> None:
        """Mark a node as unhealthy."""
        async with self._lock:
            if node_id in self._nodes:
                self._nodes[node_id].status = "unhealthy"
                self._nodes[node_id].last_heartbeat = time.time()

    async def get_healthy_nodes(self) -> list[NodeHealth]:
        """Return all currently healthy nodes."""
        async with self._lock:
            return [n for n in self._nodes.values() if n.is_healthy]

    async def is_ready(self) -> bool:
        """Return True if the app is ready to serve traffic."""
        return self._ready

    async def set_ready(self, ready: bool, reason: str = "") -> None:
        """Set readiness state."""
        self._ready = ready
        self._ready_reason = reason

    def readiness_status(self) -> dict[str, Any]:
        """Return a JSON-serializable readiness report."""
        healthy = [n.node_id for n in self._nodes.values() if n.is_healthy]
        unhealthy = [n.node_id for n in self._nodes.values() if not n.is_healthy]
        return {
            "ready": self._ready,
            "reason": self._ready_reason,
            "nodes_total": len(self._nodes),
            "nodes_healthy": len(healthy),
            "nodes_unhealthy": len(unhealthy),
            "healthy_nodes": healthy,
            "unhealthy_nodes": unhealthy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _monitor_readiness(self) -> None:
        """Background task that checks node health and marks app ready/unready.

        Implements active-passive HA: if more than max_unhealthy_nodes are down, set not ready.
        """
        while True:
            if self.settings.ha_enabled:
                healthy = await self.get_healthy_nodes()
                healthy_count = len(healthy)
                max_unhealthy = self.settings.ha_max_unhealthy_nodes
                ready = (healthy_count >= (len(self._nodes) - max_unhealthy))
                if ready != self._ready:
                    await self.set_ready(ready, f"Healthy nodes {healthy_count}/{len(self._nodes)}")
            await asyncio.sleep(self.settings.ha_heartbeat_interval)


class HAMiddleware:
    """Starlette middleware that gates traffic based on HA readiness."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        manager = get_ha_manager()
        if not await manager.is_ready():
            response = JSONResponse(
                {"status": "not_ready", "reason": manager.readiness_status()["reason"]},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


_ha_manager: HAManager | None = None


def get_ha_manager() -> HAManager:
    global _ha_manager
    if _ha_manager is None:
        _ha_manager = HAManager()
    return _ha_manager


# ---------------------------------------------------------------------------
# Zero-downtime migration helpers
# ---------------------------------------------------------------------------


def zdt_add_column(
    table_name: str,
    column_name: str,
    column_type: str,
    nullable: bool = True,
    default: Any = None,
) -> str:
    """Generate SQL for adding a column safely (nullable first)."""
    null_sql = "NULL" if nullable else "NOT NULL"
    default_sql = f" DEFAULT {default}" if default is not None else ""
    return f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{default_sql} {null_sql};"


def zdt_backfill(
    table_name: str,
    column_name: str,
    value: Any,
    where_clause: str | None = None,
) -> str:
    """Generate SQL for backfilling a column in batches."""
    sql = f"UPDATE {table_name} SET {column_name} = {value}"
    if where_clause:
        sql += f" WHERE {where_clause}"
    sql += ";"
    return sql


def zdt_drop_column(table_name: str, column_name: str) -> str:
    """Generate SQL for dropping a column (after data is migrated)."""
    return f"ALTER TABLE {table_name} DROP COLUMN {column_name};"


def zdt_create_index_concurrently(
    table_name: str,
    column_name: str,
    index_name: str | None = None,
) -> str:
    """Generate SQL for creating an index concurrently (PostgreSQL)."""
    idx_name = index_name or f"ix_{table_name}_{column_name}"
    return f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name} ON {table_name} ({column_name});"


def zdt_drop_index_concurrently(index_name: str) -> str:
    """Generate SQL for dropping an index concurrently (PostgreSQL)."""
    return f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};"


__all__ = [
    "HAManager",
    "NodeHealth",
    "HAMiddleware",
    "get_ha_manager",
    "zdt_add_column",
    "zdt_backfill",
    "zdt_drop_column",
    "zdt_create_index_concurrently",
    "zdt_drop_index_concurrently",
]
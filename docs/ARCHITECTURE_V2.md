# PeaWork v2 — Target Architecture

**Audience:** engineers and operators  
**Status:** implemented incrementally in `peawork/`  
**Constraint:** must run on one Linux server (Docker or systemd), using open-source components only.

---

## 1. Design at a glance

```
Browser / PWA
     │  HTTPS
     ▼
Caddy 2 (TLS, reverse proxy, security headers, compression)
     │  127.0.0.1 / docker network
     ▼
Gunicorn + Uvicorn workers  ──  FastAPI app factory
     │
     ├── Web (Jinja2 + HTMX, server-rendered, CSP-safe)
     ├── JSON API (/api/v1, OpenAPI 3.1)
     ├── Domain services (authz, people, leave, documents, audit)
     └── Storage adapter (local private dir → MinIO/S3 later)
     │
     ▼
PostgreSQL 16  (primary datastore, migrations via Alembic)
     │
     ├── scheduled pg_dump + retention + offsite copy
     └── optional Redis 7 (cache/rate-limit/jobs in R2)
```

### Why a modular monolith

For 50–2,000 employees, a modular monolith gives one transaction boundary, one deployable, and far lower operational cost than microservices. Modules are enforced by package boundaries and import rules, not by network hops. Services are extracted only when a measured constraint demands it (payroll batch, notifications, reporting).

---

## 2. Layers

| Layer | Location | Rules |
|---|---|---|
| Transport | `peawork/web/`, `peawork/api/` | Parse input, call services, render. No business logic, no raw SQL. |
| Domain services | `peawork/services/` | Transactions, invariants, authorization decisions, audit emission. |
| Models | `peawork/models.py` | SQLAlchemy 2.0 typed mappings; constraints and indexes. |
| Schemas | `peawork/schemas.py` | Pydantic v2 request/response contracts. |
| Platform | `peawork/config.py`, `db.py`, `security.py`, `middleware.py` | Settings, engine/session, crypto, headers, request context. |
| Migrations | `peawork/migrations/` | Alembic revisions; production is forward-only. |

Import direction: transport → services → models. Never transport → models directly for writes.

---

## 3. Security model

### Authentication
- **Argon2id** password hashing (`argon2-cffi`), tuned parameters, rehash-on-login.
- **Opaque session tokens**: 256-bit random, stored only as SHA-256 hashes, cookie `HttpOnly; Secure; SameSite=Lax`.
- Absolute expiry (12 h default) + idle timeout (2 h), rotation on login and privilege change.
- Login throttling by username + IP, generic error messages, security-event logging.
- MFA (TOTP/WebAuthn) and SSO/SCIM in R5.

### Authorization
Central policy in `services/policy.py`:

```
can(actor, action, resource) -> bool
scope(actor) -> QueryFilter
```

Roles: `admin`, `hr`, `manager`, `employee`, `auditor`, `payroll`.  
Object rules: self, manager-of, HRBP-by-department, payroll-only, auditor read-only.  
Deny by default. Every list endpoint applies `scope()`; every detail endpoint calls `can()`.  
Authorization tests are a required matrix per module.

### Transport
- TLS terminated by Caddy; HSTS in production.
- CSP: `default-src 'self'`; no inline scripts; styles from self.
- `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`.
- CSRF: double-submit cookie + per-session hash; required on every unsafe method except Bearer-token API calls.
- Request size limits at proxy and app; upload validation + optional ClamAV.

### Data
- Secrets from environment/secret manager, never defaults in production.
- Sensitive fields encrypted at rest (R2+). PII redacted from logs and audit details.
- Least-privilege DB roles: app role cannot `DROP`/`ALTER`; migration role separate.
- Backups encrypted; restore rehearsed quarterly.

---

## 4. Data model (R1)

```
organizations
  └── users ── sessions
  └── departments ── positions
  └── employees ── employee_history
        ├── leave_balances ── leave_types
        ├── leave_requests
        ├── attendance_records ── shifts
        └── documents
  └── holidays
  └── audit_events
  └── notifications
```

Key decisions:
- **UUID primary keys** for externally exposed entities (no enumeration).
- **Effective-dated history** for employment changes; current state is a projection, not a second truth.
- **`NUMERIC`** for money and rates (from R3).
- **Append-only audit**; DB grants prevent UPDATE/DELETE by the app role.
- **Soft state via explicit status enums**, not nullable "active" flags alone.
- **`timestamptz`** everywhere; store UTC, render in org timezone.

---

## 5. Request lifecycle

1. Caddy terminates TLS, adds request ID if absent.
2. Middleware: request ID, security headers, CSRF cookie bootstrap.
3. Session middleware: resolve cookie → session row → user; attach to `request.state`.
4. Route dependency: `require_user(roles)` and `csrf_guard` for unsafe methods.
5. Route calls a service inside a DB transaction.
6. Service enforces policy, invariants, writes audit event.
7. Response: HTML (Jinja2) or JSON (Pydantic).
8. Log: structured JSON with method, path, status, duration, user id, request id. No PII.

---

## 6. Deployment topologies

### A. Single small server (default)
- 2 vCPU / 4 GB RAM / 40 GB SSD
- Docker Compose: Caddy, app, PostgreSQL, backup
- Nightly `pg_dump`, 30-day retention, weekly offsite copy
- Target: up to ~500 employees

### B. Single medium server
- 4 vCPU / 8 GB RAM
- Gunicorn 4 workers, PostgreSQL tuned, MinIO for documents
- Target: up to ~2,000 employees

### C. HA (R5)
- 2+ app replicas behind a load balancer
- Managed PostgreSQL with PITR, Redis, object storage
- Zero-downtime migrations (expand/contract)
- Target: multi-tenant SaaS

### Non-Docker alternative
`deploy/systemd/peawork.service` runs Gunicorn as a non-root user; Caddy runs on the host; PostgreSQL from distro packages. Same environment contract.

---

## 7. Observability

- `/health/live` — process alive.
- `/health/ready` — DB reachable, migrations current.
- `/metrics` — Prometheus; bound to internal network only.
- JSON logs to stdout → journald/Docker → Loki optional.
- Alert rules: 5xx rate, p95 latency, DB connections, failed logins, backup age, disk.
- Grafana dashboards (optional profile) for HTTP, DB, jobs.

---

## 8. Backup & disaster recovery

| Asset | Method | Frequency | Retention |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc` + WAL archiving (PITR in R5) | Daily / continuous | 30 days |
| Documents | filesystem snapshot or MinIO replication | Daily | 30 days |
| Config/secrets | secret manager export (sealed) | On change | 90 days |

Restore is a scripted, tested procedure: provision clean host → restore dump → verify checksums → run migrations → smoke test. RPO ≤ 15 min, RTO ≤ 2 h for topology A.

---

## 9. Testing strategy

- **Unit:** services, policy, date/leave math, rounding.
- **Integration:** API with a real PostgreSQL container (SQLite only for fast unit runs).
- **E2E:** Playwright for login, employee create, leave approve, document upload.
- **Contract:** OpenAPI snapshot; response schema validation.
- **Security:** authz matrix, CSRF, session expiry, upload abuse.
- **Load:** k6 profiles for list/detail/write.
- **Migrations:** upgrade from previous release fixture.

CI gates: ruff, mypy (incremental), pytest + coverage, alembic check, container build, pip-audit, secret scan.

---

## 10. Architecture decision records (initial)

| ID | Decision | Rationale | Revisit |
|---|---|---|---|
| ADR-001 | Modular monolith | Transactional integrity, low ops cost for target size | R5 if scale demands |
| ADR-002 | PostgreSQL only in production | Concurrency, constraints, PITR, RLS path | — |
| ADR-003 | Server-rendered Jinja2 + HTMX | No Node build, fast on low-end devices, accessible | If rich client needed |
| ADR-004 | Opaque DB-backed sessions | Revocation, simplicity, no custom crypto | If stateless scale needed |
| ADR-005 | UUID public identifiers | Prevent enumeration, merge-friendly | — |
| ADR-006 | Single organization per deployment | Removes tenant-confusion class of bugs | R5 |
| ADR-007 | Caddy as edge proxy | Automatic TLS, tiny config, open source | — |
| ADR-008 | Forward-only prod migrations | Predictable upgrades, backups as rollback | — |

---

## 11. Package layout

```
peawork/
  __init__.py
  main.py            # create_app, lifespan, router wiring
  config.py          # pydantic-settings
  db.py              # engine, session factory
  models.py          # SQLAlchemy 2.0 models
  schemas.py         # Pydantic v2 contracts
  security.py        # Argon2, tokens, CSRF helpers
  middleware.py      # request context, headers, session
  deps.py            # auth/CSRF dependencies, templates
  services/
    __init__.py
    audit.py
    employees.py
    leave.py
    documents.py
    policy.py
    seed.py
  api/
    __init__.py
    v1.py
  web/
    __init__.py
    auth.py
    dashboard.py
    employees.py
    leave.py
    admin.py
  templates/
  static/
  migrations/
deploy/
  Dockerfile
  docker-compose.yml
  Caddyfile
  .env.example
  backup.sh
  systemd/peawork.service
tests/
```

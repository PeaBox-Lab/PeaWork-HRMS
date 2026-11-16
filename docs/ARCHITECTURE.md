# Architecture

PeaWork starts as a modular monolith. Modules share one transaction boundary while keeping domain separation: identity/access, people, organization, leave, documents, reporting and audit.

## Current local topology

Browser → FastAPI/Jinja UI + REST API → SQLite → local upload vault.

## Local server target

Browser → reverse proxy/TLS → PeaWork app → PostgreSQL → encrypted file storage. Package through Docker Compose, scheduled backups and restore verification.

## Cloud target

CDN/WAF → load balancer → stateless PeaWork application → managed PostgreSQL + object storage + queue/workers. Add tenant_id to all tenant-owned records, database row-level security, per-tenant encryption context, centralized observability, SSO/SAML, SCIM and point-in-time recovery.

## Why modular monolith first

For the 50–2,000 employee target, a modular monolith lowers deployment and transaction complexity. Extract payroll calculations, notifications, reporting and integrations into services only after measurable scale or release-cadence pressure.

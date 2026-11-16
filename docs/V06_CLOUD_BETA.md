# v0.6 Cloud beta

## Architecture and isolation

PeaWork v0.6 introduces a control plane separate from tenant application data.

- **Local cloud-beta mode:** each tenant receives a separate SQLite database file under the tenant database root.
- **PostgreSQL mode:** each tenant receives a separate PostgreSQL schema selected through request-scoped context.
- Tenant identity resolves from a verified custom domain, the tenant cookie, or the configured default tenant.
- Authentication tokens include the tenant slug. A token issued in one tenant is rejected when replayed against another tenant.
- Switching tenants clears the existing application session and requires a new sign-in.
- The global cloud control plane is available only from the `local` control tenant; tenant administrators cannot administer other tenants.

This is schema/database isolation rather than row-label isolation, so existing HR, recruitment, workforce, talent and payroll queries operate only against the active tenant database/schema.

## Cloud control workflows

### Tenants and subscriptions

Administrators create an isolated tenant with a slug, region and subscription plan. The active subscription determines the employee limit; employee creation and candidate conversion stop when the limit is reached. Subscription lifecycle states are Trial, Active, Past Due, Suspended and Cancelled.

### Custom domains

A domain receives a unique verification token. The beta records verification and TLS-provisioning state after the administrator confirms the token. Production deployment must connect this workflow to authoritative DNS verification and the ingress/certificate provider; manually matching the token in the beta is not independent DNS proof.

### SSO and SCIM

The control plane stores OIDC or SAML provider metadata and configuration status. It does not claim end-to-end federation certification; production enablement requires provider-specific callback, signature, metadata and logout validation.

SCIM uses one-time bearer tokens. Only the token hash and visible prefix are stored. `POST /scim/v2/Users` creates or updates a tenant-local user and restricts provisioned roles to employee, manager, HR, recruiter or payroll. The beta endpoint covers core user provisioning, not the complete SCIM protocol surface.

### PostgreSQL deployment

`PEAWORK_DATABASE_URL` selects PostgreSQL. The database adapter converts application placeholders, creates the tenant schema and initializes the existing PeaWork schema inside it. `docker-compose.cloud.yml` provides a PostgreSQL target and object-storage service. Run `scripts/cloud_preflight.py` before starting a cloud environment.

### Object storage

Uploads use a tenant-prefixed key and either the local upload vault or an S3-compatible backend. Cloud configuration requires a bucket, region and server-side encryption. `scripts/create_object_bucket.py` prepares the configured bucket.

### Monitoring

The cloud console runs checks against the control database and each selected tenant database, recording status, latency and details. `/metrics` exposes counts for active tenants, failed backups and migrations needing attention. Production monitoring must add authentication/network restriction, alert routing, request metrics and centralized logs.

### Backup and PITR

- SQLite tenant backup uses the SQLite backup API and records location, size and outcome.
- PostgreSQL mode records a managed PITR request/checkpoint. Actual snapshots, WAL retention and restore execution must be connected to the managed database provider.
- Each tenant records frequency, retention, PITR state and target RPO/RTO.

A completed backup record is not a restore test. Production readiness still requires measured restore drills.

### Migration assistant

The assistant accepts only SQLite databases inside the PeaWork data directory. Preflight inventories every table, record count and source size. Execution requires typing `MIGRATE`.

- SQLite destination: the source database is copied with the SQLite backup API.
- PostgreSQL destination: the source tables are loaded into the tenant schema and serial sequences are reset.

The source and destination cannot be the same. Migration status, counts and failures remain in the control audit trail.

## Cloud beta deployment

1. Copy `.env.cloud.example` to `.env` and replace every secret.
2. Install the pinned requirements or build the container.
3. Start PostgreSQL and object storage with `docker compose -f docker-compose.cloud.yml up -d --build`.
4. Create the object bucket.
5. Open PeaWork on port 8090 and sign in to the local control tenant.
6. Create tenants, configure storage and identity metadata, then run health checks and backups.

## Production gaps

The cloud beta is not production certification. Before production, complete provider-certified SSO, full SCIM interoperability, authoritative DNS/TLS automation, managed-secret integration, control-plane high availability, PostgreSQL migration/load testing, object retention policies, centralized immutable audit, authenticated metrics, alerting, backup restore drills, penetration testing, DPDP workflows and disaster-recovery exercises.

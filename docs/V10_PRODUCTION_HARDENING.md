# v1.0 production hardening candidate

## Status

This release implements production-readiness controls and evidence workflows. It is **not itself an ASVS certification, penetration-test result, accessibility conformance claim, legal opinion, DPDP compliance certification, or disaster-recovery certification**. The Production Readiness page remains “Not production certified” until every required gate has qualified reviewer evidence.

## Implemented application controls

- Versioned PBKDF2 password hashing with automatic upgrade from the earlier stored format.
- Tenant-bound signed sessions with unique IDs, server-side registry, expiry and revocation.
- Login throttling by tenant, username and IP, with high-severity security event creation.
- Signed session-bound CSRF protection for unsafe methods.
- Strict session cookie behavior and Secure cookies/HSTS in cloud or production environments.
- CSP, frame denial, MIME-sniff prevention, referrer and browser-permission headers.
- Upload extension/MIME/size checks and optional fail-closed ClamAV-compatible streaming scan.
- Bootstrap refusal when cloud/production secrets or admin password retain development defaults.
- Immutable-style application/cloud/security evidence records and evidenced resolution workflow.

## Privacy and retention

The Privacy page supports draft/approved retention policies, legal holds, dry runs, executed-run evidence, access/correction/erasure/grievance/consent-withdrawal requests, identity verification, decisions and breach incidents.

Retention execution is intentionally conservative. Notifications, document metadata, resolved security events and login-attempt records can be deleted under an approved, non-held policy. Candidate records are counted and routed to manual anonymization review rather than automatically removed because connected recruitment and employment evidence may require a fact-specific decision. All retention periods and DPDP decisions require qualified privacy-counsel approval.

## Accessibility and localization

- A keyboard-visible skip link and focus indicators are applied globally.
- Form controls meet a minimum target size; reduced-motion preference is respected.
- Tables on readiness/privacy pages use captions and scoped headers.
- Login fields use appropriate labels and autocomplete metadata.
- The navigation shell supports English and Hindi catalogs and saves the user’s locale.
- The readiness workflow records page-specific accessibility findings and resolution evidence.

The included language catalog is a framework and initial shell translation, not full linguistic review. Independent accessibility and localization audits remain release gates.

## Resilience and performance

- DR exercise records require scenario, target and actual RPO/RTO, verified restore evidence and approver before completion.
- `scripts/load_test.py` runs a bounded HTTP scenario and records concurrency, requests, errors and p50/p95/p99 latency to JSON.
- Load-test evidence is recorded in the readiness workflow; the accepted concurrency and latency/error thresholds must be approved by management.
- Existing backup/PITR controls remain, but restore evidence—not a backup-success message—is required for the DR gate.

## Release procedure

1. Configure managed secrets, bootstrap password, PostgreSQL, object storage and required malware scanner.
2. Run unit/integration tests and organization-approved SAST/dependency scanning.
3. Run the load scenario at management-approved concurrency and attach the output.
4. Perform and evidence a restore exercise against the approved RPO/RTO.
5. Complete accessibility and localization review.
6. Complete independent ASVS Level 2 assessment and external penetration test; remediate findings.
7. Have qualified privacy counsel validate notices, purposes, retention, requests, grievance and breach workflows.
8. Record each report and reviewer in Production Readiness.
9. Run `python scripts/readiness_check.py`; do not approve release unless it exits successfully.

## Known limitations

MFA and live OIDC/SAML federation still depend on provider integration from v0.6. Centralized immutable audit export, managed-secret-provider integration, independent security testing, full Hindi content translation, authoritative legal retention periods and production load thresholds are evidence/integration gates rather than claims completed by this repository.

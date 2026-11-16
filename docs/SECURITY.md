# Security and Privacy Baseline    
## Implemented in v0.1    
- PBKDF2-HMAC-SHA256 password storage with unique salts and 210,000 iterations.    
- Signed, HTTP-only, SameSite=Lax session cookie.    
- Role checks for admin, HR, manager and employee workflows.    
- Application audit events for login, create, upload and approval actions.    
- Parameterized SQLite statements.    
- Sensitive identifiers represented only as masked fields in the schema.    
## Required before production    
- TLS-only cookies with Secure flag, CSRF tokens and session rotation/revocation.    
- MFA; SAML/OIDC SSO for server/cloud editions.    
- Field and backup encryption plus managed secrets.    
- Fine-grained data scope rules, such as HRBP access by business unit.    
- Malware scanning and content-type/size enforcement for uploads.    
- Immutable centralized audit storage and alerting.    
- OWASP ASVS 5 Level 2 verification, SAST, dependency scanning and external penetration testing.    
- India DPDP notice, purpose, retention, correction/access, grievance and breach workflows validated by counsel.    
- Restore drills with measured RPO/RTO.    
This repository is a functional MVP, not a compliance certification.


## v0.6 cloud security controls
- Tenant binding in signed session tokens prevents cross-tenant session replay.
- Control-plane administration is restricted to the local control tenant.
- Tenant data uses separate database files or PostgreSQL schemas.
- SCIM credentials are random, displayed once and stored as SHA-256 hashes.
- SCIM role assignment is allow-listed and cannot provision platform-admin access.
- Tenant switching clears the prior session.
- Cloud cookies use the Secure flag when `PEAWORK_ENV` begins with `cloud`.
- Object-storage keys include the tenant prefix and request server-side encryption.

These controls do not replace production threat modeling, secret-vault integration, network isolation, row/schema authorization testing, federation signature validation, rate limiting, centralized audit or penetration testing.


## v1.0 implemented hardening
- Session-bound CSRF validation for state-changing browser requests.
- Versioned password hashing with legacy upgrade.
- Server-side session registration, expiry and revocation.
- Login throttling and high-severity event creation.
- CSP, anti-framing, MIME, referrer, permissions and cloud HSTS headers.
- Strict cloud cookies and tenant-bound session IDs.
- Upload type, MIME and size allow-lists plus fail-closed malware scan option.
- Startup refusal for default production secrets/bootstrap password.
- Evidenced privacy, retention, breach, accessibility, resilience and release gates.

Independent ASVS review, external penetration testing, organization-approved SAST/dependency scanning, managed secrets, centralized immutable audit, MFA/federation certification and production network controls remain required before certification.

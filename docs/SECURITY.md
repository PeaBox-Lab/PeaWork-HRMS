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

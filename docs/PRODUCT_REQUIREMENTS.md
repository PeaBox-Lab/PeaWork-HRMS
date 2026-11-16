# Product Requirements  
## Initial market  
India-first, 50–2,000 employee organizations. Local laptop/server launch first while preserving a shared application core for SaaS.  
## Personas  
Employee, manager, HR operations, HRBP, recruiter, payroll administrator, auditor and system administrator.  
## v0.1 acceptance criteria  
1. Admin can authenticate and view a dashboard.  
2. HR can create and locate employees.  
3. Every hire creates a dated history event.  
4. HR can create departments and positions.  
5. User can submit leave; authorized role can approve/reject it.  
6. HR can upload and register employee documents.  
7. HR can export employee data to CSV.  
8. Admin can inspect audit events.  
9. API endpoints require a valid session.  
10. Application can run locally and in Docker.  
## Explicit exclusions from v0.1  
Payroll calculations, statutory filings, biometric devices, recruiting, performance, multi-tenancy, mobile apps and production SSO. These are staged in the roadmap rather than represented as complete.

## v0.2 acceptance criteria
1. HR or recruiter can create, open, hold, close and cancel a requisition.
2. HR or recruiter can register a candidate, résumé metadata and source.
3. A candidate can be linked to an open requisition through a unique application.
4. An application can progress through the controlled candidate pipeline.
5. Authorized users can schedule interviews and record ratings, scorecards, feedback and recommendations.
6. HR or recruiter can issue an offer and record acceptance, decline or withdrawal.
7. An accepted offer can generate an onboarding plan from a configurable journey.
8. Generated tasks have due dates, owner roles and tracked statuses.
9. HR or managers can record a probation review and recommendation.
10. HR can convert a candidate to an employee with an effective-dated HIRE event and linked audit history.
11. Requisition, candidate, application and onboarding APIs require a valid authorized session.
12. Existing v0.1 databases receive the v0.2 schema without destructive migration.

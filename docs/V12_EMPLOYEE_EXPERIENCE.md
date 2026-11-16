# v1.2 Employee Experience

## Purpose

PeaWork v1.2 turns the system of record into a daily employee and manager workspace. It reuses existing Core HR, leave, timesheet, journey, offboarding, document, notification and approval records; it does not create a second employee or workflow model.

## Employee self-service

Employees can see current global ID, assignment, entity, location, department, job, manager, documents, requests, tasks and notifications. Changes to phone, personal email, preferred name, emergency contacts and language create approval-gated change requests. HR approval updates the relevant system-of-record field and preserves old/new values, reason, reviewer and decision date.

Employees can create service requests, acknowledge employee documents, mark notifications read and configure email, in-app, push, digest and quiet-hour preferences. Push is a stored preference in this release; production push delivery requires a configured notification provider and device-subscription controls.

## Unified inbox

The inbox indexes existing source transactions rather than copying their business data. It currently includes employee journey tasks, offboarding tasks, leave approvals, timesheet approvals and profile-change approvals. Every item retains source entity type/ID and links to the owning workflow. Dismissal closes the inbox item only; it does not complete the source transaction.

## Manager workspace

Managers receive a direct-report directory, leave and timesheet approvals, assigned team lifecycle tasks and a calendar combining approved/pending leave with lifecycle events. Existing role and manager relationships determine scope.

## Mobile-ready PWA

The responsive shell includes an install manifest, icon, service worker and offline page. Static shell assets may be cached. Employee data and transactional pages remain network-first, and unsafe HR actions are never queued offline. This prevents stale or unaudited offline changes from later mutating employment records.

## Controls

- Self-service changes require HR/admin approval.
- Field names are allow-listed; arbitrary database columns cannot be changed.
- Document acknowledgement is unique by employee, document and version.
- Inbox records reference source transactions and do not override their controls.
- Manager views are scoped through the employee-manager relationship.
- Existing CSRF, revocable sessions, tenant isolation, audit and privacy controls remain active.
- Offline mode provides information only and does not accept HR transactions.

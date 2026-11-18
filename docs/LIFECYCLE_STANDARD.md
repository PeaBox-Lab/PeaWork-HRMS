# Lifecycle & core HR processes: market standard

**Status:** design note, 2026-11. Supersedes the single "change request" model
as the *user-facing* structure. Written after reviewing how Workday, SAP
SuccessFactors, Oracle Fusion HCM, BambooHR, Personio and HiBob structure the
same work.

The current implementation puts onboarding, promotions, transfers,
resignations, probation and contract changes into one `change_requests` table
with a flat `change_type` enum. That is **not** how global systems present these
processes, and HR teams will not recognise it. The table itself is a reasonable
*transaction engine*; the problem is the module boundaries, the missing
process steps (checklists/journeys) and the missing event/reason taxonomy.

---

## 1. How the market actually splits the work

| Vendor | Job changes | Onboarding / offboarding | Probation | Contracts |
| --- | --- | --- | --- | --- |
| **Workday** | One **Change Job** business process with *change job types*: Data Change, Demotion, Lateral Move, Promotion, Transfer, Swap Positions. A **reason** is mandatory and is mapped to a type. Effective-dated; routes current/proposed manager and HR partner. | **Onboarding** and **Offboarding** business processes, driven by checklists/tasks and journeys. Internal moves can trigger **crossboarding**. | Probation is an event on the worker; completion is its own business process step. | Contract changes are a Change Job reason / contingent-worker process with end dates. |
| **SAP SuccessFactors** | **Change Job and Compensation Info** with a mandatory **Event** and **Event Reason**. Delivered events include Promotion, Transfer, Demotion, Data Change, Position Change, Pay Rate Change, Probation, Completion of Probation, Termination, Rehire. | **Onboarding** module (preboarding, crossboarding) and **Offboarding** module with tasks, participants and activity tracking. | `Probation` and `Completion of Probation` events with event reasons. | Contract data lives in Employment Details; contract change is a job/employment event. |
| **Oracle Fusion HCM** | **Separate actions**: Promote, Transfer, Change Manager, Change Location, Change Working Hours, Terminate Work Relationship. Each has Action + Action Reason and predefined approvals by first- and second-level managers. | **Journeys**: task collections with performers (manager, HR, IT, worker) assigned on hire, transfer, termination. Offboarding/termination journeys. | Probation period captured at hire, reviewed before confirmation. | Contract extension/update as its own action. |
| **BambooHR** | Promotion/transfer recorded on the job tab; termination is a distinct "End employment" action. | **Onboarding** and **Offboarding tabs** with task-list templates, task owners (manager, HR, IT, employee), due dates relative to hire/termination date, e-sign and document collection. | Tracked via status/reports; confirmation is an HR task. | Contract fields on the employee record. |
| **Personio** | **Lifecycle Events**: a guided promotion/role-change flow with current vs proposed values side by side, effective date, compensation, and approvals attached. | **Onboarding/Offboarding workflows**: reusable steps, responsibility groups, templates, reminders, central progress dashboard. | Probation end date + reminder/confirmation tasks. | Contract data + document workflows. |
| **HiBob** | Job change / promotion flows with approvals and an effective date. | **Onboarding flows** (preboarding, tasks for manager/IT/peers) and a dedicated **Offboarding** product: separate flows for resignation, termination and fixed-term end; equipment/access/payroll tasks. | Probation tracking with reminders. | Contract lifecycle + document generation. |

### The seven things every one of them gets right

1. **Job changes are one transaction with typed events and mandatory reasons.**
   Promotion, transfer, demotion, lateral move, manager change, location change
   and data correction are *types* of a job change — not unrelated records.
   Workday maps reasons to types; SuccessFactors makes Event + Event Reason
   mandatory; Oracle exposes each action separately but with Action Reason.
2. **Onboarding and offboarding are process engines, not HR forms.** They are
   templates of tasks with an owner, a due date relative to the start/last day,
   and a completion state. Preboarding (before day one) and crossboarding
   (internal move) are first-class.
3. **Offboarding is its own module.** Exit type (resignation, termination, end
   of fixed term, retirement), notice period, last working day, clearance,
   equipment return, access removal, final pay, exit interview, rehire
   eligibility.
4. **Probation is a tracked event with a deadline and an outcome** (confirmed,
   extended, terminated) — not a note on a change request.
5. **Every change is effective-dated and written to an immutable job history**
   with its event/reason. Approving early does not apply the change early.
6. **Approvals are routed, multi-step and role-based** (current manager, proposed
   manager, HR partner, second-level). They are attached to the transaction, not
   chased by email.
7. **The employee timeline is the record of truth.** Each event shows what
   changed, why, who approved it and when it took effect.

What *not* to copy: Workday's fully configurable business-process engine. It is
powerful but a configuration project. PeaWork should ship opinionated defaults
and stay configurable only where it pays for itself (reason lists, task
templates, approver roles).

---

## 2. Gap analysis: PeaWork v2 today

| Standard capability | Today | Severity |
| --- | --- | --- |
| Separate modules for onboarding, job changes, offboarding, probation | One `/lifecycle` list with a 15-value `change_type` | **High** — the reported problem |
| Event + reason taxonomy | `change_type` only, no reason codes, no type/reason mapping | High |
| Onboarding/offboarding checklists with owners and due dates | None | **High** |
| Preboarding and crossboarding | None | Medium |
| Offboarding case: exit type, notice, last day, clearance, final pay, rehire flag | Only `resignation` as a change type | **High** |
| Probation deadline and outcome | None (no fields on `Employee`) | High |
| Effective dating independent of approval | `effective_date` exists, but approval immediately `complete`s and applies | Medium |
| Current vs proposed values (impact preview) | `old_value`/`new_value` free text; details JSON | Medium |
| Multi-step approvals / delegation | Single HR approve step | Medium |
| Contract end dates and expiry alerts | None | Medium |
| Notifications/SLA reminders for pending tasks | Job queue exists, no task/approval reminders | Medium |
| Compensation coupled to promotion | Separate payroll module, no link | Medium |
| Employee timeline per event | `EmployeeHistory` rows exist, not surfaced per module | Low |

---

## 3. Target module map

```
Core HR
  People, Organisation, Documents, Leave, Attendance          (exists)

Lifecycle  (new landing page /lifecycle)
  ├── Onboarding      /onboarding     journeys + tasks, preboarding, crossboarding
  ├── Job changes     /job-changes    promotion | transfer | demotion | lateral |
  │                                   manager | location | data change
  ├── Offboarding     /offboarding    separations + clearance checklist + exit interview
  ├── Probation       /probation      due list, review, confirm/extend/terminate
  └── Contracts       /contracts      fixed-term end dates, extensions, renewals

Talent
  Review cycles, goals, check-ins, 360 feedback                  (in progress)
```

`/lifecycle` becomes a **landing dashboard**: counts and next actions per module
(onboarding starting this week, job changes awaiting approval, exits in the next
30 days, probations due, contracts expiring). The generic "raise any change
request" form goes away.

### Data model

Keep `change_requests` — rename its role to **job change / contract transaction
engine**. Add:

| Column | Purpose |
| --- | --- |
| `event` | `promotion`, `transfer`, `demotion`, `lateral_move`, `manager_change`, `location_change`, `data_change`, `contract_change`, `termination` |
| `reason_code` | tenant-defined reason, validated against `event` |
| `transaction_date` | when the request was raised/approved |
| `current_snapshot` / `proposed_snapshot` (JSON) | side-by-side impact preview and exact applied values |
| `approval_step` | 1st manager, 2nd manager, HR partner |
| `applied_at` | when the effective-dated change was written to the employee |

New tables:

| Table | Purpose |
| --- | --- |
| `journey_templates` | onboarding / offboarding / crossboarding / probation templates, scoped by department, location, employment type |
| `journey_template_tasks` | task name, owner role (HR/IT/manager/employee), offset days from anchor date, required flag, document requirement |
| `journeys` | one running instance per employee: type, anchor date (start/last day), status, template |
| `journey_tasks` | instance task: assignee (user or role), due date, status, completed_at, evidence/document |
| `separations` | exit type, reason, notice date, last working day, rehire eligible, exit interview, final pay ready |
| `probation_reviews` | due date, reviewer, outcome (`confirmed`/`extended`/`terminated`), new date, notes |
| `contract_events` | contract start/end, extension, renewal, document link |

`ChangeRequest` rows for `resignation` migrate into `separations` +
an offboarding `journey`. Existing promotion/transfer rows map to `event` +
`reason_code = legacy`.

---

## 4. Standard workflows

### 4.1 Onboarding (and preboarding / crossboarding)

**Trigger:** hire created, or a transfer with `crossboard = true`.

```
draft ──► in_progress ──► completed
              │
              └──► cancelled
```

Tasks by owner (defaults, editable per template):

| Owner | Example tasks | Due |
| --- | --- | --- |
| Employee (preboarding) | personal details, bank/tax, emergency contact, policy acknowledgements, photo | -7d |
| HR | contract issued/signed, right-to-work, payroll enrolment, benefits enrolment | -3d |
| IT | account, email, laptop, access groups | -1d |
| Manager | welcome message, buddy assignment, 30/60/90 plan, first-week 1:1s | +1d / +30d |
| Buddy | intro coffee, team tour | +1d |

Rules: template selection by department/location/employment type; overdue tasks
surface on `/onboarding` and the manager dashboard; completion requires all
`required` tasks closed; documents upload to the employee record.

### 4.2 Job change

```
draft ─► submitted ─► approved ─► applied
            │            │
            │            └─► cancelled / rescinded
            └─► rejected
```

- **Event is mandatory; reason is mandatory and must belong to the event.**
- Proposed values are captured as a snapshot, not free text. The form shows
  *current → proposed* side by side (Personio-style) before submit.
- Approval chain (default): current manager → proposed manager (for transfers) →
  HR partner. HR/admin can always act. Approvers can delegate.
- `applied` happens only on/after `effective_date` (a scheduled job applies
  due transactions; early approval does not mutate the employee record).
- Promotion may carry a compensation change, but it is a **separate
  permissioned sub-object** (promotion with or without pay change).
- Every applied change writes `EmployeeHistory` with event + reason + approver.

### 4.3 Offboarding

```
initiated ─► notice ─► clearance ─► final_pay ─► exited
                 │
                 └─► withdrawn
```

- Exit type: resignation, termination (with/without cause), end of fixed term,
  retirement, mutual agreement.
- Capture notice date, last working day, reason; auto-create the offboarding
  journey with due dates relative to the last day.
- Clearance checklist: handover document, equipment (laptop, phone, badge),
  access revocation, knowledge transfer, manager sign-off.
- Final pay readiness gates `exited` (unused leave, payroll hand-off).
- Exit interview recorded; `rehire_eligible` flag for future hiring.
- On `exited`: user sessions revoked, employee status set, directory visibility
  rules applied. Rehire starts a new employee record linked to the old one.

### 4.4 Probation

```
scheduled ─► review_due ─► confirmed | extended | terminated
```

- Due date derived from hire date + probation months (default 3/6, per
  employment type).
- Manager submits recommendation, HR confirms.
- Extension sets a new date and re-schedules; termination routes to offboarding.

### 4.5 Contracts

- Fixed-term contracts carry `end_date`; alerts at 90/60/30 days.
- Extension/renewal is a contract event with approval and a new end date.
- Conversion to permanent is a job/contract change with an event reason.

---

## 5. Delivery plan

| Phase | Scope | Why first |
| --- | --- | --- |
| **A** | Journey engine + onboarding + offboarding + `/lifecycle` landing. Split nav. | Biggest gap vs market; immediately recognisable to HR; reuses the job queue for reminders. |
| **B** | Job-change module: event/reason taxonomy, current-vs-proposed form, multi-step approvals, effective-date application via scheduler, `/job-changes` queue. Retire the generic change-request form. | Fixes the reported "not market standard" problem at its core. |
| **C** | Probation + contracts + reminder notifications + employee timeline per module. | Completes the lifecycle surface. |
| **D** | Crossboarding trigger from transfer, compensation sub-object on promotion, exit-interview analytics, rehire linking. | Depth and automation. |

Migration is additive: keep `change_requests` as the transaction engine, add
`event`/`reason_code`/snapshots, backfill `resignation` rows into `separations`,
and redirect the old `/lifecycle` list to `/job-changes` once phase B lands.

---

## 6. Sources

- Workday: *Steps: Set Up Job Changes*; *Set Up Reasons for Job Changes*;
  *Concept: Worker Transfers*; *Change Job Configurations* —
  `doc.workday.com` (Staffing / Change Job).
- SAP SuccessFactors: *Events in Employee Central*; *Event Reasons in Employee
  Central*; *Changing Jobs*; *Offboarding Process Overview*; *Defining the
  Internal Hire Process* — `help.sap.com`, `learning.sap.com`.
- Oracle Fusion HCM: *Predefined Approval Rules for Oracle Fusion Global Human
  Resources Tasks*; *How You Define Approvals for Human Capital Management*;
  *Implementing and Using Journeys* — `docs.oracle.com`.
- BambooHR: *Set Up Onboarding and Offboarding*; *Activating the Onboarding
  Process*; *Onboarding and Offboarding Sample Templates* — `help.bamboohr.com`.
- Personio: *Create onboarding and offboarding workflows*; *Lifecycle Events:
  Promotions and Role Changes in One Flow* — `support.personio.de`,
  `personio.com`.
- HiBob: *Onboarding flows*; *Offboarding* — `hibob.com`.

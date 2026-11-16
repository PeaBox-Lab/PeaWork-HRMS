# v0.4 Talent

## End-to-end workflow

1. HR creates a performance cycle with goal, self-review and manager-review deadlines.
2. HR adds employees and managers as cycle participants and activates the cycle.
3. Employees and managers create weighted goals or OKRs, optionally aligned to parent goals.
4. Goal owners post measurable progress, current values, evidence and status changes.
5. Employees and managers record check-ins covering accomplishments, blockers, support, next steps and sentiment.
6. Employees, managers or HR request 360 feedback from peers, managers, direct reports or stakeholders.
7. Feedback providers submit strengths, improvement opportunities, examples, ratings and a visibility choice.
8. Employees and managers assess competencies and skills; managers can verify skills with evidence.
9. Employees and managers create development plans and track learning, experience, exposure, coaching and certification actions.
10. Employees submit self-reviews, managers submit manager reviews, and HR runs calibration sessions.
11. Calibration records proposed and adjusted ratings, performance/potential buckets, rationale and risk flags.
12. HR finalizes the participant review after calibration.

## Ratings and levels

- Review and feedback ratings use a 1–5 scale and support one decimal place.
- Competency and skill proficiency levels use integers from 1 to 5.
- Goal progress is constrained to 0–100 percent.
- Rating labels are intentionally configurable in future work; v0.4 stores numeric evidence without claiming a universal meaning for each number.

## Feedback visibility

- `Manager Only`: visible to HR/admin, the feedback author and the subject’s manager; not shown to the subject.
- `Subject and Manager`: visible to the subject, author, subject manager and HR/admin.
- `Anonymous Summary`: visible to the same permitted audience, but the author is displayed as Anonymous to protect identity in the application view.

This is application-level visibility, not a substitute for production-grade fine-grained authorization, database row-level security or minimum-response anonymity thresholds.

## Roles

- `admin`, `hr`: configure cycles and capability libraries, manage participants, view permitted feedback, run calibration and finalize reviews.
- `manager`: manage goals, check-ins, manager reviews, skills/competencies and development for the available population; participate in feedback and calibration evidence gathering.
- `employee`: maintain own goals, check-ins, self-review, feedback requests/responses, skills, competencies and development plans.

## Migration and audit

The v0.4 schema is added with idempotent `CREATE TABLE IF NOT EXISTS` statements. Existing v0.3 people, recruitment, onboarding and workforce data remain intact. Material cycle, goal, feedback, capability, development and calibration actions write to the shared audit log.

## Production boundaries

Before production, implement organization-scoped authorization, configurable rating scales, feedback anonymity thresholds, moderation and appeal workflows, legal/employee-relations review, retention rules, export controls, notification delivery, immutable review snapshots and bias/adverse-impact monitoring. Calibration output is decision support; it must not be treated as an automated employment decision.

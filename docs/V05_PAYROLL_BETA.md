# v0.5 India payroll beta

## Scope and validation boundary

This release provides payroll workflow and calculation infrastructure: salary structures, recurring earnings and deductions, attendance-linked proration, approved variable inputs, arrears, reimbursements, payroll previews, variance controls, payslips and balanced accounting export.

It does **not** claim statutory filing readiness. No statutory rates, thresholds, wage ceilings or tax slabs are hard-coded as authoritative. A statutory component may calculate only when an effective `statutory_rule_versions` record is marked `Validated` and names the qualified specialist who validated it. All statutory rules, payroll results, payslips, journal mappings and filings require review by qualified payroll, tax and finance professionals.

## Workflow

1. Payroll administrators maintain earning, deduction, reimbursement and employer-cost components.
2. Every component receives an expense/asset or liability GL mapping before finalization.
3. HR/payroll creates salary structures using fixed amounts, percent of monthly CTC, percent of another component, or approved input values.
4. Employees receive effective-dated compensation assignments.
5. Payroll creates a period with start/end dates, pay date and working days.
6. Attendance synchronization derives payable days, loss-of-pay days and overtime from Workforce records. When the period has no attendance records for an employee, the beta defaults payable days to the period’s configured working days and surfaces the source in the payroll input table.
7. Authorized users enter and approve variable pay, arrears, recoveries and adjustments.
8. Employees submit reimbursement claims; payroll/HR approves or rejects them.
9. A payroll preview calculates recurring pay, proration, approved inputs and reimbursements, and compares net pay with the latest finalized prior period.
10. Finalization blocks when a statutory rule is unvalidated, a payroll line lacks required accounting mapping, calculation errors exist, or net-pay variance exceeds the selected review threshold.
11. Finalization publishes employee payslips and closes the payroll period.
12. Accounting export produces debit and credit rows and refuses delivery unless total debits equal total credits.

## Calculation methods

- `Fixed`: monthly configured amount.
- `Percent CTC`: annual CTC divided by 12, multiplied by the configured rate.
- `Percent Component`: percentage of a previously calculated component in the same structure.
- `Input`: amount comes only from an approved payroll input.
- Recurring earnings and employer costs are multiplied by payable days / period working days.
- Reimbursements are non-taxable in the beta only when the component is configured that way; the organization remains responsible for determining actual tax treatment.
- Currency amounts use decimal half-up rounding to two decimal places.

## Variance and accounting controls

- Employee preview shows previous finalized net pay, amount variance and percentage variance.
- Finalization uses a user-selected percentage threshold and blocks unexplained exceptions above it.
- Earnings and reimbursements debit mapped expense/asset accounts.
- Deductions credit mapped liability accounts.
- Employer costs create both expense debits and liability credits.
- Net pay credits the configured `payroll_net_pay_liability_account`, or a visible placeholder if not configured.
- Export is created only if debits equal credits within ₹0.01.

## Specialist validation and golden cases

Before production, a payroll specialist must validate every statutory rule version and approve golden payroll cases covering at least: full month, mid-month joiner, unpaid leave, arrear, reimbursement, deduction recovery, statutory ceiling, threshold crossing, rounding boundary, retroactive change and termination. Filing, payment, Form 16, TDS returns, PF/ESI returns, professional-tax returns and state-specific registrations are outside this beta.

The release includes `payroll_golden_cases.csv` for repeatable payroll-mechanics checks and `statutory_rule_template.json` for controlled statutory-rule intake. The statutory rows intentionally remain pending until a qualified specialist supplies and validates the effective parameters.

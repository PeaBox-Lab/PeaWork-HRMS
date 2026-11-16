# v0.3 Workforce

## Windows installation and launch

- `install.bat` detects Python, creates `.venv`, upgrades pip, installs pinned requirements, pauses on failure, and reports a clear recovery action.
- On success it pauses before launch, opens `start_peawork.bat` in a persistent server window, waits briefly, and opens `http://127.0.0.1:8080` in the default browser.
- `start_peawork.bat` is the normal daily launcher and does not reinstall libraries.
- The server window must remain open while PeaWork is in use. `Ctrl+C` stops the local server.

## Workforce workflow

1. HR defines shifts and weekly-off patterns.
2. HR assigns an effective-dated shift and weekly-off pattern to each employee.
3. Employees or authorized operations users record daily attendance. Worked and overtime hours are calculated from punches and shift break/minimum-hour rules.
4. Employees request overtime; HR or managers approve or reject it.
5. Employees create weekly timesheets, add dated project/activity entries, submit them, and correct rejected sheets.
6. HR or managers approve or reject submitted timesheets.
7. HR registers biometric devices and imports CSV records. Imports upsert by employee and date and preserve row-level rejection details.
8. Authorized systems can send authenticated biometric punches through the API.
9. HR runs one leave-accrual job per leave type and month. The job updates every active employee’s annual balance in one transaction and blocks duplicate runs.
10. Managers review attendance and leave events together in the monthly workforce calendar.

## Biometric CSV format

Required headers:

```text
employee_no,attendance_date,check_in,check_out
```

Optional header: `status`.

Punch values may be full ISO date-times such as `2026-11-16T09:30`, or `HH:MM` values such as `09:30`; time-only values are combined with `attendance_date`.

## Roles

- `admin`, `hr`: configure shifts, weekly offs, patterns, devices, imports and accrual jobs; manage all workforce workflows.
- `manager`: view workforce operations, approve overtime and timesheets, and use the manager calendar.
- `employee`: record and view own attendance, request own overtime, and maintain own timesheets.

## Production boundaries

Biometric integrations in this release are authenticated application endpoints and CSV ingestion, not device-vendor certification. Before production, add device signatures or service credentials, replay protection, source IP policy, encrypted transport, clock-drift monitoring, exception alerts, attendance policy validation and DPDP-reviewed retention/access controls.

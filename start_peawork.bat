@echo off
setlocal
cd /d "%~dp0"
title PeaWork Server
if not exist ".venv\\Scripts\\python.exe" (
  echo PeaWork is not installed yet. Run install.bat first.
  pause
  exit /b 1
)

REM --- Free port 8090: stop any stale listener (legacy HRMS often holds it) ---
powershell -NoProfile -Command "$con = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if ($con) { Write-Host ('Stopping stale process on port 8090 (PID ' + $con.OwningProcess + ')...'); Stop-Process -Id $con.OwningProcess -Force -ErrorAction SilentlyContinue } else { Write-Host 'Port 8090 is free.' }"

echo Starting PeaWork v2 at http://127.0.0.1:8090
echo Press Ctrl+C to stop the server.
echo.
".venv\\Scripts\\python.exe" -m uvicorn peawork.main:app --host 127.0.0.1 --port 8090
if errorlevel 1 (
  echo.
  echo PeaWork stopped because of an error.
  pause
)
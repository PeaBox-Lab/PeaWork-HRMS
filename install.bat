@echo off
setlocal
cd /d "%~dp0"
title PeaWork Setup
color 0A

echo ============================================================
echo                 PeaWork Windows Setup
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON=py"
) else (
  where python >nul 2>nul
  if errorlevel 1 goto :no_python
  set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating the private Python environment...
  %PYTHON% -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo [1/3] Existing Python environment found.
)

echo [2/3] Installing or updating required libraries...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo [3/3] Setup completed successfully.
echo.
echo PeaWork will now start at http://127.0.0.1:8080
echo Keep the server window open while using the application.
echo.
pause
start "PeaWork Server" "%~dp0start_peawork.bat"
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8080'"
exit /b 0

:no_python
color 0C
echo ERROR: Python was not found.
echo Install Python 3.11 or newer from python.org and enable
echo "Add Python to PATH", then run install.bat again.
goto :hold

:failed
color 0C
echo.
echo ERROR: PeaWork setup did not complete.
echo Review the message above, correct the problem, and run install.bat again.

:hold
echo.
pause
exit /b 1

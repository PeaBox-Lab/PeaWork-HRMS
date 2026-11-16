@echo off  
setlocal  
cd /d "%~dp0"  
title PeaWork Server  
if not exist ".venv\\Scripts\\python.exe" (  
  echo PeaWork is not installed yet. Run install.bat first.  
  pause  
  exit /b 1  
)  
echo Starting PeaWork at http://127.0.0.1:8090  
echo Press Ctrl+C to stop the server.  
echo.  
".venv\\Scripts\\python.exe" run.py  
if errorlevel 1 (  
  echo.  
  echo PeaWork stopped because of an error.  
  pause  
)

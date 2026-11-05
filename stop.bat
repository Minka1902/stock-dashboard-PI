@echo off
REM Stop the dashboard servers started by start.bat / start.ps1
REM (frees ports 8000 and 5173, supervisor processes included).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Stop
if errorlevel 1 pause

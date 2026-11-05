@echo off
REM Double-clickable wrapper around start.ps1 (bypasses execution policy for
REM this one run only). Everything is passed through, so these all work:
REM     start.bat              dev  -> api :8000 (reload) + vite :5173
REM     start.bat prod         single port -> build, then serve on :8000
REM     start.bat -Stop        shut the dashboard down
REM     start.bat -Status      what's running, plus a log tail
REM     start.bat -Logs        follow the logs
REM     start.bat -Install     create the venv / install deps if missing
REM     start.bat -Kill        stop whatever already holds the ports
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause

@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -B -X utf8 -m petlab_app --setup
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0quickstart.ps1" -Setup
)
if errorlevel 1 pause

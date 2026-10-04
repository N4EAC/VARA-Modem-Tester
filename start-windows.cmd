@echo off
cd /d "%~dp0"
.venv\Scripts\python vara_tester.py
if errorlevel 1 pause

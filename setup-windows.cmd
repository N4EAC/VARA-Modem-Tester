@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Setup complete. Run start-windows.cmd.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.12 64-bit from python.org, then retry.
pause
exit /b 1

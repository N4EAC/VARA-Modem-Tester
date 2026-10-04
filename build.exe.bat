@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo VARA Contact Lab - Windows executable build
echo Detecting a compatible 64-bit Python installation...
set "TESTER_PY="
for %%V in (3.12 3.11 3.13) do call :probe %%V
if not defined TESTER_PY (
  echo Python 3.12, 3.11 or 3.13 64-bit with the py launcher is required.
  echo Install Python 3.12 from https://www.python.org/downloads/windows/
  goto fail
)
echo Selected Python %TESTER_PY%
if exist ".build-venv\Scripts\python.exe" (
  .build-venv\Scripts\python.exe -c "import sys,struct; assert sys.version_info[:2] == tuple(map(int,'%TESTER_PY%'.split('.'))); assert struct.calcsize('P') == 8" >nul 2>nul
  if errorlevel 1 (
    echo Existing .build-venv does not match the selected Python.
    echo Remove or rename .build-venv, then run this script again.
    goto fail
  )
) else (
  py -%TESTER_PY% -m venv .build-venv
  if errorlevel 1 goto fail
)
set "TESTER_BUILD_PY=.build-venv\Scripts\python.exe"
"%TESTER_BUILD_PY%" -m pip install --upgrade pip
if errorlevel 1 goto fail
"%TESTER_BUILD_PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail
"%TESTER_BUILD_PY%" -m pip check
if errorlevel 1 goto fail
"%TESTER_BUILD_PY%" -c "import tkinter, pyaudiowpatch, psutil, PyInstaller; import struct; assert struct.calcsize('P') == 8"
if errorlevel 1 goto fail
"%TESTER_BUILD_PY%" -m unittest discover -s tests -v
if errorlevel 1 goto fail
"%TESTER_BUILD_PY%" -m PyInstaller --clean --noconfirm --onefile --windowed --name VARA-Modem-Tester --collect-all pyaudiowpatch --hidden-import psutil vara_tester.py
if errorlevel 1 goto fail
if not exist "dist\VARA-Modem-Tester.exe" goto fail
copy /y WINDOWS_STEP_BY_STEP.md dist\WINDOWS_STEP_BY_STEP.md >nul
if errorlevel 1 goto fail
copy /y VARA_HF_REFERENCE_RATES.csv dist\VARA_HF_REFERENCE_RATES.csv >nul
if errorlevel 1 goto fail
echo.
echo Built: %CD%\dist\VARA-Modem-Tester.exe
echo Instructions and reference rates copied to dist.
echo Original VARA executables and virtual audio drivers must be installed separately.
pause
exit /b 0
:probe
if defined TESTER_PY exit /b 0
py -%1 -c "import struct, tkinter; assert struct.calcsize('P') == 8" >nul 2>nul
if not errorlevel 1 set "TESTER_PY=%1"
exit /b 0
:fail
echo.
echo Build failed. Read the error above. Internet access is required for dependencies.
pause
exit /b 1

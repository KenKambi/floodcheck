@echo off
rem FloodCheck - one-click start for Windows.
rem First run: creates a private Python environment (.venv) and installs the packages (a few minutes).
rem Later runs: starts straight away. Then open http://localhost:8000 in your browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Setting up FloodCheck for the first time...
  set "PYCMD="
  for %%V in (3.12 3.11 3.10) do (
    if not defined PYCMD (
      py -%%V -c "import sys" >nul 2>&1 && set "PYCMD=py -%%V"
    )
  )
  if not defined PYCMD (
    echo.
    echo Python 3.10, 3.11 or 3.12 was not found.
    echo Install Python 3.11 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
    echo then run this file again.
    pause
    exit /b 1
  )
  call %%PYCMD%% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
)

".venv\Scripts\python.exe" run.py %*
pause

@echo off
rem One-click start for Windows: creates a virtual environment, installs packages, runs the app.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv .venv || (pause & exit /b 1)
)
if not exist .env copy .env.example .env >nul
echo Installing packages (first run takes a minute)...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || (pause & exit /b 1)
.venv\Scripts\python.exe -m travel_planner serve --open
pause

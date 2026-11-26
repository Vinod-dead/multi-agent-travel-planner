#!/usr/bin/env bash
# One-command start for macOS / Linux: creates a virtual environment, installs packages, runs the app.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Python 3.10+ is required (https://www.python.org/downloads/)"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
[ -f .env ] || cp .env.example .env
echo "Installing packages (first run takes a minute)..."
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python -m travel_planner serve --open

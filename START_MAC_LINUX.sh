#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Spotify Library Manager"
echo "======================="
echo "Installing Python packages from requirements.txt if needed."
echo "Keep this terminal open while the app is running."
echo

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 app.py

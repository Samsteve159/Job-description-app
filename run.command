#!/bin/bash
# Double-click this to start the app. It opens in your normal browser.
cd "$(dirname "$0")" || exit 1

if ! python3 -c "import fastapi, uvicorn, docx, sqlalchemy" 2>/dev/null; then
  echo "Installing dependencies, one moment..."
  python3 -m pip install --quiet -r requirements.txt || {
    echo "Install failed. Run this yourself to see why:"
    echo "  python3 -m pip install -r requirements.txt"
    read -r -p "Press return to close."
    exit 1
  }
fi

python3 main.py
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "The app stopped with an error (exit $status)."
  read -r -p "Press return to close."
fi

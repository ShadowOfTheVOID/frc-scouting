#!/bin/bash
# Double-click this to start the scouting server.
# Leave the window open — closing it stops the server.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "  Python 3 is not installed."
  echo
  echo "  Get it from https://www.python.org/downloads/ and run this again."
  echo
  read -n 1 -s -r -p "  Press any key to close..."
  exit 1
fi

python3 -u server/hub.py "$@"
code=$?
if [ $code -ne 0 ]; then
  echo
  echo "  The server stopped unexpectedly (exit $code). The message above says why."
  echo
  read -n 1 -s -r -p "  Press any key to close..."
fi

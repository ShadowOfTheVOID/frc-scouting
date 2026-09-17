#!/bin/bash
# Double-click this to get a Lovat API key.
# Copy the `profile` request in your browser first - the window will say how.
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

python3 -u server/lovat_key.py "$@"
echo
read -n 1 -s -r -p "  Press any key to close..."
echo

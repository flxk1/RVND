#!/bin/bash
# Double-click this in Finder to open the Rvnd app (the Loom canvas).
# It starts the local server and opens your browser. Close the window to stop.
cd "$(dirname "$0")/.." || exit 1
echo "Starting Rvnd…  (close this window to stop the app)"
exec python3 app/serve.py

#!/bin/bash
# Double-click this in Finder to start Rvnd. It sets everything up the first
# time (creates the virtual environment and installs dependencies), then starts
# the local console and opens your browser. No terminal knowledge needed.
#
#   • First run may take a few minutes (it downloads dependencies).
#   • To STOP Rvnd: close this Terminal window (or press Ctrl-C in it).
#
# Everything runs on your own machine, on http://127.0.0.1 only.

set -euo pipefail
cd "$(dirname "$0")/.." || { echo "Could not find the Rvnd folder."; read -r; exit 1; }

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
die() { printf '\n\033[1;31m%s\033[0m\n\n' "$1"; echo "Press Return to close."; read -r; exit 1; }

# 1. Python present?
if ! command -v python3 >/dev/null 2>&1; then
  die "Python 3 isn't installed. Install it from https://www.python.org/downloads/ (get 3.10 or newer), then double-click this again."
fi

# 2. First run: create the virtual environment + install dependencies.
if [ ! -x ".venv/bin/python" ]; then
  say "First-time setup — creating the environment and installing Rvnd (this can take a few minutes)…"
  if [ -x "./server/install.sh" ]; then
    ./server/install.sh || die "Setup failed. See the messages above; re-running usually fixes a network hiccup."
  else
    python3 -m venv .venv || die "Could not create the virtual environment."
    .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
    .venv/bin/python -m pip install -e . || die "Could not install dependencies. Re-run to retry (often just a network hiccup)."
  fi
fi

PY=".venv/bin/python"
[ -x "$PY" ] || die "The environment looks incomplete. Delete the .venv folder and double-click this again to reinstall."

# 3. Pick the first free port from 8799 upward, so a leftover Rvnd never blocks
#    startup with a cryptic 'address already in use'.
PORT="$("$PY" - <<'PYEOF'
import socket
for p in range(8799, 8899):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); print(p); break
    except OSError:
        pass
    finally:
        s.close()
else:
    print(8799)
PYEOF
)"

# 4. Launch. serve.py opens the browser and prints the URL; it runs in this
#    window, so closing the window stops Rvnd.
say "Starting Rvnd on http://127.0.0.1:${PORT}  —  close this window to stop."
exec "$PY" app/serve.py "$PORT"

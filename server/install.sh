#!/usr/bin/env bash
# Workspace — one-command install for users.
#
#   ./server/install.sh          (runnable from anywhere; it cd's to the repo root)
#
# Creates an isolated virtual environment, installs Workspace + all dependencies
# into it, and verifies the install by running the oversight demo. Safe to
# re-run (idempotent). No global Python pollution, no PYTHONPATH, no manual
# dependency hunting.
#
# The build config is a single pyproject.toml at the REPO ROOT — `server/` holds
# no setup.py or pyproject.toml, so pip must be pointed at the root, not here.
# The venv is the root `.venv/`, the same one the README and the Makefile use.
#
# After it finishes, activate the environment with:
#   source .venv/bin/activate
# …then `workspaces`, `pytest`, and `import workspaces` just work.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

# 1. Pick a Python. Prefer 3.12 (most-tested), fall back to whatever python3 is.
PY=""
for cand in python3.12 python3.13 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "error: no python3 found on PATH. Install Python 3.10+ and re-run." >&2
  exit 1
fi
echo "› using $($PY --version 2>&1) at $(command -v "$PY")"

# 2. Create the venv if missing.
if [ ! -d .venv ]; then
  echo "› creating virtual environment in .venv/"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Install Workspace + deps into the venv (editable so source edits stay live).
#    `mcp` needs no extra — it is a required runtime dependency in pyproject.toml.
#    This also pulls the pinned Loomground upstreams over git+https, so the first
#    run needs network and a working `git`.
echo "› installing workspace + dependencies (this pulls mcp, cryptography, anyascii…)"
python -m pip install --upgrade pip >/dev/null
python -m pip install -e ".[test]"

# 4. Verify: import works without PYTHONPATH, and the demo runs.
echo "› verifying install"
python -c "import workspaces; assert hasattr(workspaces,'assess'); print('  workspaces importable:', len(workspaces.__all__), 'exports')"
if [ -f server/examples/oversight_demo.py ]; then
  echo "› running the oversight demo (proof the engine works end-to-end):"
  echo "  ----------------------------------------------------------------"
  python server/examples/oversight_demo.py | sed 's/^/  /'
  echo "  ----------------------------------------------------------------"
fi

cat <<'DONE'

✓ Workspace installed into the repo-root .venv/.

  To use it in this shell now:        source .venv/bin/activate
  Then, from the repo root:           workspaces --help
                                      pytest server/tests -q
                                      python server/examples/oversight_demo.py
                                      python app/serve.py     # → http://127.0.0.1:8799

  The virtual environment keeps Workspace's dependencies isolated from the rest of
  your system. Re-run ./server/install.sh any time to update.
DONE

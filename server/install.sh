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
# CI and advanced users may select an interpreter explicitly without changing
# the ordinary one-command install.
PY="${RVND_INSTALL_PYTHON:-}"
if [ -n "$PY" ] && ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: RVND_INSTALL_PYTHON=$PY is not available on PATH." >&2
  exit 1
fi
if [ -z "$PY" ]; then
  for cand in python3.12 python3.13 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
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
# pip caches built wheels by (name, VERSION), not by git commit. RVND pins each
# Loomground plane to an exact commit, but if this machine holds a stale cached
# wheel of a version that the pinned commit also declares, pip reuses that stale
# wheel and installs the WRONG build — the failure that could break a fresh
# install (a stale loomground_solver-0.2.0 lacking ESCALATE). Dropping the plane
# wheels forces pip to rebuild every git pin from its pinned commit, so the
# collision cannot occur. Third-party wheels stay cached for speed.
python -m pip cache remove 'loomground_*' >/dev/null 2>&1 || true
python -m pip install -e ".[test]"

# 4. Verify: the consumed planes installed with their REAL surface, import works
#    without PYTHONPATH, and the demo runs.
echo "› verifying install"
# Prove each consumed plane installed with its real surface BEFORE importing
# RVND. A stale or wrong plane wheel (a pip version-cache collision) is caught
# here with a one-line fix, instead of a cryptic downstream ImportError — or,
# worse, an engine that imports but is hollow. These are the exact symbols a
# fresh install must expose (the ones a stale wheel was missing).
python - <<'PYCHECK'
import importlib, sys
REQUIRED = {
    "loomground_solver": ("ESCALATE", "Dimension", "RelationAlgebra"),
    "loomground_ingest": ("Subgraph", "versum_writer", "RequiredArtifact", "EnrichingWriter"),
    "deontic": ("classify_incident",),
    "versum": ("DimensionedSubgraphSink", "load_dimensioned_subgraphs"),
    "loomground_legal": ("connection",),
    "loomground_vertical": ("SubjectCard", "build_house", "register_court_pack"),
}
missing = []
for mod, syms in REQUIRED.items():
    try:
        m = importlib.import_module(mod)
    except Exception as exc:  # noqa: BLE001
        missing.append("%s: not importable (%s)" % (mod, exc))
        continue
    for s in syms:
        if not hasattr(m, s):
            missing.append("%s.%s is missing" % (mod, s))
if missing:
    sys.stderr.write("  x consumed-plane surface check FAILED "
                     "— a stale or wrong plane wheel is installed:\n")
    for item in missing:
        sys.stderr.write("      - %s\n" % item)
    sys.stderr.write("    This is a pip wheel-cache version collision. Fix:\n"
                     "      python -m pip cache purge && ./server/install.sh\n")
    sys.exit(1)
print("  consumed-plane surfaces OK "
      "(solver ESCALATE/RelationAlgebra, ingest, deontic, versum, legal, vertical)")
PYCHECK
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

  To drive RVND from an AI agent
  (Claude Code / Codex):              ./scripts/connect-agent-hub.sh

  The virtual environment keeps Workspace's dependencies isolated from the rest of
  your system. Re-run ./server/install.sh any time to update.
DONE

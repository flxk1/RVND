#!/usr/bin/env bash
# install-rvnd.sh — install the RVND governance engine + the full pinned
# Loomground plane stack, straight from public GitHub. No PyPI, no auth.
#
#   Requirements: git, Python >= 3.10, network access to github.com.
#   Usage:
#     ./install-rvnd.sh                      # default ref + ./rvnd-venv
#     RVND_REF=v0.6.9.9 ./install-rvnd.sh    # pin a specific release/tag/sha
#     RVND_VENV=~/.venvs/rvnd ./install-rvnd.sh
#     PYTHON=python3.12 ./install-rvnd.sh
#
# What it installs: RVND + its 15 exact-SHA-pinned Loomground planes
# (solver, versum, governance, deontic, ingest, legal, norm, factual,
# epistemic, vertical, workspace, brief, proxy, enforcement-posture,
# effect-reconciliation). It does NOT install the ctrl orchestration
# plugins (those live in private/local repos) — the engine runs without them.
set -euo pipefail

# --- config (override via env) ---------------------------------------------
RVND_REPO="${RVND_REPO:-https://github.com/flxk1/RVND}"
RVND_REF="${RVND_REF:-v0.6.9.10}"         # pinned release: includes #120 (packaging fix) + #121 (erasure line). Override for another ref/sha.
RVND_VENV="${RVND_VENV:-./rvnd-venv}"
PYTHON="${PYTHON:-}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. preconditions ------------------------------------------------------
info "Checking prerequisites"
command -v git >/dev/null 2>&1 || die "git not found. Install git and retry."

# find a Python >= 3.10
if [ -z "$PYTHON" ]; then
  for c in python3.12 python3.11 python3.10 python3; do
    command -v "$c" >/dev/null 2>&1 && { PYTHON="$c"; break; }
  done
fi
[ -n "$PYTHON" ] || die "No python3 found. Install Python >= 3.10."
"$PYTHON" - <<'PY' || die "Python >= 3.10 required."
import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)
PY
ok "$("$PYTHON" -V) via $PYTHON"

# network + repo reachable (also proves the repo is public / you have access)
git ls-remote "$RVND_REPO" "refs/tags/$RVND_REF" >/dev/null 2>&1 \
  || git ls-remote "$RVND_REPO" >/dev/null 2>&1 \
  || die "Cannot reach $RVND_REPO. Check network / the ref '$RVND_REF'."
ok "reachable: $RVND_REPO @ $RVND_REF"

# --- 2. venv ---------------------------------------------------------------
info "Creating virtualenv at $RVND_VENV"
"$PYTHON" -m venv "$RVND_VENV"
# macOS: the sandbox may set UF_HIDDEN on the venv, which makes Python 3.12
# skip its editable/.pth entries. Clearing it is harmless elsewhere.
if [ "$(uname)" = "Darwin" ]; then chflags -R nohidden "$RVND_VENV" 2>/dev/null || true; fi
VPY="$RVND_VENV/bin/python"
"$VPY" -m pip install --upgrade pip >/dev/null
ok "venv ready"

# --- 3. install (GitHub only; pulls the whole pinned closure) ---------------
info "Installing RVND + pinned Loomground stack (this pulls ~15 repos; a few minutes)"
"$VPY" -m pip install "git+${RVND_REPO#https://}@${RVND_REF}" 2>/dev/null \
  || "$VPY" -m pip install "git+${RVND_REPO}@${RVND_REF}"
if [ "$(uname)" = "Darwin" ]; then chflags -R nohidden "$RVND_VENV" 2>/dev/null || true; fi
ok "pip install complete"

# --- 4. verify (fail loudly if the install isn't actually usable) ----------
info "Verifying the install"
"$VPY" - <<'PY' || die "Verification failed — the install is not usable."
import importlib, sys
# 4a. engine + every plane imports
mods = ["rvnd","versum","loomground_solver","loomground_governance","deontic",
        "loomground_norm","loomground_legal","loomground_ingest","loomground_factual",
        "loomground_epistemic","loomground_vertical","loomground_workspace",
        "loomground_brief","loomground_proxy"]
bad = []
for m in mods:
    try: importlib.import_module(m)
    except Exception as e: bad.append(f"{m}: {e}")
if bad: print("import failures:\n  " + "\n  ".join(bad)); sys.exit(1)
import rvnd; print("  rvnd", rvnd.__version__, "+", len(mods)-1, "planes import OK")
# 4b. data actually shipped in the wheel (jurisdiction packs load from site-packages)
from rvnd import juris_packs
packs = sorted(p.name for p in juris_packs._PACK_DIR.glob("*.json"))
assert "site-packages" in str(juris_packs._PACK_DIR), "packs not from installed pkg"
assert packs, "no jurisdiction packs shipped"
print("  jurisdiction packs shipped:", packs)
PY
ok "engine + planes import; data packs present"

# 4c. CLI entry points work
"$RVND_VENV/bin/workspaces" --version >/dev/null 2>&1 && ok "CLI: $("$RVND_VENV/bin/workspaces" --version)" \
  || info "CLI 'workspaces' not on the venv bin path (library still usable)"

# --- 5. next steps ---------------------------------------------------------
cat <<EOF

$(printf '\033[1;32mRVND installed.\033[0m')  venv: $RVND_VENV  ref: $RVND_REF

Activate it:        source $RVND_VENV/bin/activate
Health check:       $RVND_VENV/bin/workspaces doctor
Run the MCP server: $RVND_VENV/bin/workspaces-mcp        (drive it from an MCP client)
CLI help:           $RVND_VENV/bin/workspaces --help

Pinned release:     defaults to v0.6.9.10 (includes the packaging fix #120 and
                    the erasure line #121). Override RVND_REF for another ref.
Note: this installs the ENGINE only. The ctrl orchestration plugins are not
public and are not part of this install.
EOF

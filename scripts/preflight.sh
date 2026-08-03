#!/bin/sh
# RVND pre-flight — check this machine has what RVND needs BEFORE installing.
#
# Unlike `workspaces doctor` (which needs RVND already installed), this runs on a
# bare machine: it is plain POSIX sh with no dependency on the package. Run it
# yourself after cloning, or let bootstrap.sh call it. It NEVER changes anything.
#
#   sh scripts/preflight.sh [TARGET_DIR]
#
# TARGET_DIR (default: current dir) is only used to check write access + free
# space. Exit 0 = ready to install; exit 1 = a required tool is missing.
#
# Required: git, python (>=3.10). Advisory (warn only): curl, free disk space.

set -eu

TARGET="${1:-.}"
MISSING=0        # required checks that failed
WARN=0           # advisory checks that failed

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; MISSING=$((MISSING + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }

say "RVND pre-flight"
say "========================================"

# --- OS (informational) ---------------------------------------------------
OS="$(uname -s 2>/dev/null || echo unknown)"
case "$OS" in
  Darwin) ok "operating system: macOS" ;;
  Linux)  ok "operating system: Linux" ;;
  *)      warn "operating system: $OS (untested — macOS and Linux are supported)" ;;
esac

# --- git (required) -------------------------------------------------------
if command -v git >/dev/null 2>&1; then
  ok "git: $(git --version 2>/dev/null | head -1)"
else
  bad "git: not found — needed to fetch and update RVND."
  case "$OS" in
    Darwin) say "      fix: run  xcode-select --install   (installs the git command-line tools)" ;;
    Linux)  say "      fix: install via your package manager, e.g.  sudo apt-get install git" ;;
  esac
fi

# --- python >= 3.10 (required) --------------------------------------------
# Prefer the most-tested interpreters, matching server/install.sh's ordering.
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  bad "python: no python3 found — RVND needs Python 3.10 or newer."
  case "$OS" in
    Darwin) say "      fix: install from https://www.python.org/downloads/  or  brew install python@3.12" ;;
    Linux)  say "      fix: install via your package manager, e.g.  sudo apt-get install python3 python3-venv" ;;
  esac
else
  VER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "0.0")"
  MAJ="${VER%%.*}"; MIN="${VER#*.}"
  if [ "$MAJ" = "3" ] && [ "$MIN" -ge 10 ] 2>/dev/null; then
    ok "python: $VER ($(command -v "$PY"))"
    # venv is what install.sh builds on; flag its absence early (Debian splits it out).
    if ! "$PY" -c 'import venv' >/dev/null 2>&1; then
      bad "python venv module: missing — install.sh cannot build its .venv."
      say "      fix: install the venv package, e.g.  sudo apt-get install python3-venv"
    fi
  else
    bad "python: $VER is too old — RVND needs 3.10 or newer."
  fi
fi

# --- curl (advisory here; required by the curl|sh bootstrap itself) --------
if command -v curl >/dev/null 2>&1; then
  ok "curl: present"
else
  warn "curl: not found — fine for a local install; the one-line bootstrap needs it."
fi

# --- target writable + free space (advisory) ------------------------------
if [ -d "$TARGET" ] && [ -w "$TARGET" ]; then
  ok "target writable: $TARGET"
elif [ -d "$TARGET" ]; then
  warn "target not writable: $TARGET"
fi
# Free space: RVND + its venv is well under 1 GB without local models; warn under ~1 GB.
FREE_KB="$(df -Pk "$TARGET" 2>/dev/null | awk 'NR==2{print $4}' || echo "")"
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -lt 1048576 ] 2>/dev/null; then
  warn "low free space: $((FREE_KB / 1024)) MB on $TARGET (local models need more)."
elif [ -n "$FREE_KB" ]; then
  ok "free space: $((FREE_KB / 1048576)) GB"
fi

say "========================================"
if [ "$MISSING" -gt 0 ]; then
  say "NOT READY — $MISSING required check(s) failed. Fix the above, then re-run."
  exit 1
fi
if [ "$WARN" -gt 0 ]; then
  say "READY (with $WARN advisory note(s) above)."
else
  say "READY — all checks passed."
fi
exit 0

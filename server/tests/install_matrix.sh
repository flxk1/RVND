#!/usr/bin/env bash
# install_matrix.sh — local pre-PR validation of the install matrix.
#
# Runs the same smoke sequence as .github/workflows/install-matrix.yml against
# whatever Python + installer combination is on the current host. Intended for
# running BEFORE opening a PR — catches the obvious "it works on my machine"
# regression before CI burns 24 cells finding the same thing.
#
# Works on Linux, macOS, and Windows (Git Bash / msys). Tested under bash 3.2
# (default macOS) and bash 5+ — keep the syntax POSIX-ish, no `mapfile`,
# `${var,,}`, etc.
#
# Usage:
#   ./install_matrix.sh                    # auto-detect installers, fresh mode
#   ./install_matrix.sh --installer pip    # restrict to one installer
#   ./install_matrix.sh --mode upgrade-from-pypi
#   ./install_matrix.sh --python 3.12      # use a specific python from PATH
#   ./install_matrix.sh --runtime ../runtime  # override runtime path
#   ./install_matrix.sh --keep             # keep the venv for inspection
#
# Exit codes:
#   0  every selected cell passed
#   1  at least one cell failed
#   2  bad invocation / missing prerequisite

set -u

# ----- terminal colours --------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

# ----- helpers -----------------------------------------------------------

pass() { printf "%s[PASS]%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
fail() { printf "%s[FAIL]%s %s\n" "$C_RED"   "$C_RESET" "$1"; FAILED=$((FAILED+1)); }
skip() { printf "%s[SKIP]%s %s\n" "$C_YELLOW" "$C_RESET" "$1"; }
info() { printf "%s[..]%s   %s\n" "$C_BLUE"  "$C_RESET" "$1"; }
banner() { printf "\n%s== %s ==%s\n" "$C_BOLD" "$1" "$C_RESET"; }

die() { printf "%sFATAL:%s %s\n" "$C_RED" "$C_RESET" "$1" >&2; exit 2; }

# ----- arg parsing -------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALLER_FILTER=""
MODE="fresh"
PYTHON_BIN="python3"
KEEP_VENV="0"

while [ $# -gt 0 ]; do
  case "$1" in
    --installer)  INSTALLER_FILTER="$2"; shift 2 ;;
    --mode)       MODE="$2"; shift 2 ;;
    --python)     PYTHON_BIN="python$2"; shift 2 ;;
    --runtime)    RUNTIME_DIR="$(cd "$2" && pwd)"; shift 2 ;;
    --keep)       KEEP_VENV="1"; shift ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "$PYTHON_BIN not found on PATH"

case "$MODE" in
  fresh|upgrade-from-pypi) ;;
  *) die "--mode must be fresh|upgrade-from-pypi" ;;
esac

# ----- detect available installers --------------------------------------

ALL_INSTALLERS=""
command -v pip  >/dev/null 2>&1 && ALL_INSTALLERS="$ALL_INSTALLERS pip"
command -v uv   >/dev/null 2>&1 && ALL_INSTALLERS="$ALL_INSTALLERS uv"
command -v pipx >/dev/null 2>&1 && ALL_INSTALLERS="$ALL_INSTALLERS pipx"
# pip is always available via `python -m pip`; force-include it.
case " $ALL_INSTALLERS " in *" pip "*) ;; *) ALL_INSTALLERS="pip $ALL_INSTALLERS" ;; esac

if [ -n "$INSTALLER_FILTER" ]; then
  INSTALLERS="$INSTALLER_FILTER"
else
  INSTALLERS="$ALL_INSTALLERS"
fi

# Skip pipx on Windows (matches the CI exclusion)
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    INSTALLERS=$(echo "$INSTALLERS" | tr ' ' '\n' | grep -v '^pipx$' | tr '\n' ' ')
    ;;
esac

# ----- setup workspace ---------------------------------------------------

WORK_DIR="$(mktemp -d -t workspaces-install-matrix.XXXXXX)"
TEST_FOLDER="$WORK_DIR/test"
mkdir -p "$TEST_FOLDER"

cleanup() {
  if [ "$KEEP_VENV" = "0" ]; then
    rm -rf "$WORK_DIR"
  else
    printf "%sKept workspace at%s %s\n" "$C_YELLOW" "$C_RESET" "$WORK_DIR"
  fi
}
trap cleanup EXIT

banner "Install-matrix local runner"
info "runtime:    $RUNTIME_DIR"
info "python:     $($PYTHON_BIN --version 2>&1)  ($(command -v $PYTHON_BIN))"
info "installers: $INSTALLERS"
info "mode:       $MODE"
info "workspace:  $WORK_DIR"

FAILED=0
TOTAL=0

# ----- per-cell smoke ---------------------------------------------------

run_cell() {
  local installer="$1"
  local cell_dir="$WORK_DIR/$installer"
  local venv_dir="$cell_dir/venv"
  local diag="$cell_dir/diag"
  mkdir -p "$diag"
  TOTAL=$((TOTAL+1))

  banner "Cell: installer=$installer mode=$MODE"

  # Create an isolated venv per cell so installs don't cross-contaminate.
  "$PYTHON_BIN" -m venv "$venv_dir" \
    || { fail "$installer · venv create"; return; }

  # shellcheck disable=SC1090
  if [ -f "$venv_dir/bin/activate" ]; then
    . "$venv_dir/bin/activate"
  elif [ -f "$venv_dir/Scripts/activate" ]; then
    . "$venv_dir/Scripts/activate"   # Windows / Git Bash layout
  else
    fail "$installer · venv activate (no activate script)"; return
  fi

  python -m pip install --quiet --upgrade pip \
    || { fail "$installer · pip upgrade"; deactivate; return; }

  case "$installer:$MODE" in
    pip:fresh)
      python -m pip install --quiet "$RUNTIME_DIR/[mcp]" \
        > "$diag/install.log" 2>&1
      ;;
    uv:fresh)
      uv pip install --quiet "$RUNTIME_DIR/[mcp]" \
        > "$diag/install.log" 2>&1
      ;;
    pipx:fresh)
      # pipx into a per-cell home so we don't pollute the user's pipx state.
      PIPX_HOME="$cell_dir/pipx-home" PIPX_BIN_DIR="$cell_dir/pipx-bin" \
        python -m pipx install --python "$(command -v python)" "$RUNTIME_DIR/[mcp]" --force \
        > "$diag/install.log" 2>&1
      export PATH="$cell_dir/pipx-bin:$PATH"
      ;;
    pip:upgrade-from-pypi)
      python -m pip install --quiet "workspaces<0.6.7" > "$diag/install.log" 2>&1 \
        || python -m pip install --quiet "$RUNTIME_DIR/tests/fixtures/wheels/workspaces-0.6.6-py3-none-any.whl" \
           >> "$diag/install.log" 2>&1
      python -m pip install --quiet --upgrade "$RUNTIME_DIR/[mcp]" \
        >> "$diag/install.log" 2>&1
      ;;
    uv:upgrade-from-pypi)
      uv pip install --quiet "workspaces<0.6.7" > "$diag/install.log" 2>&1 \
        || uv pip install --quiet "$RUNTIME_DIR/tests/fixtures/wheels/workspaces-0.6.6-py3-none-any.whl" \
           >> "$diag/install.log" 2>&1
      uv pip install --quiet --upgrade "$RUNTIME_DIR/[mcp]" \
        >> "$diag/install.log" 2>&1
      ;;
    pipx:upgrade-from-pypi)
      PIPX_HOME="$cell_dir/pipx-home" PIPX_BIN_DIR="$cell_dir/pipx-bin" \
        python -m pipx install "workspaces<0.6.7" --force > "$diag/install.log" 2>&1 \
        || PIPX_HOME="$cell_dir/pipx-home" PIPX_BIN_DIR="$cell_dir/pipx-bin" \
             python -m pipx install "$RUNTIME_DIR/tests/fixtures/wheels/workspaces-0.6.6-py3-none-any.whl" --force \
             >> "$diag/install.log" 2>&1
      PIPX_HOME="$cell_dir/pipx-home" PIPX_BIN_DIR="$cell_dir/pipx-bin" \
        python -m pipx reinstall workspaces --python "$(command -v python)" \
        >> "$diag/install.log" 2>&1
      export PATH="$cell_dir/pipx-bin:$PATH"
      ;;
    *)
      fail "$installer · unknown installer/mode combo"
      deactivate; return
      ;;
  esac

  if [ "$?" -ne 0 ]; then
    fail "$installer · install (see $diag/install.log)"
    deactivate; return
  fi
  pass "$installer · install"

  # ----- check 1: workspaces --help -----
  if workspaces --help > "$diag/help.txt" 2>&1 && grep -q "workspaces" "$diag/help.txt"; then
    pass "$installer · workspaces --help"
  else
    fail "$installer · workspaces --help (see $diag/help.txt)"
  fi

  # ----- check 2: workspaces doctor (B3 — soft) -----
  if workspaces doctor --help >/dev/null 2>&1; then
    if workspaces doctor --json > "$diag/doctor.json" 2>&1; then
      pass "$installer · workspaces doctor"
    else
      fail "$installer · workspaces doctor (see $diag/doctor.json)"
    fi
  else
    skip "$installer · workspaces doctor (B3 not landed yet)"
  fi

  # ----- check 3: workspaces status --json -----
  if workspaces status --folder "$TEST_FOLDER" --json > "$diag/status.json" 2>&1; then
    if python -c "
import json,sys
d=json.load(open('$diag/status.json'))
sys.exit(0 if d.get('ok') is True else 1)
" 2>/dev/null; then
      pass "$installer · workspaces status (ok=true)"
    else
      fail "$installer · workspaces status (ok!=true, see $diag/status.json)"
    fi
  else
    fail "$installer · workspaces status (exit nonzero, see $diag/status.json)"
  fi

  # Snapshot the resolved deps for post-mortem.
  python -m pip freeze > "$diag/freeze.txt" 2>&1 || true

  deactivate
}

for installer in $INSTALLERS; do
  run_cell "$installer"
done

# ----- summary ----------------------------------------------------------

banner "Summary"
if [ "$FAILED" -eq 0 ]; then
  printf "%sAll %d cell(s) passed.%s\n" "$C_GREEN" "$TOTAL" "$C_RESET"
  exit 0
else
  printf "%s%d of %d cell(s) failed.%s\n" "$C_RED" "$FAILED" "$TOTAL" "$C_RESET"
  if [ "$KEEP_VENV" = "0" ]; then
    printf "Re-run with %s--keep%s to inspect the venv + diag bundle.\n" "$C_BOLD" "$C_RESET"
  fi
  exit 1
fi

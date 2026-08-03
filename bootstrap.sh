#!/bin/sh
# RVND one-line bootstrap — from a bare machine to an installed, ready RVND.
#
#   curl -fsSL https://raw.githubusercontent.com/flxk1/RVND/main/bootstrap.sh | sh
#
# Prefer to read before you run (sensible for anything piped into a shell):
#   curl -fsSL https://raw.githubusercontent.com/flxk1/RVND/main/bootstrap.sh -o bootstrap.sh
#   less bootstrap.sh && sh bootstrap.sh
#
# What it does, in order: checks git + curl are present, clones (or updates)
# RVND, runs the full pre-flight (scripts/preflight.sh), installs into an
# isolated .venv (server/install.sh), then runs a health check. It is
# non-interactive and idempotent — safe to re-run; re-running updates in place.
#
# Where it installs (first match wins):
#   $RVND_DIR   →   first argument   →   $HOME/rvnd
#
# It will NOT touch a non-empty directory that isn't already an RVND clone.
# The interactive first-run setup (`workspaces init`) is a separate step it
# prints at the end — a piped bootstrap cannot prompt (stdin is the script).

set -eu

REPO_URL="https://github.com/flxk1/RVND.git"

say()  { printf '%s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

main() {
  case "${1:-}" in
    -h|--help)
      say "Usage: sh bootstrap.sh [TARGET_DIR]"
      say "  TARGET_DIR   where to install (default: \$RVND_DIR, else \$HOME/rvnd)"
      return 0 ;;
  esac

  say "RVND bootstrap"
  say "========================================"

  if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
    say "! running as root — RVND is a user tool; installing into root's home."
  fi

  # Minimal fetch pre-flight (the full check runs post-clone). git + curl are
  # what we need just to bring the repo down.
  have git  || die "git not found. macOS: xcode-select --install  ·  Linux: install 'git', then re-run."
  have curl || die "curl not found — required to fetch. Install curl and re-run."

  # Resolve the target: env, then arg, then default. Expand ~ lazily via HOME.
  TARGET="${RVND_DIR:-${1:-$HOME/rvnd}}"

  # Clone, or update in place — but never clobber someone else's directory.
  if [ -d "$TARGET/.git" ]; then
    origin="$(git -C "$TARGET" remote get-url origin 2>/dev/null || echo '')"
    case "$origin" in
      *flxk1/RVND*|*"/RVND.git"|*"/RVND")
        say "› updating existing RVND at $TARGET"
        git -C "$TARGET" pull --ff-only || die "could not fast-forward $TARGET (local changes?). Resolve, then re-run." ;;
      *)
        die "$TARGET is a git repo for something else ($origin). Set RVND_DIR to a fresh path." ;;
    esac
  elif [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null || echo x)" ]; then
    die "$TARGET already exists and is not empty. Set RVND_DIR to a new path."
  else
    say "› cloning RVND into $TARGET"
    git clone --depth 1 "$REPO_URL" "$TARGET" || die "git clone failed."
  fi

  cd "$TARGET" || die "cannot enter $TARGET"

  # Full pre-flight from the freshly-cloned tree (python, venv, disk, write).
  if [ -f scripts/preflight.sh ]; then
    say ""
    sh scripts/preflight.sh "$PWD" || die "pre-flight failed — fix the above and re-run this bootstrap."
  fi

  # Install into the repo-root .venv (idempotent). install.sh uses bash.
  say ""
  say "› installing (this builds an isolated .venv)…"
  if [ -f server/install.sh ]; then
    ./server/install.sh
  else
    die "server/install.sh not found in $TARGET — is this a full RVND clone?"
  fi

  # Post-install health gate — informational. doctor exits non-zero for
  # warnings (e.g. no controller key yet), which is normal on a fresh install,
  # so never let it fail the bootstrap.
  if [ -x .venv/bin/workspaces ]; then
    say ""
    say "› health check (workspaces doctor):"
    .venv/bin/workspaces doctor || true
  fi

  say ""
  say "========================================"
  say "✓ RVND is installed at $TARGET"
  say ""
  say "Next:"
  say "  cd $TARGET"
  say "  source .venv/bin/activate"
  say "  workspaces init                 # guided first-run setup"
  say "  workspaces guide                # what every command does"
  case "$(uname -s 2>/dev/null || echo '')" in
    Darwin) say "  open 'app/Open Rvnd.command'    # launch the console" ;;
    *)      say "  python app/serve.py             # launch the console → http://127.0.0.1:8799" ;;
  esac
  say "  ./scripts/connect-agent-hub.sh  # let Claude Code / Codex drive RVND"
}

main "$@"

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
# isolated .venv (server/install.sh), runs a health check, then — when a
# Claude Code CLI is on PATH — wires the agent (MCP server + plugin + a
# monitor-mode PreToolUse hook) via scripts/connect-agent-hub.sh. It is
# non-interactive and idempotent — safe to re-run; re-running updates the
# engine and re-applies the agent wiring in place.
#
# Where it installs (first match wins):
#   $RVND_DIR   →   first argument   →   ASK (default $HOME/rvnd)   →   $HOME/rvnd
#
# If neither $RVND_DIR nor an argument is given, it ASKS where to install —
# reading the controlling terminal (/dev/tty), which works even under
# `curl … | sh` because only stdin is the script, not the tty. A truly
# non-interactive run (CI, no tty) falls through to $HOME/rvnd without asking.
#
# It will NOT touch a non-empty directory that isn't already an RVND clone.
# The deeper first-run setup (`workspaces init`) is still a separate step it
# prints at the end.

set -eu

REPO_URL="https://github.com/flxk1/RVND.git"

say()  { printf '%s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Branding — a proper installer banner. Colour only on a real terminal, and
# honour NO_COLOR.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  c_accent=$(printf '\033[38;5;39m'); c_dim=$(printf '\033[2m'); c_reset=$(printf '\033[0m')
else
  c_accent=''; c_dim=''; c_reset=''
fi

banner() {
  printf '\n%s' "$c_accent"
  printf '%s\n' \
'   ██████╗ ██╗   ██╗███╗   ██╗██████╗ ' \
'   ██╔══██╗██║   ██║████╗  ██║██╔══██╗' \
'   ██████╔╝██║   ██║██╔██╗ ██║██║  ██║' \
'   ██╔══██╗╚██╗ ██╔╝██║╚██╗██║██║  ██║' \
'   ██║  ██║ ╚████╔╝ ██║ ╚████║██████╔╝' \
'   ╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═══╝╚═════╝ '
  printf '%s' "$c_reset"
  printf '   %slocal-first governance for agentic AI · loomground%s\n\n' "$c_dim" "$c_reset"
}

main() {
  case "${1:-}" in
    -h|--help)
      say "Usage: sh bootstrap.sh [TARGET_DIR]"
      say "  TARGET_DIR   where to install (default: \$RVND_DIR, else \$HOME/rvnd)"
      return 0 ;;
  esac

  banner

  if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
    say "! running as root — RVND is a user tool; installing into root's home."
  fi

  # Minimal fetch pre-flight (the full check runs post-clone). git + curl are
  # what we need just to bring the repo down.
  have git  || die "git not found. macOS: xcode-select --install  ·  Linux: install 'git', then re-run."
  have curl || die "curl not found — required to fetch. Install curl and re-run."

  # Resolve the target: env, then arg, then ASK (default $HOME/rvnd). The prompt
  # reads /dev/tty — the controlling terminal — so it works even under
  # `curl | sh`, where stdin is the script, not the keyboard. No tty (CI /
  # non-interactive) → the default, no prompt. A leading ~ is expanded here
  # because a value from `read` is not word-expanded by the shell.
  TARGET="${RVND_DIR:-${1:-}}"
  if [ -z "$TARGET" ]; then
    default_dir="$HOME/rvnd"
    reply=""
    # Prompt ONLY if the tty is actually usable. The write attempt lives in the
    # `if` condition so that, under `set -e`, a present-but-dead /dev/tty (some
    # containers/CI) fails the test and falls through to the default instead of
    # aborting the install. Both write and read are error-tolerant.
    if [ -c /dev/tty ] && { printf 'Install RVND to [%s]: ' "$default_dir" >/dev/tty; } 2>/dev/null; then
      { IFS= read -r reply </dev/tty; } 2>/dev/null || reply=""
      case "$reply" in
        "~")    reply="$HOME" ;;
        "~/"*)  reply="$HOME/${reply#\~/}" ;;
      esac
    fi
    TARGET="${reply:-$default_dir}"
  fi

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
    say "  (the log_root line above is where the signed audit log lives —"
    say "   override with RVND_LOG_ROOT or --log-root)"
  fi

  say ""
  say "${c_accent}✓ RVND installed at $TARGET${c_reset}"

  # Agent wiring — MCP server + plugin + a monitor-mode PreToolUse hook, at
  # user scope so it's available to every project this hub opens. Monitor
  # mode logs would-be verdicts and never blocks, so it's the safe default
  # for an unattended `curl | sh` run. Runs only when a Claude Code CLI is on
  # PATH. Guarded so a wiring failure cannot fail the whole bootstrap — the
  # engine install above already succeeded and stands on its own regardless.
  AGENT_HUB=0
  if command -v claude >/dev/null 2>&1 && [ -x scripts/connect-agent-hub.sh ]; then
    say ""
    say "› wiring the agent (MCP server + plugin + monitor-mode hook, user scope)…"
    if ./scripts/connect-agent-hub.sh --yes --scope user --hook monitor; then
      AGENT_HUB=1
    else
      say "  ! agent wiring did not complete — RVND itself is installed and fine."
      say "    Retry any time:  ./scripts/connect-agent-hub.sh --yes --scope user --hook monitor"
    fi
  fi

  # One flow: when a terminal is attached (works under `curl | sh` via /dev/tty),
  # go straight into the guided multi-step setup (folder, local model, skills,
  # oversight), then show the command overview and offer the console. A
  # non-interactive run (no usable tty) prints the steps instead.
  if [ -c /dev/tty ] && [ -x .venv/bin/python ] && { : </dev/tty; } 2>/dev/null; then
    say ""
    say "› guided first-run setup"
    .venv/bin/python -m workspaces init </dev/tty \
      || say "  (setup exited — re-run anytime with: workspaces init)"
    say ""
    say "› your commands"
    .venv/bin/python -m workspaces guide </dev/tty 2>/dev/null || true
    say ""
    say "› agent"
    if [ "$AGENT_HUB" = 1 ]; then
      say "  Claude Code is wired: MCP server + plugin + hook, MONITOR mode (logs, never blocks)."
      say "  Give it teeth (ENFORCE): edit ~/.claude/settings.json, drop RVND_HOOK_MODE=monitor from the hook command."
      say "  Turn off:                set RVND_HOOK_MODE=off in that same command."
      say "  Remove entirely:         .venv/bin/rvnd-hook --uninstall --scope user"
    else
      say "  Wire Claude Code / Codex to drive RVND:  ./scripts/connect-agent-hub.sh"
    fi
    say ""
    { printf 'Open the RVND console in your browser now? [y/N]: ' >/dev/tty; } 2>/dev/null || true
    { IFS= read -r _open </dev/tty; } 2>/dev/null || _open=""
    case "$_open" in
      [Yy]*)
        case "$(uname -s 2>/dev/null || echo '')" in
          Darwin) open "app/Open Rvnd.command" 2>/dev/null \
                    || .venv/bin/python app/serve.py </dev/tty ;;
          *)      say "  console → http://127.0.0.1:8799  (Ctrl-C to stop)"
                  .venv/bin/python app/serve.py </dev/tty ;;
        esac ;;
      *) say "  Later:  cd $TARGET && python app/serve.py   →  http://127.0.0.1:8799" ;;
    esac
  else
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
    if [ "$AGENT_HUB" = 1 ]; then
      say "  Agent already wired: MCP server + plugin + hook, MONITOR mode (logs, never blocks)."
      say "    Give it teeth (ENFORCE): edit ~/.claude/settings.json, drop RVND_HOOK_MODE=monitor from the hook command."
      say "    Turn off:                set RVND_HOOK_MODE=off in that same command."
      say "    Remove entirely:         .venv/bin/rvnd-hook --uninstall --scope user"
    else
      say "  ./scripts/connect-agent-hub.sh  # wire Claude Code / Codex to drive RVND"
    fi
  fi
}

main "$@"

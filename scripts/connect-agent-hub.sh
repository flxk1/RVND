#!/bin/bash
# Connect RVND to your agent hub so an AI agent can drive the governance server:
# it registers the RVND governance MCP server, installs the governance skills,
# and installs the PreToolUse enforcement hook (monitor mode by default — it
# logs verdicts and never blocks; flip to enforce when you're ready for it to
# have teeth).
#
# Supported hubs: Claude Code (scriptable), Codex (manual — no install CLI yet).
# Safe to re-run: it detects what's already there and skips it.
#
#   ./scripts/connect-agent-hub.sh                    # asks once; hook mode prompted (default monitor)
#   ./scripts/connect-agent-hub.sh --yes              # no prompts; hook defaults to monitor
#   ./scripts/connect-agent-hub.sh --dry-run          # print the commands, change nothing
#
# --scope <project|user>
#     Where the MCP server, plugin and hook are registered. project (default)
#     ties them to this repo/checkout only. user makes them available to every
#     project you open with this hub.
# --hook <skip|monitor|enforce>
#     skip     don't install the enforcement hook now (prints the one-liner
#              to add it later).
#     monitor  install it, logging would-be verdicts to stderr, never
#              blocking. Safe default for a first install.
#     enforce  install it gating every tool call fail-closed; a NO-GO is
#              blocked.
#     Omitted + interactive: prompts, default monitor.
#     Omitted + --yes: defaults to monitor (not enforce — the safer choice
#     for an unattended run).

set -euo pipefail
cd "$(dirname "$0")/.." || { echo "Could not find the RVND folder."; exit 1; }
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"
HOOK_BIN="$REPO/.venv/bin/rvnd-hook"

DRY=0; YES=0; SCOPE="project"; HOOK_MODE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --scope) SCOPE="${2:-}"; shift 2 ;;
    --scope=*) SCOPE="${1#--scope=}"; shift ;;
    --hook) HOOK_MODE="${2:-}"; shift 2 ;;
    --hook=*) HOOK_MODE="${1#--hook=}"; shift ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 1 ;;
  esac
done

case "$SCOPE" in
  project|user) ;;
  *) echo "Invalid --scope: $SCOPE (expected project or user)" >&2; exit 1 ;;
esac
case "$HOOK_MODE" in
  ""|skip|monitor|enforce) ;;
  *) echo "Invalid --hook: $HOOK_MODE (expected skip, monitor or enforce)" >&2; exit 1 ;;
esac

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
run()  { if [ "$DRY" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

if [ ! -x "$PY" ]; then
  echo "RVND isn't installed yet. Run ./server/install.sh first (or double-click 'app/Open Rvnd.command'), then re-run this."
  exit 1
fi
if ! "$PY" -c "import rvnd" >/dev/null 2>&1; then
  echo "RVND's virtualenv exists but 'import rvnd' fails — the install is incomplete. Re-run ./server/install.sh, then re-run this."
  exit 1
fi

# The MCP server descriptor every hub needs (a stdio server run by RVND's own venv
# python, so 'rvnd' always imports regardless of the system Python).
MCP_CMD="$PY"
MCP_ARGS="-m rvnd.mcp_server"
MCP_ENV="RVND_GOVERNANCE_LAYER=on"

confirm() {
  if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then return 0; fi
  printf 'This will register RVND with your agent hub (editing its config). Continue? [Y/n] '
  read -r ans
  case "${ans:-Y}" in Y|y|"") return 0;; *) echo "Aborted."; exit 0;; esac
}

# Where the hook's install state lives for the chosen scope, so the "already
# installed" guard reads the settings file that --scope actually writes to.
if [ "$SCOPE" = "user" ]; then
  HOOK_SETTINGS="$HOME/.claude/settings.json"
else
  HOOK_SETTINGS="$REPO/.claude/settings.json"
fi

# Display-only rendering of the hook CLI's scope arguments (project scope also
# needs --dir; user scope does not), used only in printed hints below.
hook_scope_display() {
  if [ "$SCOPE" = "project" ]; then
    printf -- '--scope %s --dir "%s"' "$SCOPE" "$REPO"
  else
    printf -- '--scope %s' "$SCOPE"
  fi
}

# Runs the hook install for the given mode ("monitor" or "enforce"). Builds
# the --command value the hook CLI expects: monitor wraps the console script
# in `env RVND_HOOK_MODE=monitor`; enforce runs the console script bare.
install_hook() {
  mode="$1"
  case "$mode" in
    monitor) hook_cmd="/usr/bin/env RVND_HOOK_MODE=monitor $HOOK_BIN" ;;
    enforce) hook_cmd="$HOOK_BIN" ;;
  esac
  if [ "$SCOPE" = "project" ]; then
    run "$HOOK_BIN" --install --scope "$SCOPE" --dir "$REPO" --command "$hook_cmd" --yes
  else
    run "$HOOK_BIN" --install --scope "$SCOPE" --command "$hook_cmd" --yes
  fi
}

did=0

# ---- Claude Code (fully scriptable) ---------------------------------------
if command -v claude >/dev/null 2>&1; then
  did=1
  say "Claude Code detected (scope: $SCOPE)."
  confirm
  # 1. MCP server (idempotent: 'claude mcp get' fails if absent). -s/--scope
  #    must come after the -e flags: claude's --env is variadic and otherwise
  #    swallows the server name that follows.
  if claude mcp get rvnd-governance >/dev/null 2>&1; then
    echo "  ✓ MCP server 'rvnd-governance' already registered."
  else
    echo "  • registering the RVND governance MCP server…"
    run claude mcp add -e "$MCP_ENV" -e "RVND_AGENT=claude-code" -s "$SCOPE" rvnd-governance -- "$MCP_CMD" $MCP_ARGS
  fi
  # 2. Marketplace + skills (idempotent: check the installed list first)
  if claude plugin list 2>/dev/null | grep -q "rvnd"; then
    echo "  ✓ plugin 'rvnd' already installed."
  else
    echo "  • adding the marketplace and installing the governance skills…"
    run claude plugin marketplace add "$REPO"
    run claude plugin install "rvnd@rvnd" -s "$SCOPE"
  fi
  # What the skills ARE (and aren't): the cooperative governance cycle. Each is
  # a way to run work THROUGH the server; they take effect only when the agent
  # invokes them — step 3's hook is what binds an agent that doesn't.
  cat <<'EOF'
  • Skills installed — 8 user-action skills, cooperative (effect only when invoked):
      govern-an-action    "about to do X — is it allowed?" put a consequential action through the gate
      sign-off            the human oversight decision — what's waiting for approval? approve / hold / deny
      onboard-a-policy    bring a written policy, regulation, or contract into governance
      resolve-a-conflict  two governance rules clash — surface it and validate a resolution
      verify-a-receipt    prove one decision happened unaltered — a receipt against the signed chain
      revoke-or-erase     pull a granted authority, or erase a person from the governed record
      audit-the-ai        report the whole governance board in chat, with and without RVND
      build-a-surface     assemble and lint a governed app screen (the MCP App surface)
    They author and operate governance; they do NOT enforce it. The hook does.
EOF
  # 3. PreToolUse enforcement hook — the TEETH. Steps 1–2 give the agent RVND's
  #    tools + skills (cooperative: they govern only actions the agent routes
  #    through RVND). The hook gates EVERY tool call (native Bash/Edit + all MCP)
  #    through the SAME chokepoint — so the governance language binds even an
  #    uncooperative agent. In monitor mode it only logs; in enforce mode a
  #    NO-GO is blocked fail-closed. Reversible either way.
  #
  #    `rvnd-hook --status` reports the mode baked into the console script's
  #    own default and does not read the --command string in settings.json,
  #    so it can print mode=enforce even when the installed command wraps the
  #    hook in RVND_HOOK_MODE=monitor. The --command string in settings.json
  #    is the actual source of truth for which mode is running; don't rely on
  #    --status for that.
  if [ -f "$HOOK_SETTINGS" ] && grep -q "rvnd-hook" "$HOOK_SETTINGS" 2>/dev/null; then
    echo "  ✓ PreToolUse enforcement hook already installed ($SCOPE scope)."
    echo "    Settings file: $HOOK_SETTINGS"
  else
    if [ -z "$HOOK_MODE" ]; then
      if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then
        HOOK_MODE=monitor
      else
        printf '  • Install the PreToolUse enforcement hook?\n'
        printf '      monitor  logs would-be verdicts, never blocks (default)\n'
        printf '      enforce  blocks a NO-GO action, fail-closed\n'
        printf '      skip     do not install now\n'
        printf '    [monitor/enforce/skip] (default: monitor) '
        read -r hook_reply
        case "${hook_reply:-monitor}" in
          monitor|m) HOOK_MODE=monitor ;;
          enforce|e) HOOK_MODE=enforce ;;
          skip|s) HOOK_MODE=skip ;;
          *) echo "    · unrecognised choice, defaulting to monitor."; HOOK_MODE=monitor ;;
        esac
      fi
    fi
    case "$HOOK_MODE" in
      monitor)
        install_hook monitor
        echo "    Installed in MONITOR mode — logs would-be verdicts on stderr, never blocks."
        echo "    Give it teeth (ENFORCE):  $HOOK_BIN --install $(hook_scope_display) --command \"$HOOK_BIN\" --yes"
        echo "    Off-switch:               export RVND_HOOK_MODE=off   (disable without removing)"
        echo "    Remove entirely:          $HOOK_BIN --uninstall $(hook_scope_display)"
        ;;
      enforce)
        install_hook enforce
        echo "    Installed in ENFORCE mode — blocks a NO-GO action, fail-closed."
        echo "    Step back to MONITOR:  $HOOK_BIN --install $(hook_scope_display) --command \"/usr/bin/env RVND_HOOK_MODE=monitor $HOOK_BIN\" --yes"
        echo "    Off-switch:             export RVND_HOOK_MODE=off   (disable without removing)"
        echo "    Remove entirely:        $HOOK_BIN --uninstall $(hook_scope_display)"
        ;;
      skip)
        echo "    · skipped — governance stays DECLARED-ONLY until you add the hook."
        echo "      Enable later (monitor): $HOOK_BIN --install $(hook_scope_display) --command \"/usr/bin/env RVND_HOOK_MODE=monitor $HOOK_BIN\" --yes"
        ;;
    esac
  fi
  echo "  Done — restart Claude Code (or run /reload) if the tools/skills aren't visible yet."
  echo "  Note: installed alone, the skills run in transparency mode (they cascade to a host-only"
  echo "  view without the engine). This connection added the governed mode — the signed board,"
  echo "  enforcement, and reconciliation."
fi

# ---- Codex (manual: no install CLI) --------------------------------------
if [ -d "$HOME/.codex" ]; then
  did=1
  say "Codex detected — one manual step (Codex has no install CLI):"
  cat <<EOF
  • Add this MCP server to your Codex config (mcpServers shape):
      "rvnd-governance": {
        "command": "$MCP_CMD",
        "args": ["-m", "rvnd.mcp_server"],
        "env": { "RVND_GOVERNANCE_LAYER": "on", "RVND_AGENT": "codex" }
      }
  This gives Codex RVND's MCP tools (cooperative governance). The skills and the
  PreToolUse enforcement hook are Claude Code only for now.
EOF
fi

# ---- The governance language (handed at the front door) ------------------
say "The governance language (the machine-readable policy your agent is bound by):"
cat <<'EOF'
The MCP handshake hands it to the agent as the `governance://llms.txt` resource
(consumed from loomground-governance, never copied). You can also read it from
the console at http://127.0.0.1:8799/llms.txt .
The language DECLARES the policy; the PreToolUse hook (step 3) is what ENFORCES
it. With the hook installed in enforce mode, every tool call is gated
(GO / CONDITIONAL / NO-GO) through the signed chokepoint and a NO-GO is
blocked fail-closed. In monitor mode the same verdicts are logged, not
enforced. Without the hook, the language binds only the actions the agent
chooses to route through RVND.
EOF

# ---- Workspace & Lock (the DATA boundary — separate from the action gate) --
say "Workspace & Lock (optional — the DATA boundary, distinct from the hook):"
cat <<'EOF'
Connecting an agent (above) needs NO workspace. A WORKSPACE is a governed folder
you create when you want a per-folder policy, its own signed audit chain, or
egress control for that folder:   workspaces init
The LOCK is that folder's network-egress boundary: when sealed, outbound traffic
and data pass through a proxy that classifies and can REFUSE egress, so
confidential context cannot leave the folder:   workspace-lock setup
In one line: the hook gates what the agent DOES; the Lock gates what DATA leaves.
EOF

# ---- No known hub --------------------------------------------------------
if [ "$did" = 0 ]; then
  say "No Claude Code or Codex install detected."
  cat <<EOF
Point any MCP-capable host at this local stdio server:
    command: $MCP_CMD
    args:    -m rvnd.mcp_server
    env:     RVND_GOVERNANCE_LAYER=on RVND_AGENT=<your-agent-name>
A ready-made descriptor lives at:
    $REPO/plugin/rvnd/mcp/rvnd.mcp.json
EOF
fi

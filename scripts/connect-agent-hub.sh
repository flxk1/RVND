#!/bin/bash
# Connect RVND to your agent hub so an AI agent can drive the governance server:
# it registers the RVND governance MCP server and installs the governance skills.
#
# Supported hubs: Claude Code (scriptable), Codex (manual — no install CLI yet).
# Safe to re-run: it detects what's already there and skips it.
#
#   ./scripts/connect-agent-hub.sh            # do it (asks once before changing config)
#   ./scripts/connect-agent-hub.sh --yes      # no prompt
#   ./scripts/connect-agent-hub.sh --dry-run  # print the commands, change nothing

set -euo pipefail
cd "$(dirname "$0")/.." || { echo "Could not find the RVND folder."; exit 1; }
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"

DRY=0; YES=0
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --yes|-y) YES=1;; -h|--help) sed -n '2,11p' "$0"; exit 0;; esac; done

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
run()  { if [ "$DRY" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

if [ ! -x "$PY" ]; then
  echo "RVND isn't installed yet. Run ./server/install.sh first (or double-click 'app/Open Rvnd.command'), then re-run this."
  exit 1
fi

# The MCP server descriptor every hub needs (a stdio server run by RVND's own venv
# python, so 'workspaces' always imports regardless of the system Python).
MCP_CMD="$PY"
MCP_ARGS="-m workspaces.mcp_server"
MCP_ENV="RVND_GOVERNANCE_LAYER=on"

confirm() {
  if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then return 0; fi
  printf 'This will register RVND with your agent hub (editing its config). Continue? [Y/n] '
  read -r ans
  case "${ans:-Y}" in Y|y|"") return 0;; *) echo "Aborted."; exit 0;; esac
}

did=0

# ---- Claude Code (fully scriptable) ---------------------------------------
if command -v claude >/dev/null 2>&1; then
  did=1
  say "Claude Code detected."
  confirm
  # 1. MCP server (idempotent: 'claude mcp get' fails if absent)
  if claude mcp get rvnd-governance >/dev/null 2>&1; then
    echo "  ✓ MCP server 'rvnd-governance' already registered."
  else
    echo "  • registering the RVND governance MCP server…"
    run claude mcp add -e "$MCP_ENV" -e "RVND_AGENT=claude-code" rvnd-governance -- "$MCP_CMD" $MCP_ARGS
  fi
  # 2. Marketplace + skills (idempotent: check the installed list first)
  if claude plugin list 2>/dev/null | grep -q "rvnd-governance"; then
    echo "  ✓ plugin 'rvnd-governance' already installed."
  else
    echo "  • adding the marketplace and installing the governance skills…"
    run claude plugin marketplace add "$REPO"
    run claude plugin install "rvnd-governance@rvnd"
  fi
  # What the skills ARE (and aren't): the cooperative governance cycle. Each is
  # a way to run work THROUGH the server; they take effect only when the agent
  # invokes them — step 3's hook is what binds an agent that doesn't.
  cat <<'EOF'
  • Skills installed — the governance cycle, cooperative (effect only when used):
      /rvnd-govern    put a consequential action through the gate (propose → server verdict → apply)
      /rvnd-decide    the human decision — ratify / hold / escalate a verdict
      /reason-governance-rules   ask the server for an action's governed outcome (read-only)
      /extract-policy-norms + /compile-loomground-policy   turn source rules into a typed policy patch
      /resolve-rule-conflicts    server-validated resolution of clashing rules
      /rvnd-audit     verify the tamper-evident chain (receipts vs the Ed25519 log)
      /rvnd-incident  revoke authority / record erasure after an incident
    They author and operate governance; they do NOT enforce it. The hook does.
EOF
  # 3. PreToolUse enforcement hook — the TEETH. Steps 1–2 give the agent RVND's
  #    tools + skills (cooperative: they govern only actions the agent routes
  #    through RVND). The hook gates EVERY tool call (native Bash/Edit + all MCP)
  #    through the SAME chokepoint, fail-closed — so the governance language binds
  #    even an uncooperative agent. Scoped to THIS project, reversible.
  if [ -f "$REPO/.claude/settings.json" ] && grep -q "rvnd-hook\|workspaces.hook" "$REPO/.claude/settings.json" 2>/dev/null; then
    echo "  ✓ PreToolUse enforcement hook already installed for this project."
  else
    if [ "$YES" = 1 ]; then dohook=y; else
      printf '  • Install the PreToolUse ENFORCEMENT hook for THIS project?\n'
      printf '    It gates every tool call (fail-closed) and can BLOCK actions. Reversible. [y/N] '
      read -r dohook
    fi
    case "${dohook:-N}" in
      y|Y) run "$PY" -m workspaces.hook --install --scope project --dir "$REPO" \
             --command "$PY -m workspaces.hook"
           echo "    Off-switch:  export RVND_HOOK_MODE=off     (disable without removing)"
           echo "    Dry-run:     export RVND_HOOK_MODE=monitor  (log verdicts, never block)"
           echo "    Remove:      $PY -m workspaces.hook --uninstall --scope project --dir \"$REPO\"" ;;
      *)   echo "    · skipped — governance stays DECLARED-ONLY until you add the hook."
           echo "      Enable later: $PY -m workspaces.hook --install" ;;
    esac
  fi
  echo "  Done — restart Claude Code (or run /reload) if the tools/skills aren't visible yet."
fi

# ---- Codex (manual: no install CLI) --------------------------------------
if [ -d "$HOME/.codex" ]; then
  did=1
  say "Codex detected — one manual step (Codex has no install CLI):"
  cat <<EOF
  • Add this MCP server to your Codex config (mcpServers shape):
      "rvnd-governance": {
        "command": "$MCP_CMD",
        "args": ["-m", "workspaces.mcp_server"],
        "env": { "RVND_GOVERNANCE_LAYER": "on", "RVND_AGENT": "codex" }
      }
  • Enable the plugin manifest at:
      $REPO/.codex-plugin/plugin.json
EOF
fi

# ---- The governance language (handed at the front door) ------------------
say "The governance language (the machine-readable policy your agent is bound by):"
cat <<'EOF'
The MCP handshake hands it to the agent as the `governance://llms.txt` resource
(consumed from loomground-governance, never copied). You can also read it from
the console at http://127.0.0.1:8799/llms.txt .
The language DECLARES the policy; the PreToolUse hook (step 3) is what ENFORCES
it. With the hook installed, every tool call is gated (GO / CONDITIONAL / NO-GO)
through the signed chokepoint and a NO-GO is blocked fail-closed. Without it, the
language binds only the actions the agent chooses to route through RVND.
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
    args:    -m workspaces.mcp_server
    env:     RVND_GOVERNANCE_LAYER=on RVND_AGENT=<your-agent-name>
A ready-made descriptor lives at:
    $REPO/plugin/rvnd-governance/mcp/rvnd.mcp.json
EOF
fi

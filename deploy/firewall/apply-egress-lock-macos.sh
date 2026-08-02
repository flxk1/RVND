#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
#
# Opt-in macOS pf installer. It manages only the com.rvnd.egress-lock anchor
# and never replaces the host's complete pf ruleset.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ANCHOR=com.rvnd.egress-lock
DEST=/etc/pf.anchors/com.rvnd.egress-lock
AGENT_USER=rvnd-agent
MODE=
MODE_COUNT=0

fail() { echo "REFUSING: $*" >&2; exit 1; }
usage() {
    echo "usage: $0 (--dry-run | --apply | --remove) [--agent-user <name>]"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --agent-user) AGENT_USER="${2:?--agent-user needs a value}"; shift 2 ;;
        --dry-run) MODE=dry-run; MODE_COUNT=$((MODE_COUNT + 1)); shift ;;
        --apply) MODE=apply; MODE_COUNT=$((MODE_COUNT + 1)); shift ;;
        --remove) MODE=remove; MODE_COUNT=$((MODE_COUNT + 1)); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown flag: $1" ;;
    esac
done

[ "$MODE_COUNT" -eq 1 ] || fail "choose exactly one of --dry-run, --apply, or --remove"
case "$AGENT_USER" in ""|*[!A-Za-z0-9._-]*) fail "invalid agent user" ;; esac
[ "$(uname -s)" = Darwin ] || fail "this installer is for macOS"
command -v pfctl >/dev/null 2>&1 || fail "pfctl not found"

if [ "$MODE" = remove ]; then
    [ "$(id -u)" -eq 0 ] || fail "removal requires root"
    pfctl -a "$ANCHOR" -F all
    echo "RESULT: RVND pf anchor flushed. Remove $DEST if it is no longer needed."
    exit 0
fi

id "$AGENT_USER" >/dev/null 2>&1 || fail "agent user '$AGENT_USER' does not exist"
RENDERED="$(sed "s/@AGENT_USER@/$AGENT_USER/g" "$SCRIPT_DIR/pf.conf")"
printf '%s\n' "$RENDERED" | pfctl -vnf -

if [ "$MODE" = dry-run ]; then
    echo "RESULT: template valid; would load anchor $ANCHOR from $DEST."
    exit 0
fi

[ "$(id -u)" -eq 0 ] || fail "apply requires root"
printf '%s\n' "$RENDERED" > "$DEST"
pfctl -a "$ANCHOR" -f "$DEST"
pfctl -E >/dev/null
echo "RESULT: RVND egress lock loaded in pf anchor $ANCHOR."
echo "Persist the anchor from the host pf.conf; see deploy/firewall/README.md."

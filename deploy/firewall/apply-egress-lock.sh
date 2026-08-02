#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
#
# One-command apply for the load-bearing egress lock (Linux/nftables).
#
# Renders deploy/firewall/nftables.conf with the proxy uid, writes it to
# /etc/nftables-rvnd-egress.conf,
# loads it as one atomic transaction, then runs the verifier. Ends in one
# plain line: lock in force / NOT in force / unverified, and exits with the
# verifier's code (0/1/2).
#
# Safe to re-run: the table is replaced atomically, never stacked. There are
# no provider address sets and therefore no DNS refresh duty.
#
#   deploy/firewall/apply-egress-lock.sh --dry-run [--proxy-user <name|uid>]
#   sudo deploy/firewall/apply-egress-lock.sh --apply [--proxy-user <name|uid>]
#   sudo deploy/firewall/apply-egress-lock.sh --remove
#
# --dry-run needs no root: on Linux it resolves and prints the provider
# IPs, names the destination file, and lints the templates without touching
# the firewall; elsewhere it lints only. Apply is Linux only -- macOS
# (pf.conf) and Windows (windows-firewall.ps1) stay operator-applied from
# their templates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/nftables.conf"
DEST=/etc/nftables-rvnd-egress.conf
TABLE=rvnd_egress_lock

PROXY_USER=rvnd-egress
DRY_RUN=0
APPLY=0
REMOVE=0

usage() {
    echo "usage: $0 (--dry-run | --apply | --remove) [--proxy-user <name|uid>]"
    echo "  --proxy-user  OS user the egress proxy runs as (default: rvnd-egress)"
    echo "  --dry-run     resolve + lint only; no root, no firewall change"
    echo "  --apply       load the rendered RVND nftables table (requires root)"
    echo "  --remove      delete only the RVND nftables table (requires root)"
}

fail() {
    echo "REFUSING: $*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --proxy-user) PROXY_USER="${2:?--proxy-user needs a value}"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --apply)      APPLY=1; shift ;;
        --remove)     REMOVE=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            usage >&2; fail "unknown flag: $1" ;;
    esac
done

[ $((DRY_RUN + APPLY + REMOVE)) -eq 1 ] \
    || fail "choose exactly one of --dry-run, --apply, or --remove"

case "$PROXY_USER" in
    ""|*[!A-Za-z0-9._-]*) fail "proxy user must be a plain name or uid, got: '$PROXY_USER'" ;;
esac

run_verifier() {
    local rc=0
    (cd "$REPO_ROOT" && PYTHONPATH=server/src python3 scripts/verify_egress_lock.py "$@") || rc=$?
    return $rc
}

report_and_exit() {
    case "$1" in
        0) echo "RESULT: egress lock in force." ;;
        2) echo "RESULT: egress lock UNVERIFIED -- could not read the live ruleset." ;;
        *) echo "RESULT: egress lock NOT in force." ;;
    esac
    exit "$1"
}

# --- non-Linux: never pretend ------------------------------------------------
if [ "$(uname -s)" != "Linux" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "This host is $(uname -s); apply is Linux/nftables only. macOS (pf.conf)"
        echo "and Windows (windows-firewall.ps1) stay operator-applied. Linting the"
        echo "templates is all that can be checked here:"
        rc=0
        run_verifier --dry-run || rc=$?
        exit $rc
    fi
    fail "apply is Linux/nftables only (this host is $(uname -s)). macOS and Windows apply their deploy/firewall/ templates by hand."
fi

if [ "$REMOVE" -eq 1 ]; then
    [ "$(id -u)" -eq 0 ] || fail "not root. Re-run with sudo."
    command -v nft >/dev/null 2>&1 || fail "nft not found."
    if nft list table inet "$TABLE" >/dev/null 2>&1; then
        nft delete table inet "$TABLE"
        echo "RESULT: RVND egress lock removed."
    else
        echo "RESULT: RVND egress lock was not installed."
    fi
    exit 0
fi

# --- preflight ---------------------------------------------------------------
if [ "$DRY_RUN" -eq 0 ]; then
    [ "$(id -u)" -eq 0 ] || fail "not root. Re-run with sudo -- loading a kernel firewall ruleset needs it."
    command -v nft >/dev/null 2>&1 || fail "nft not found. Install nftables (e.g. apt-get install nftables) and re-run."
    case "$PROXY_USER" in
        *[!0-9]*) getent passwd "$PROXY_USER" >/dev/null \
            || fail "user '$PROXY_USER' does not exist. Create the egress-proxy user first, or pass --proxy-user." ;;
    esac
fi

# --- render identity-bound rules ---------------------------------------------
RENDERED="$(sed "s/\${PROXY_USER}/$PROXY_USER/g" "$TEMPLATE")"

# Atomic replace in one nft transaction: 'add' is a no-op when the table
# exists, 'delete' then always succeeds, and the fresh table loads in the
# same netlink commit -- no window with the lock absent, no rule stacking.
RENDERED="# Rendered by deploy/firewall/apply-egress-lock.sh -- do not edit; re-run the script.
add table inet $TABLE
delete table inet $TABLE
$RENDERED"

# --- dry-run: show, lint, stop -----------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
    echo "would write the rendered ruleset to $DEST and load it with: nft -f $DEST"
    rc=0
    run_verifier --dry-run || rc=$?
    exit $rc
fi

# --- apply and verify ----------------------------------------------------------
printf '%s\n' "$RENDERED" > "$DEST"
nft -f "$DEST"
echo "loaded $DEST (table inet $TABLE, proxy user: $PROXY_USER)"

rc=0
run_verifier || rc=$?
report_and_exit $rc

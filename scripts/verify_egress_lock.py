#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Verify the identity-bound OS egress lock is in force.

The egress proxy (server/src/rvnd/lock/egress_proxy.py) scans and audits
every prompt, but it only *enforces* privacy when the OS also blocks direct
outbound to the provider hosts from everything except the proxy. The templates
in deploy/firewall/ set that up; this script checks it took.

It reads the live firewall ruleset for the current platform and confirms that
the agent has no direct egress and, on dedicated Linux hosts, only the proxy
identity can originate outbound traffic. It never mutates the firewall.
the default live mode reads the OS ruleset, which requires root (Administrator
on Windows). An unreadable ruleset is reported as SKIP (not verified), never
as a missing lock.

Modes:
  --dry-run / --lint   Validate deploy/firewall/ templates and report the
                       expected rules WITHOUT reading the live firewall. Safe
                       for CI: rootless, no state, no mutation.
  (default)            Read the live ruleset and check the lock is active.

Exit conventions:
  0   lock in force (default mode) / templates well-formed (dry-run)
  1   lock NOT in force, or a template is missing/malformed
  2   not verified — unsupported/undetected OS, or the live ruleset could not
      be read (needs root / dump tool unavailable). See the SKIP message.
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_NOT_IN_FORCE = 1
EXIT_SKIP = 2

# The four provider hosts the lock must cover — mirrors _ALLOWED_UPSTREAMS in
# egress_proxy.py — mapped to the set/table token the deploy/firewall/
# templates actually use (nftables sets carry _v4/_v6 suffixes, pf tables use
# the bare token). If either side changes, this mapping must change with it.
PROVIDER_SETS = {
    "api.anthropic.com": "anthropic",
    "api.openai.com": "openai",
    "api.cohere.ai": "cohere",
    "generativelanguage.googleapis.com": "google_genai",
}
PROVIDER_HOSTS = tuple(PROVIDER_SETS)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIREWALL_DIR = _REPO_ROOT / "deploy" / "firewall"

# Windows rules store resolved IPs in the address filter, not hostnames, and
# Get-NetFirewallRule alone prints neither — join each rule with its address
# filter and print name|action|addresses per line.
_WINDOWS_DUMP = (
    "Get-NetFirewallRule -DisplayName 'Rvnd egress lock*' | ForEach-Object { "
    "$af = $_ | Get-NetFirewallAddressFilter; "
    "'{0}|{1}|{2}' -f $_.DisplayName, $_.Action, "
    "($af.RemoteAddress -join ',') }"
)

# Per-platform template + the command that dumps the live ruleset.
_PLATFORMS = {
    # Inspect only RVND's table. Host and runner rules may legitimately contain
    # destination-scoped accepts and must not be attributed to this lock.
    "Linux": ("nftables.conf", ["nft", "list", "table", "inet", "rvnd_egress_lock"]),
    "Darwin": ("pf.conf", ["pfctl", "-sr"]),
    "Windows": ("windows-firewall.ps1",
                ["powershell", "-NoProfile", "-Command", _WINDOWS_DUMP]),
}

# Live dumps need privileges (nft/pfctl need root; see module docstring).
# These markers distinguish "cannot read the ruleset" from "no lock loaded".
_PERMISSION_MARKERS = (
    "permission denied",
    "operation not permitted",
    "must be root",
    "access is denied",
    "requires elevation",
)


def _read_live_ruleset(cmd: list[str]) -> tuple[str, int]:
    """Dump the live ruleset. Read-only — never mutates the firewall."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "", 127
    return proc.stdout + proc.stderr, proc.returncode


def _cannot_read(output: str, returncode: int | None) -> bool:
    """True when the dump failed for lack of privileges or a missing tool."""
    low = output.lower()
    if any(marker in low for marker in _PERMISSION_MARKERS):
        return True
    return returncode is not None and returncode != 0 and not output.strip()


def _nft_sets(ruleset: str) -> dict[str, bool]:
    """Map set name -> has elements, from an ``nft list ruleset`` dump."""
    sets: dict[str, bool] = {}
    current: str | None = None
    depth = 0
    body: list[str] = []
    for line in ruleset.splitlines():
        if current is None:
            m = re.search(r"\bset\s+(\w+)\s*\{", line)
            if not m:
                continue
            current = m.group(1)
            segment = line[m.end() - 1:]
            depth = segment.count("{") - segment.count("}")
            body = [segment]
        else:
            depth += line.count("{") - line.count("}")
            body.append(line)
        if current is not None and depth <= 0:
            sets[current] = "elements" in " ".join(body)
            current = None
    return sets


def _check_linux(ruleset: str) -> list[str]:
    """Dedicated-host form: output policy drop, loopback/return traffic, and
    exactly the proxy uid's explicit outbound accept."""
    reasons: list[str] = []
    compact = " ".join(ruleset.split())
    if not re.search(r"chain output \{[^}]*policy drop", compact):
        reasons.append("output chain is not default-drop")
    accepts = [
        line.strip() for line in ruleset.splitlines()
        if "accept" in line and "skuid" in line
    ]
    if len(accepts) != 1:
        reasons.append("expected exactly one uid-scoped proxy accept")
    if re.search(r"\b(ip|ip6)\s+daddr\b", ruleset):
        reasons.append("destination-scoped rules present; lock is not identity-wide")
    return reasons


def _check_darwin(ruleset: str) -> list[str]:
    """Shared-host form: one quick agent-uid block covers TCP and UDP."""
    reasons: list[str] = []
    lines = [ln.strip() for ln in ruleset.splitlines()]
    blocks = [ln for ln in lines if ln.startswith("block") and "user" in ln]
    if not any("quick" in ln and "tcp" in ln and "udp" in ln for ln in blocks):
        reasons.append("no quick agent-user block covering TCP and UDP")
    if any("<" in ln and ">" in ln for ln in blocks):
        reasons.append("agent block is destination-scoped")
    return reasons


def _check_windows(ruleset: str) -> list[str]:
    """The agent block must exist and must not carry address scoping."""
    reasons: list[str] = []
    block_ok = False
    for line in ruleset.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        name, action, addrs = parts[0].lower(), parts[1].lower(), parts[2]
        if "block agent" in name and action == "block":
            if addrs.lower() in ("", "any", "*"):
                block_ok = True
            else:
                reasons.append("agent block is address-scoped")
    if not block_ok:
        reasons.append("identity-wide agent block rule not present")
    return reasons


_CHECKERS = {
    "Linux": _check_linux,
    "Darwin": _check_darwin,
    "Windows": _check_windows,
}


def check_lock_in_force(ruleset: str, system: str) -> list[str]:
    """Return the reasons the lock is not in force; empty list means it is.

    Honest and coarse: it confirms the rules and populated sets/tables the
    templates install are present in the live dump. It cannot prove the OS
    will drop a packet — that is the kernel's job."""
    if not ruleset.strip():
        return ["empty ruleset — no egress lock loaded"]
    checker = _CHECKERS.get(system)
    if checker is None:  # callers gate on _PLATFORMS first; belt and braces
        return [f"no checker for platform {system!r}"]
    return checker(ruleset)


def lint_templates() -> list[str]:
    """Validate deploy/firewall/ templates statically. Returns problems."""
    problems: list[str] = []
    for tmpl, _ in _PLATFORMS.values():
        path = _FIREWALL_DIR / tmpl
        if not path.exists():
            problems.append(f"missing template: deploy/firewall/{tmpl}")
            continue
        text = path.read_text()
        if tmpl == "nftables.conf":
            if "policy drop" not in text or "meta skuid ${PROXY_USER} accept" not in text:
                problems.append(f"{tmpl}: missing default-drop/proxy-uid invariant")
        elif tmpl == "pf.conf":
            if "block return out quick proto { tcp, udp } user @AGENT_USER@" not in text:
                problems.append(f"{tmpl}: missing all-destination agent block")
        elif "-RemoteAddress" in text or "-Protocol" in text:
            problems.append(f"{tmpl}: agent block must cover every address/protocol")
    return problems


def run(argv: list[str], *, ruleset: str | None = None,
        system: str | None = None) -> int:
    """Entry point. ``ruleset`` / ``system`` are injection seams for tests."""
    parser = argparse.ArgumentParser(
        description="Verify the OS egress lock is in force (see module docstring).")
    parser.add_argument("--dry-run", "--lint", action="store_true",
                        dest="dry_run",
                        help="validate templates only; no live firewall read")
    args = parser.parse_args(argv)

    if args.dry_run:
        problems = lint_templates()
        if problems:
            for p in problems:
                print(f"FAIL: {p}")
            return EXIT_NOT_IN_FORCE
        print("egress-lock lint: templates well-formed; "
              f"{len(PROVIDER_HOSTS)} provider hosts covered per platform")
        return EXIT_OK

    sysname = system if system is not None else platform.system()
    entry = _PLATFORMS.get(sysname)
    if entry is None:
        print(f"SKIP: no egress-lock template for platform {sysname!r} — "
              "not verified. Apply the lock manually and re-run on a "
              "supported OS (Linux/Darwin/Windows).")
        return EXIT_SKIP

    _, dump_cmd = entry
    if ruleset is not None:
        live, rc = ruleset, None
    else:
        live, rc = _read_live_ruleset(dump_cmd)
    if _cannot_read(live, rc):
        print(f"SKIP: cannot read the live ruleset on {sysname} — needs "
              "root/Administrator (or the dump tool is unavailable). The "
              "lock is UNVERIFIED, not necessarily absent. Re-run "
              "privileged, or use --dry-run for the rootless template lint.")
        return EXIT_SKIP

    reasons = check_lock_in_force(live, sysname)
    if reasons:
        print(f"FAIL: egress lock NOT in force on {sysname}:")
        for r in reasons:
            print(f"  - {r}")
        return EXIT_NOT_IN_FORCE
    print(f"egress lock in force on {sysname}: identity-wide containment verified")
    return EXIT_OK


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

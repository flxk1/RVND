# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Egress-lock verifier — dry-run lint and injected live-ruleset paths.

Exercises scripts/verify_egress_lock.py without root and without touching any
real firewall. The live-ruleset checks run against dumps derived from the
shipped identity-bound templates, so the verifier is tested against what the
templates actually produce, not a hand-rolled lookalike.
"""

import importlib.util
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "verify_egress_lock.py"
_FIREWALL = _REPO / "deploy" / "firewall"
_spec = importlib.util.spec_from_file_location("verify_egress_lock", _SCRIPT)
vel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vel)

def _nft_live_dump():
    """Simulate `nft list ruleset` after loading the shipped template:
    comments stripped (nft drops them), ${PROXY_USER} substituted (envsubst),
    with the proxy uid substituted."""
    text = (_FIREWALL / "nftables.conf").read_text()
    lines = []
    for ln in text.splitlines():
        ln = ln.split("#", 1)[0].rstrip()
        if ln.strip():
            lines.append(ln.replace("${PROXY_USER}", "4242"))
    return "\n".join(lines)


def _pf_live_dump(quick=True, with_block=True):
    """Simulate the identity-wide agent block printed by ``pfctl -sr``."""
    q = "quick " if quick else ""
    return (
        f"block return out {q}proto {{ tcp udp }} from any to any "
        "user rvnd-agent"
    ) if with_block else "pass out all"


def test_dry_run_is_rootless_and_passes():
    assert vel.run(["--dry-run"]) == vel.EXIT_OK
    assert vel.run(["--lint"]) == vel.EXIT_OK


def test_templates_are_identity_wide_not_destination_scoped():
    nft_text = (_FIREWALL / "nftables.conf").read_text()
    assert "policy drop" in nft_text
    assert "meta skuid ${PROXY_USER} accept" in nft_text
    assert "set " not in nft_text
    pf_text = (_FIREWALL / "pf.conf").read_text()
    assert "quick proto { tcp, udp } user @AGENT_USER@" in pf_text
    assert "table <" not in pf_text
    ps1_text = (_FIREWALL / "windows-firewall.ps1").read_text()
    assert "block agent" in ps1_text
    assert "-RemoteAddress" not in ps1_text
    assert "-Protocol" not in ps1_text


def test_nft_template_dump_populated_passes():
    assert vel.run([], ruleset=_nft_live_dump(), system="Linux") == vel.EXIT_OK


def test_nft_live_dump_is_scoped_to_rvnd_table():
    assert vel._PLATFORMS["Linux"][1] == [
        "nft", "list", "table", "inet", "rvnd_egress_lock",
    ]


def test_nft_non_default_drop_fails():
    rc = vel.run(
        [], ruleset=_nft_live_dump().replace("policy drop", "policy accept"),
        system="Linux",
    )
    assert rc == vel.EXIT_NOT_IN_FORCE


def test_nft_dump_without_proxy_carveout_fails():
    dump = "\n".join(ln for ln in _nft_live_dump().splitlines()
                     if "skuid" not in ln)
    assert vel.run([], ruleset=dump, system="Linux") == vel.EXIT_NOT_IN_FORCE


def test_pf_template_dump_passes():
    assert vel.run([], ruleset=_pf_live_dump(), system="Darwin") == vel.EXIT_OK
    # pf.conf itself must carry quick on the block (last-match-wins).
    pf_text = (_FIREWALL / "pf.conf").read_text().replace("\\\n", " ")
    block_stmt = re.search(r"^block return out.*", pf_text, re.M).group(0)
    assert "quick" in block_stmt and "user" in block_stmt


def test_pf_pass_without_quick_fails():
    rc = vel.run([], ruleset=_pf_live_dump(quick=False), system="Darwin")
    assert rc == vel.EXIT_NOT_IN_FORCE


def test_pf_missing_agent_block_fails():
    rc = vel.run([], ruleset=_pf_live_dump(with_block=False), system="Darwin")
    assert rc == vel.EXIT_NOT_IN_FORCE


def test_windows_dump_passes_and_block_is_identity_wide():
    dump = "Rvnd egress lock - block agent|Block|Any"
    assert vel.run([], ruleset=dump, system="Windows") == vel.EXIT_OK
    ps1_text = (_FIREWALL / "windows-firewall.ps1").read_text()
    assert "(A;;CC;;;$agentSid)" in ps1_text
    assert "[string]$Mode = 'Plan'" in ps1_text
    assert "Remove-NetFirewallRule" in ps1_text


def test_installers_require_an_explicit_mode_and_offer_removal():
    linux = (_FIREWALL / "apply-egress-lock.sh").read_text()
    macos = (_FIREWALL / "apply-egress-lock-macos.sh").read_text()
    assert "DRY_RUN + APPLY + REMOVE" in linux
    assert "--remove" in linux
    assert "ANCHOR=com.rvnd.egress-lock" in macos
    assert 'pfctl -a "$ANCHOR" -f "$DEST"' in macos
    assert 'pfctl -f "$DEST"' not in macos


def test_windows_block_rule_missing_or_unpopulated_fails():
    only_allow = "Rvnd egress lock - allow proxy|Allow|Any"
    assert vel.run([], ruleset=only_allow,
                   system="Windows") == vel.EXIT_NOT_IN_FORCE
    empty_block = "Rvnd egress lock - block agent|Block|192.0.2.1"
    assert vel.run([], ruleset=empty_block,
                   system="Windows") == vel.EXIT_NOT_IN_FORCE


def test_empty_ruleset_not_in_force():
    assert vel.run([], ruleset="", system="Linux") == vel.EXIT_NOT_IN_FORCE
    reasons = vel.check_lock_in_force("", "Linux")
    assert reasons and "empty" in reasons[0]


def test_partial_lock_missing_sets_is_nonzero():
    ruleset = "block out to api.anthropic.com api.openai.com api.cohere.ai"
    assert vel.run([], ruleset=ruleset,
                   system="Linux") == vel.EXIT_NOT_IN_FORCE


def test_permission_denied_is_skip_not_fail():
    # Unprivileged dumps must read as "cannot verify", never "no lock".
    for output, system in (
        ("nft: Operation not permitted", "Linux"),
        ("pfctl: /dev/pf: Permission denied", "Darwin"),
    ):
        assert vel.run([], ruleset=output, system=system) == vel.EXIT_SKIP


def test_unsupported_os_skips():
    assert vel.run([], ruleset="whatever", system="Plan9") == vel.EXIT_SKIP

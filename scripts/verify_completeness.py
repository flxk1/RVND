#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Completeness verifier — the single runnable proof of what is functional.

Runs every UI render gate (each boots the real serve.py and drives the real
workspace_* ops in jsdom) and, with --server, the full server suite. Prints a
per-item PASS/FAIL table and exits non-zero if anything fails. This is the
The release command is the claim of record: "fully functional" means this passes.

  python3 verify_completeness.py            # UI render gates (fast)
  python3 verify_completeness.py --server   # + full server pytest suite
"""
from __future__ import annotations
import subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
APP = HERE / "app"

# Each UI item -> its render gate. The gate boots serve.py and drives the real op.
ITEMS = [
    ("Console install → first workspace → agent + governance lane", "harness/console_build_agent_test.py"),
    ("Canvas / patch (graph, verdicts server-side)", "shell/patch_render_test.py"),
    ("Patchbay + Loomground (override/query/connector/policy/register/editor)", "shell/patchbay_render_test.py"),
    ("Loom (master + governed faders + law clamp)", "shell/loom_render_test.py"),
    ("Declaration authoring (reserve/obligation, sticky)", "panels/declarations_render_test.py"),
    ("Matrix (autonomy x oversight, tighten-only)", "shell/matrix_render_test.py"),
    ("Policy drawer (discrete oversight levels, governed disable)", "panels/policy_render_test.py"),
    ("Front-door toolbar (grouped actions, name-on-create)", "shell/toolbar_render_test.py"),
    ("Audit drawer (read)", "panels/audit_render_test.py"),
    ("Conformity drawer (read, attributed)", "panels/conformity_render_test.py"),
    ("Grounder drawer (read)", "panels/grounder_render_test.py"),
    ("Lens drawer (read + WRITE: cap/precedent)", "panels/lens_render_test.py"),
    ("Privacy Lock drawer (read + WRITE: floor/seal/reclassify)", "panels/lock_render_test.py"),
    ("Data drawer (memory/mirror/erase read)", "panels/data_render_test.py"),
    ("AI & Capture drawer (read)", "panels/ai_render_test.py"),
    ("Legal drawer (read)", "panels/legal_render_test.py"),
    ("About float (server_info)", "shell/about_render_test.py"),
    ("Approvals inbox (workspace_contract, multi-signer)", "panels/approvals_render_test.py"),
    ("Live Audit Ticker (workspace_audit tail)", "shell/ticker_render_test.py"),
    ("Workflow board (run/enqueue/cancel/resume)", "panels/workflow_render_test.py"),
    ("Live Governance (sessions · run-lease serialization · per-agent verdict · one chain)", "panels/govlive_render_test.py"),
    ("Integral governance strip (always-on lights · HOTL alarm · expands to drawer)", "shell/govstrip_render_test.py"),
    ("Contract execution (reviews/ingest/state/resolve)", "panels/contract_render_test.py"),
    ("Policy lock-mode WRITE (tighten direct / loosen governed)", "panels/policy_write_render_test.py"),
    ("Workspace workspace-creator (create + switch)", "shell/workspace_render_test.py"),
    ("MIDI-learn controller + All-Stop", "shell/controller_render_test.py"),
    ("Workspaces rail (channel per workspace, L0-L4 LEDs, group/send)", "shell/wsrail_render_test.py"),
    ("Read-only badge on read-only drawers", "shell/slice_e_render_test.py"),
    ("Verdict Router node (visualize-only verdict-handling map)", "panels/verdict_router_render_test.py"),
    ("Transport bar (resume/hold/all-stop + always-on REC)", "shell/transport_render_test.py"),
    ("Inspector sign-off CTA (verdict→action + oversight traffic light)", "panels/signoff_cta_render_test.py"),
    ("PATCH ⇄ ARRANGE view (lanes + mixer strips with mode-switched screens)", "shell/arrange_render_test.py"),
    ("Empty workspace shows empty (no demo leak; panels agree)", "shell/empty_workspace_render_test.py"),
    ("Environment save/open (signed .rvnd bundle: build + verify + adopt)", "shell/session_env_render_test.py"),
    ("Federation drawer (channels, client groups, joined verdict, kill switches, group floor)", "panels/federation_render_test.py"),
    ("Data-lineage tags in Inspector (authored ∪ connector; tag-guarded reservation)", "panels/tags_render_test.py"),
    ("Federated verdict in Check (joined strictest-wins + disagreement)", "panels/federated_verdict_render_test.py"),
    ("Mirror review lifecycle in Data drawer (history/diff/discard)", "panels/mirror_review_render_test.py"),
    ("Workflow delete + skill unpin (list remove actions)", "panels/wf_unpin_render_test.py"),
    ("Jurisdiction packs + delegate signing in Protections", "panels/policy_extra_render_test.py"),
    ("Pin skills (pin/pin_many) + suggest companions in AI drawer", "panels/ai_pin_render_test.py"),
    ("Policy map paste→map→ask over live governance_map + chat routing", "panels/governance_map_render_test.py"),
    ("Help drawer (operation reference from live op catalogues)", "shell/help_render_test.py"),
    ("First-run onboarding wizard (true first-run only; get-started strip)", "shell/wizard_render_test.py"),
    ("Privacy-lock backend setup CTA (wizard run from the drawer)", "panels/lock_setup_render_test.py"),
    ("Obligations board (Pending; severity bins, drill-in, read-only)", "panels/obligations_render_test.py"),
    ("Federated verdict composition (dominator, bindings, muted struck)", "panels/federation_comp_render_test.py"),
    ("Decision workbench (options+chat+judgment; earned considered; one write)", "panels/decision_render_test.py"),
    ("Model attestation card in Audit (verdict, drift vs coverage gap)", "panels/attestation_render_test.py"),
    ("Attestation triggers in Audit (baseline entry, run battery, admit note; exact payloads)", "panels/attest_trigger_render_test.py"),
    ("Decision routing (pending list, claim lease, mine-filter, closure)", "panels/decision_routing_render_test.py"),
    ("Action-link identity (acting-as, record via token, spent link refused)", "panels/decision_link_render_test.py"),
    ("Trusted-front identity (signed-in chip, proxy actor overrides client)", "panels/proxy_identity_render_test.py"),
    ("Co-decision panel (sealed badge, seat claim, counts, no rationale leak)", "panels/decision_panel_render_test.py"),
    ("Coverage lens Kind x risk preset (server coverage_matrix; kinds x bands, empty band 'none')", "panels/coverage_matrix_render_test.py"),
    ("Egress board LLM-egress attestation (live broker probe: enforced vs attested, worded)", "panels/egress_render_test.py"),
    ("MATRIX canvas view (third view-toggle state; coverage grid full-stage, own preset selector)", "shell/matrix_view_render_test.py"),
    ("Roles & competence roster (grouped, competence chips, worded status, governed register)", "panels/roles_render_test.py"),
    ("DESK stage view (fourth view-toggle state; clamped read-only faders; menu shortcut)", "shell/desk_view_render_test.py"),
    ("Erasure drawer (Rules entry; scoping copy; sweep preview; confirm-gated request + status)", "panels/erasure_render_test.py"),
    ("Bring-in drawer (Set up entry; ingest-file round-trip; URL fetch confirm-gated)", "panels/bringin_render_test.py"),
    ("Draft persistence (rehydrate + prefill; debounced saves, close/pagehide flush; amber stale chip; chat divider; discard)", "shell/draft_persist_render_test.py"),
]


def run_gate(test: str) -> tuple[bool, str]:
    p = APP / test
    if not p.exists():
        return False, "MISSING TEST FILE"
    try:
        r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    lines = [ln.strip() for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    line = ""
    for ln in lines:
        if "PASS:" in ln or "FAIL:" in ln:
            line = ln
            break
    if r.returncode != 0 and not line:
        line = lines[-1] if lines else f"exited {r.returncode} without output"
    return (r.returncode == 0 and "PASS" in r.stdout), line[:90]


def main() -> int:
    print("=" * 74)
    print("RVND COMPLETENESS VERIFIER  ", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 74)
    npass = nfail = 0
    for name, test in ITEMS:
        ok, detail = run_gate(test)
        mark = "PASS" if ok else "FAIL"
        if ok: npass += 1
        else: nfail += 1
        print(f"  [{mark}] {name}")
        if not ok and detail:
            print(f"         -> {detail}")
    print("-" * 74)
    print(f"  UI render gates: {npass} PASS / {nfail} FAIL  (of {len(ITEMS)})")

    if "--server" in sys.argv:
        print("-" * 74)
        print("  running full server suite (PYTHONPATH=server/src python3 -m pytest server/tests) ...")
        r = subprocess.run(
            ["python3", "-m", "pytest", "server/tests/", "-q"],
            cwd=str(HERE), capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(HERE / "server" / "src")},
        )
        tail = [ln for ln in r.stdout.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
        print("  server:", tail[-1] if tail else "(no summary)")
        if r.returncode != 0:
            nfail += 1
    print("=" * 74)
    print("RESULT:", "ALL GREEN" if nfail == 0 else f"{nfail} FAILING")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

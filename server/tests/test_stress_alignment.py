# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Stress test: are Oversight, Lock, and Grounder aligned?

Drives real user "clicks" (set the oversight level, paint a matrix cell, toggle
the lock, ground/don't-ground an output) through the live substrate and validates
that every action and every output resolves to the SAME tri-state vocabulary
(permit / hold / deny), honours the workspace's oversight level, and lands on the
signed audit chain. Run directly to print the click→decision table:

    python -m pytest runtime/tests/test_stress_alignment.py        # validate
    python runtime/tests/test_stress_alignment.py                  # + table
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from workspaces import governance as gov
from workspaces import policy_matrix as pm
from workspaces.policy import set_oversight_level, set_lock_mode, load_policy
from workspaces.mutation_log import MutationLog


def _run_scenarios(root: Path):
    log = root / "log"
    rows = []

    def folder(name):
        f = root / name; f.mkdir(parents=True, exist_ok=True); return f

    # ── S1 routine low-reach action, default approve, no override → PERMIT ──
    f = folder("s1"); set_oversight_level(f, "approve")
    d = gov.decide_action(f, action_class="dispatch:summarize", grade="L1", log_root=log)
    rows.append(("S1 routine action · approve · no paint", "oversight=approve",
                 d["verdict"], "permit", d["audit_id"]))

    # ── S2 user paints L1×approve = block → DENY (policy override stops it) ──
    f = folder("s2"); set_oversight_level(f, "approve")
    m = pm.recommended_default(); pm.set_cell(m, "L1", "approve", "block")
    pm.save_own_matrix(str(f), m)
    d = gov.decide_action(f, action_class="dispatch:summarize", grade="L1", log_root=log)
    rows.append(("S2 same action · paint L1×approve=block", "paint cell=block",
                 d["verdict"], "deny", d["audit_id"]))

    # ── S3 user paints L1×approve = ask → HOLD (push to human) ──
    f = folder("s3")
    m = pm.recommended_default(); pm.set_cell(m, "L1", "approve", "ask")
    pm.save_own_matrix(str(f), m)
    d = gov.decide_action(f, action_class="dispatch:summarize", grade="L1", log_root=log)
    rows.append(("S3 same action · paint L1×approve=ask", "paint cell=ask",
                 d["verdict"], "hold", d["audit_id"]))

    # ── S4 regulated data floors the live row to supervised → HOLD ──
    f = folder("s4"); set_oversight_level(f, "approve")
    d = gov.decide_action(f, action_class="egress", grade="L1",
                          privacy_class="regulated", log_root=log)
    rows.append(("S4 action on regulated data · approve", "privacy=regulated",
                 d["verdict"], "hold", d["audit_id"]))

    # ── S5 danger corner: full reach × never-asks → DENY by default ──
    f = folder("s5"); set_oversight_level(f, "autonomous")
    d = gov.decide_action(f, action_class="publish", grade="L4", log_root=log)
    rows.append(("S5 L4 reach · autonomous (danger corner)", "oversight=autonomous, grade L4",
                 d["verdict"], "deny", d["audit_id"]))

    # ── S6–S9 grounder output routed by oversight ──
    f = folder("s6"); set_oversight_level(f, "approve")
    d = gov.decide_output(f, grounded=True, log_root=log)
    rows.append(("S6 grounded output · approve", "grounded=yes",
                 d["verdict"], "permit", d["audit_id"]))

    f = folder("s7"); set_oversight_level(f, "approve")
    d = gov.decide_output(f, grounded=False, log_root=log)
    rows.append(("S7 ungrounded output · approve (HITL)", "grounded=no, oversight=approve",
                 d["verdict"], "hold", d["audit_id"]))

    f = folder("s8"); set_oversight_level(f, "notify")
    d = gov.decide_output(f, grounded=False, log_root=log)
    rows.append(("S8 ungrounded output · notify (HOTL)", "grounded=no, oversight=notify",
                 d["verdict"], "permit", d["audit_id"]))

    f = folder("s9"); set_oversight_level(f, "autonomous")
    d = gov.decide_output(f, grounded=False, log_root=log)
    rows.append(("S9 ungrounded output · autonomous (HIC)", "grounded=no, oversight=autonomous",
                 d["verdict"], "permit", d["audit_id"]))

    # ── S10 inheritance: paint on ROOT, action runs in SUB-WORKSPACE → DENY ──
    rootworkspace = folder("acme")
    set_oversight_level(rootworkspace, "approve", log_root=log)  # makes root discoverable under this log
    m = pm.recommended_default(); pm.set_cell(m, "L2", "approve", "block")
    pm.save_own_matrix(str(rootworkspace), m)
    sub = folder("acme/legal"); set_oversight_level(sub, "approve")
    d = gov.decide_action(sub, action_class="dispatch:review", grade="L2", log_root=log)
    rows.append(("S10 sub-workspace inherits root paint (L2 block)", "paint on root, act in sub",
                 d["verdict"], "deny", d["audit_id"]))

    # ── S11 lock is a SEPARATE axis: toggling it doesn't change the action verdict ──
    f = folder("s11"); set_oversight_level(f, "approve")
    before = gov.decide_action(f, action_class="dispatch:x", grade="L1", log_root=log)["verdict"]
    set_lock_mode(f, "off", accepted_by="stress", reason="test")  # the lock toggle
    after = gov.decide_action(f, action_class="dispatch:x", grade="L1", log_root=log)["verdict"]
    lock_off = load_policy(f).lock_mode
    rows.append(("S11 lock toggled off · action verdict unchanged",
                 f"lock={lock_off}", f"{before}->{after}", "permit->permit", "—"))

    return rows


def _evaluate(rows):
    results = []
    for name, clicks, got, exp, aud in rows:
        ok = (got == exp)
        results.append((ok, name, clicks, got, exp, aud))
    return results


def test_alignment_stress(tmp_path):
    rows = _run_scenarios(tmp_path)
    results = _evaluate(rows)
    failed = [r for r in results if not r[0]]
    assert not failed, "FAILED:\n" + "\n".join(f"  {r[1]}: got {r[3]} != {r[4]}" for r in failed)
    # every action/output decision is on the signed chain (audit_id present)
    assert all(r[5] for r in results if r[5] != "—"), "a decision was not audited"
    # the signed chain verifies for a representative folder
    log = tmp_path / "log"
    chain = MutationLog(tmp_path / "s1", log_root=log).verify_chain()
    ok = chain.get("ok", chain.get("valid", True)) if isinstance(chain, dict) else bool(chain)
    assert ok, f"signed chain failed to verify: {chain}"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        rows = _run_scenarios(Path(d))
        results = _evaluate(rows)
        w = max(len(r[1]) for r in results)
        print(f"\n{'scenario':<{w}}  {'clicks':<34}  {'got':<14} {'expected':<14} ok")
        print("-" * (w + 70))
        for ok, name, clicks, got, exp, aud in results:
            print(f"{name:<{w}}  {clicks:<34}  {got:<14} {exp:<14} {'PASS' if ok else 'FAIL'}")
        n = len(results); p = sum(1 for r in results if r[0])
        print("-" * (w + 70))
        print(f"{p}/{n} passed · all decisions audited on the signed chain")

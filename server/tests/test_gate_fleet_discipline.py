# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fleet discipline for the UI render gates. Three classes of silent failure
died in one day (2026-08-08) and a fourth on 2026-08-19; these assertions keep
them dead.

1. **Explicit success exit.** Every gate .mjs must call ``process.exit(0)``.
   The composed page carries always-on chrome with live refresh intervals, so
   node's event loop never drains on its own: a gate relying on natural exit
   PASSES its assertions and then hangs to its timeout (the approvals gate,
   deterministically, the day the governance strip landed).

2. **No private /tool bridges.** A gate that reads the server does it through
   the page's OWN ``window.tool`` (same auth + prefix logic, throws loudly).
   A hand-rolled fetch with a silent failure mode is how a verdict cross-check
   slept through two builds and a review while reporting green.

3. **No tolerated tool rejections.** The page bridges throw loudly and every
   gate's ``main().catch(fail)`` turns a dead bridge into a failure — UNLESS a
   gate swallows the rejection with a tolerant ``.catch``. A ``window.tool``
   call may only route its rejection to ``fail`` (that is exactly how the
   original asleep cross-check survived: a tolerant guard around a broken
   helper).

4. **Every bridge-using gate proves the bridge is alive.** Rule 3 only armors
   gates that actually CALL ``window.tool``; a gate that reads server-fed
   state purely through the page's own boot has no such signal, because
   ``loadGraph()`` (app/src/index.html) deliberately SWALLOWS a bridge fault
   and returns an empty graph — the honest product behaviour, so an empty
   workspace is drawn empty rather than dressed in the demo. The cost is that
   a dead /tool bridge is indistinguishable from an empty workspace, and a
   gate whose assertions survive an empty render then reports PASS against a
   server it never reached. That is not hypothetical: with a 403-ing /tool,
   ``empty_workspace_render.mjs`` printed its full PASS line (measured
   2026-08-19, A/B against a forwarding proxy that 403s POST /tool). So every
   gate must carry at least ONE liveness signal — a ``window.tool`` call
   (rule 3 forces its rejection to ``fail``) or an explicit
   ``assertBridgeAlive`` (app/harness/rvnd_gate_guards.mjs — the RVND-owned
   file beside the vendored harness), which turns the same dead bridge into a
   named failure. A gate whose SUBJECT is a broken bridge opts out with an
   inline ``// gate-guard: exempt — <reason>`` marker: the exemption lives
   beside the code that needs it, carries its reason, and greps out, instead
   of accumulating in a list nobody rereads.

HONEST RESIDUAL — what this file deliberately does NOT cover: a SUCCESSFUL
but EMPTY result asserted as pass. Some gates legitimately expect empty, so
no fleet-wide rule can exist without lying, and a grep heuristic for
"fetched but never asserted-on" would cry wolf and train reviewers to
ignore this file. That class stays a per-gate REVIEW line-item: where a
value is expected, assert the source non-empty (the I1 lights==board
pattern). An uncovered class named here is honest; one silently assumed
covered is how the last three were born.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE_DIRS = (REPO / "app" / "panels", REPO / "app" / "shell")


def _gate_mjs():
    out = []
    for d in GATE_DIRS:
        if d.is_dir():
            out += sorted(p for p in d.glob("*render*.mjs"))
    assert out, "no render-gate .mjs found — the fleet moved; update GATE_DIRS"
    return out


def test_every_gate_exits_explicitly_on_success():
    missing = [str(p.relative_to(REPO)) for p in _gate_mjs()
               if "process.exit(0)" not in p.read_text(errors="ignore")]
    assert not missing, (
        "gate .mjs without an explicit process.exit(0) — with always-on shell "
        "chrome holding live intervals, these PASS and then hang to timeout:\n  "
        + "\n  ".join(missing))


def test_no_gate_carries_a_private_tool_bridge():
    offenders = []
    for p in _gate_mjs():
        text = p.read_text(errors="ignore")
        if "fetch(`http" in text and "/tool" in text:
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, (
        "gate .mjs hand-rolls a /tool fetch — server reads go through the "
        "page's own window.tool (throws loudly); a private bridge with a "
        "silent failure mode is how a cross-check falls asleep:\n  "
        + "\n  ".join(offenders))


def test_no_gate_tolerates_a_tool_rejection():
    offenders = []
    for p in _gate_mjs():
        lines = p.read_text(errors="ignore").splitlines()
        for i, ln in enumerate(lines):
            if "window.tool(" not in ln:
                continue
            window = "\n".join(lines[i:i + 3])
            if ".catch(" in window and "fail" not in window:
                offenders.append(f"{p.relative_to(REPO)}:{i + 1}")
    assert not offenders, (
        "window.tool rejection swallowed by a tolerant .catch — a dead bridge "
        "or refused op must FAIL the gate, never let a cross-check silently "
        "skip (route the rejection to fail):\n  " + "\n  ".join(offenders))


def test_every_gate_proves_its_bridge_is_alive():
    # loadGraph() swallows a bridge fault by design (an empty workspace must
    # draw empty, not the demo), so page boot alone is NOT evidence the server
    # was reached. A gate with no window.tool call of its own therefore needs
    # the explicit probe, or a 403-ing bridge reads exactly like empty state.
    offenders = []
    for p in _gate_mjs():
        text = p.read_text(errors="ignore")
        if "bridgeGlobals" not in text:
            continue                       # no live bridge to prove
        if "window.tool(" in text or "assertBridgeAlive(" in text:
            continue
        m = re.search(r"//\s*gate-guard:\s*exempt\s*—\s*(\S.*)", text)
        if m and m.group(1).strip():
            continue                       # opted out, with a reason, in place
        offenders.append(str(p.relative_to(REPO)))
    assert not offenders, (
        "gate .mjs wires a live bridge but never proves it is alive — no "
        "window.tool call (rule 3 armor) and no assertBridgeAlive. A dead "
        "/tool bridge is indistinguishable from an empty workspace, so these "
        "can PASS against a server they never reached; call "
        "assertBridgeAlive(window, fail) right after the boot wait:\n  "
        + "\n  ".join(offenders))

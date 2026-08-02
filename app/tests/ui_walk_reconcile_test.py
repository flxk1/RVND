#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""UI-binding walk reconciliation — every ui-supported op is driven by a live gate.

The capability register calls 75 operations ``ui-supported``: reachable from the
console because a control invokes their route. A route existing is not the same
as a control driving it. This gate runs every committed ``app/*_render_test.py``
(each a real jsdom interaction that opens a panel against a live serve.py bridge)
with the bridge instrumented to record every ``(facade, op)`` the page actually
calls, then reconciles that ground truth against the register:

  * a ui-supported op is COVERED when at least one render gate drives it live;
  * the union of live-driven ops must contain every ui-supported op except the
    enumerated ``KNOWN_GAPS`` (ops with a panel but no committed interaction that
    reaches them — each needs a render gate or demotion to deferred);
  * the gap set is pinned both ways: a new uncovered op fails here, and a gap
    that some gate now covers must be moved out of the list.

It also statically checks that governance verdict text is rendered from server
responses (a client only tightens by the disclosed law floor, never recomputes a
verdict). Emits ``docs/evidence/ui-walk-matrix.json`` (op -> covering render gates).

  python3 app/ui_walk_reconcile_test.py        # exit 0 = PASS

Runs the whole render suite as subprocesses (~30s, 8-way). Needs jsdom:
``cd app && npm ci`` if missing.
"""
from __future__ import annotations

import concurrent.futures
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
REGISTER = REPO / "docs" / "evidence" / "capability-register.json"
MATRIX_OUT = REPO / "docs" / "evidence" / "ui-walk-matrix.json"
INDEX = HERE.parent / "src" / "index.html"

# Ops the register calls ui-supported that NO committed render gate drives to the
# bridge. Each has a panel or route but no live interaction reaching this exact
# op — a real coverage gap, not a passing claim. Closing one means adding a
# render gate that drives it (then removing it here) or demoting it to deferred.
#
# This may be empty only when every ui-supported operation is covered. The
# register itself must contain at least one ui-supported operation; otherwise
# there is no UI contract for this test to reconcile and the gate fails.
KNOWN_GAPS: dict[tuple[str, str | None], str] = {}

# Preamble run inside each render-gate subprocess BEFORE the gate's own code:
# import serve and wrap its bridge seam so every (facade, op) the jsdom page POSTs
# to /tool is appended to RVND_TRACE_OPS. The gate later `import serve`s the same
# cached, already-wrapped module, so the trace is the page's real live traffic.
_TRACE_PREAMBLE = (
    "import os,json,serve,runpy\n"
    "_t=os.environ['RVND_TRACE_OPS'];_o=serve._facade_call\n"
    "def _w(tool,args):\n"
    " try:op=(args or {}).get('op')\n"
    " except Exception:op=None\n"
    " try:\n"
    "  open(_t,'a').write(json.dumps({'tool':tool,'op':op})+chr(10))\n"
    " except Exception:pass\n"
    " return _o(tool,args)\n"
    "serve._facade_call=_w\n"
)


def _run_gate(test_path: str, trace_dir: Path) -> tuple[str, bool, str]:
    name = Path(test_path).stem
    trace = trace_dir / f"{name}.jsonl"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERE.parent), str(REPO / "server" / "src"), env.get("PYTHONPATH", "")])
    env["RVND_TRACE_OPS"] = str(trace)
    env.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
    code = _TRACE_PREAMBLE + f"runpy.run_path({test_path!r}, run_name='__main__')\n"
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           env=env, capture_output=True, text=True, timeout=90)
        ok = r.returncode == 0 and "PASS" in r.stdout
        return name, ok, (r.stdout + r.stderr).strip()[-200:]
    except subprocess.TimeoutExpired:
        return name, False, "TIMEOUT"


def _collect(trace_dir: Path) -> dict:
    """(facade, op) -> sorted list of render gates that drove it live."""
    cover: dict[tuple[str, str | None], set[str]] = {}
    for f in glob.glob(str(trace_dir / "*.jsonl")):
        gate = Path(f).stem
        for line in Path(f).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            cover.setdefault((d["tool"], d["op"]), set()).add(gate)
    return {k: sorted(v) for k, v in cover.items()}


def _static_verdict_provenance() -> list[str]:
    """Static evidence that panels render the SERVER's verdict, not a client
    recomputation. Returns a list of failures (empty = pass)."""
    html = INDEX.read_text()
    fails = []
    # the client discloses when it shows the server verdict and when it tightens
    if "server said" not in html:
        fails.append("no 'server said …, tightened by the law floor' disclosure in index.html")
    if "no server verdict yet" not in html:
        fails.append("no fail-closed 'no server verdict yet' default disclosure in index.html")
    # the sole client-side verdict transform is the disclosed law-floor tightener
    if "function resolveEgressVerdict" not in html:
        fails.append("resolveEgressVerdict (the disclosed law-floor tightener) is missing")
    # verdict/finding text is read from response object fields, not fabricated
    reads = sum(html.count(p) for p in (".verdict", ".verdicts", ".decision", ".findings"))
    if reads < 20:
        fails.append(f"too few server-field verdict reads ({reads}); verdicts may be client-computed")
    return fails


def main() -> int:
    ui_ops = [(o["facade"], o["op"])
              for o in json.loads(REGISTER.read_text())["operations"]
              if o["status"] == "ui-supported"]
    if not ui_ops:
        print("FAIL: capability register has no ui-supported operations; "
              "UI reconciliation would be vacuous")
        return 1
    gates = sorted(g for g in glob.glob(str(HERE.parent / "shell" / "*_render_test.py")) + glob.glob(str(HERE.parent / "panels" / "*_render_test.py")))
    if not gates:
        print("FAIL: no render gates found")
        return 1

    trace_dir = Path(tempfile.mkdtemp(prefix="uiwalk_"))
    results: dict[str, tuple[bool, str]] = {}
    # Render gates mutate process-wide environment variables and several import
    # the same server modules. Running them concurrently lets one gate redirect
    # another gate's log/key roots, producing false missing-record failures.
    # Serialize the gates so every trace is attributable to its own isolated
    # bridge and temporary workspace.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        futs = [ex.submit(_run_gate, g, trace_dir) for g in gates]
        for fut in concurrent.futures.as_completed(futs):
            name, ok, tail = fut.result()
            results[name] = (ok, tail)

    broken = [f"{n}: {t}" for n, (ok, t) in sorted(results.items()) if not ok]
    cover = _collect(trace_dir)

    covered = sorted([f"{f}|{o}" for (f, o) in ui_ops if (f, o) in cover])
    uncovered = [(f, o) for (f, o) in ui_ops if (f, o) not in cover]

    # machine-readable matrix: op -> covering render gates (or [] if a gap)
    matrix = {
        "schema": "ui-walk-matrix-1",
        "ui_supported": len(ui_ops),
        "covered": len(covered),
        "gaps": len(uncovered),
        "render_gates_run": len(gates),
        "render_gates_broken": broken,
        "rows": [{"facade": f, "op": o,
                  "covered_by": cover.get((f, o), []),
                  "gap_reason": KNOWN_GAPS.get((f, o)) if (f, o) not in cover else None}
                 for (f, o) in ui_ops],
    }
    MATRIX_OUT.write_text(json.dumps(matrix, indent=1) + "\n")

    unexpected_gaps = [(f, o) for (f, o) in uncovered if (f, o) not in KNOWN_GAPS]
    healed = [k for k in KNOWN_GAPS if k not in {(f, o) for (f, o) in uncovered}]
    prov_fails = _static_verdict_provenance()

    print(f"render gates: {len(gates)} run, {len(broken)} broken")
    print(f"ui-supported ops: {len(ui_ops)}  covered live: {len(covered)}  gaps: {len(uncovered)}")
    print("KNOWN GAPS (need a render gate or demotion):")
    for (f, o) in uncovered:
        print(f"  - {f} | {o}  :: {KNOWN_GAPS.get((f, o), 'UNDOCUMENTED')}")

    ok = True
    if broken:
        ok = False
        print(f"FAIL: {len(broken)} render gate(s) did not pass — trace is not trustworthy:")
        for b in broken:
            print(f"  {b}")
    if unexpected_gaps:
        ok = False
        print(f"FAIL: {len(unexpected_gaps)} ui-supported op(s) uncovered and NOT in KNOWN_GAPS "
              "(add a render gate or demote to deferred):")
        for (f, o) in unexpected_gaps:
            print(f"  {f} | {o}")
    if healed:
        ok = False
        print(f"FAIL: {len(healed)} KNOWN_GAPS op(s) are now covered — remove them from KNOWN_GAPS:")
        for (f, o) in healed:
            print(f"  {f} | {o} covered by {cover.get((f, o))}")
    if prov_fails:
        ok = False
        print("FAIL: verdict-provenance static scan:")
        for m in prov_fails:
            print(f"  {m}")

    if ok:
        print(f"PASS: {len(covered)}/{len(ui_ops)} ui-supported ops driven by a live render gate; "
              f"{len(uncovered)} documented gap(s) pinned; verdicts render from server responses; "
              f"matrix -> {MATRIX_OUT.relative_to(REPO)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

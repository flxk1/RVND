#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Run Rvnd's Loomground engine against the standard's conformance vectors.

This is the gate for "Rvnd uses the language": it loads the vectors published in
loomground/conformance/vectors/ and checks Rvnd's parser / validator / token
checker / transport evaluator reproduce each vector's expected result.

Usage:
    python3 conformance/run_conformance.py [path-to-loomground/conformance]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# make `workspaces` importable from this script's location (rvnd/server/src)
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from workspaces import loomground_lang as L  # noqa: E402


def _find_conformance(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    # Vectors are dev/CI only and not bundled; resolve a live Loomground checkout
    # (LOOMGROUND_ROOT or a sibling) via the engine's asset bridge.
    from workspaces import loomground_assets  # noqa: PLC0415

    return loomground_assets.conformance_dir()


def _input(vdir: Path) -> Path:
    """The vector's netlist. Canonical extension is `.lg`; `.loom` is accepted as
    the deprecated pre-0.6.1 alias (the standard's reader rule)."""
    lg = vdir / "input.lg"
    return lg if lg.exists() else vdir / "input.loom"


def _check_patch(vdir: Path) -> tuple[bool, str]:
    patch = L.parse(_input(vdir).read_text())
    v = L.validate(patch)
    if not v["ok"]:
        return False, f"unexpected validation errors: {v['errors']}"
    obs = L.project(patch)
    expected = json.loads((vdir / "expected.json").read_text())
    if obs != expected:
        return False, f"projection != expected\n  got: {json.dumps(obs)}\n  exp: {json.dumps(expected)}"
    tpath = vdir / "transport.json"
    if tpath.exists():
        transport = json.loads(tpath.read_text())
        got = L.evaluate(patch, transport)
        exp = transport.get("expected", {})
        if got != exp:
            return False, f"transport != expected\n  got: {json.dumps(got)}\n  exp: {json.dumps(exp)}"
        # §7.4: the ordered log trace — one entry per activated gate, in evaluation
        # order, carrying the gate's effective verdict; misordering is a failure
        exp_log = transport.get("log")
        if exp_log is not None:
            got_log = L.evaluate_log(patch, transport)
            if got_log != exp_log:
                return False, f"log trace != expected\n  got: {json.dumps(got_log)}\n  exp: {json.dumps(exp_log)}"
    return True, "ok"


def _check_negative(vdir: Path) -> tuple[bool, str]:
    reject = json.loads((vdir / "reject.json").read_text())
    stage = reject["stage"]
    text = _input(vdir).read_text()
    if stage == "parse":
        try:
            L.parse(text)
        except L.ParseError:
            return True, "ok (parse rejected)"
        return False, "expected a parse-stage rejection, got none"
    else:  # apply
        try:
            patch = L.parse(text)
        except L.ParseError as e:
            return False, f"expected apply-stage rejection but failed at parse: {e}"
        v = L.validate(patch)
        if v["ok"]:
            return False, "expected apply-stage rejection, validate() said ok"
        return True, f"ok (apply rejected: {v['errors'][0]})"


def _check_token(vdir: Path) -> tuple[bool, str]:
    cases = json.loads((vdir / "tokens.json").read_text())
    for i, case in enumerate(cases):
        got = L.validate_token(case["token"])
        if got != case["valid"]:
            return False, f"token[{i}]: got valid={got}, expected {case['valid']}"
    return True, f"ok ({len(cases)} tokens)"


def main(argv: list[str]) -> int:
    conf = _find_conformance(argv)
    manifest = json.loads((conf / "manifest.json").read_text())
    vectors = manifest["vectors"]
    npass = 0
    fails: list[str] = []
    for vec in vectors:
        name, kind = vec["name"], vec["kind"]
        vdir = conf / "vectors" / name
        try:
            if kind == "patch":
                ok, msg = _check_patch(vdir)
            elif kind == "negative":
                ok, msg = _check_negative(vdir)
            elif kind == "token":
                ok, msg = _check_token(vdir)
            else:
                ok, msg = False, f"unknown kind {kind}"
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"exception: {type(e).__name__}: {e}"
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name:28s} ({kind})  {msg if not ok else ''}".rstrip())
        if ok:
            npass += 1
        else:
            fails.append(name)
    total = len(vectors)
    print(f"\n{npass}/{total} conformance vectors pass" + (" — ALL GREEN" if npass == total else ""))
    if fails:
        print("FAILED:", ", ".join(fails))
    return 0 if npass == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

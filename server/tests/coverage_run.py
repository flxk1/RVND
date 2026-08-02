# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Isolated driver for the executable coverage matrix.

Driving 108 operations mutates process-global state (env, module caches, the
request principal). To keep that out of the rest of the test suite, the matrix
is built in THIS module run as a subprocess (see test_capability_coverage.py),
not at import time in the test process. Run:

    python server/tests/coverage_run.py <out.json>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import atexit
import shutil
import tempfile

# Bind to the in-tree source, not a possibly-stale installed copy. The coverage
# matrix proves this repository's source callable; a non-editable site-packages
# install can lag the tree (e.g. omit the loomground data bundle) and make ops
# spuriously uncallable. Prepend server/src so `import workspaces` resolves here
# regardless of how the subprocess was launched.
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Root the ENTIRE harness in one disposable directory BEFORE importing
# workspaces. The workspace registry, key dir, and log root all derive from
# $HOME at import time (LOG_ROOT_DEFAULT = ~/.workspace/log), so a harness that
# registers disposable folders would otherwise pollute the user's real registry
# and leave dead `ws` entries in the live console selector. Redirecting $HOME
# here — before the first `import workspaces` freezes those paths — confines all
# state to a temp dir that is removed on exit (success or failure). The launching
# test additionally asserts the real registry is byte-identical before/after.
_HARNESS_HOME: str | None = None
if __name__ == "__main__":
    # Only the executable subprocess owns a second home.  This module is also
    # imported by test_capability_coverage for its declarative OBLIGATIONS; an
    # import must never replace pytest's already-isolated HOME for the rest of
    # the test process.
    _HARNESS_HOME = tempfile.mkdtemp(prefix="cov_harness_home_")
    os.environ["HOME"] = _HARNESS_HOME
    os.environ["WORKSPACE_KEY_DIR"] = str(Path(_HARNESS_HOME) / "keys")
    os.environ["WORKSPACE_L0_LOG_ROOT"] = str(Path(_HARNESS_HOME) / "log")

    @atexit.register
    def _cleanup_harness_home() -> None:
        assert _HARNESS_HOME is not None
        shutil.rmtree(_HARNESS_HOME, ignore_errors=True)

# The folder allowlist (WORKSPACES_ALLOW_UNREGISTERED) is orthogonal to the
# identity/principal gate this harness exercises: it lives only in
# folder_context, never in the request-principal path. Disposable coverage
# workspaces are unregistered by construction, so the harness sets it here and
# does not depend on the launching process's environment (conftest sets the
# same value for the pytest process).
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

try:
    from .coverage_harness import CHANNELS, as_unmapped_principal, is_refusal, is_success, new_workspace, empty_result
    from .coverage_fixtures import DEFAULT, FIXTURES, SURFACE_FIXTURES
except ImportError:  # top-level (subprocess): server/tests on sys.path
    from coverage_harness import CHANNELS, as_unmapped_principal, is_refusal, is_success, new_workspace, empty_result
    from coverage_fixtures import DEFAULT, FIXTURES, SURFACE_FIXTURES

REPO = Path(__file__).resolve().parents[2]
REGISTER = json.loads((REPO / "docs" / "evidence" / "capability-register.json").read_text())["operations"]


def _portable_reason(prefix: str, value: object, *roots: Path, limit: int = 140) -> str:
    """Render evidence without host-specific repository or temporary paths."""
    rendered = str(value).replace(str(REPO), "<REPO>")
    for root in roots:
        rendered = rendered.replace(str(root), "<COVERAGE_WORKSPACE>")
    return f"{prefix}{rendered}"[:limit]


def claimed_channels(e) -> set[str]:
    """The real surfaces this op is reachable on: the console (surfaced), the
    curated gateway, and the MCP dispatch (proven by the presence of a coverage
    fixture that drives its op route). Callability is what a supported claim
    rests on — driving it green here is the evidence, not the route_tested grep."""
    ch: set[str] = set()
    if e["surface_state"] == "surfaced":
        ch.add("ui")
    if e["gateway"]:
        ch.add("gateway")
    if (e["facade"], e["op"]) in FIXTURES:
        ch.add("mcp")
    return ch


# every op that claims at least one real surface is driven; ops with none stay
# deferred without a matrix row (nothing to prove)
OBLIGATIONS = [(e["facade"], e["op"], c) for e in REGISTER for c in sorted(claimed_channels(e))]

def _fixture(facade, op, channel):
    if channel != "mcp" and (facade, op) in SURFACE_FIXTURES:
        return SURFACE_FIXTURES[(facade, op)]
    return FIXTURES.get((facade, op), DEFAULT)


def drive(facade, op, channel) -> dict:
    fx = _fixture(facade, op, channel)
    invoke = CHANNELS[channel]
    row = {"facade": facade, "op": op, "channel": channel,
           "valid_ok": False, "invalid_ok": False, "reason": ""}

    valid_resp = None
    ws = new_workspace()
    try:
        ctx = fx.setup(ws)
        valid_resp = invoke(facade, op, fx.valid(ws, ctx))
        row["valid_ok"] = is_success(valid_resp) and fx.check(valid_resp)
        if not row["valid_ok"]:
            row["reason"] = _portable_reason(
                "valid not success: ", valid_resp, ws.root
            )
    except Exception as e:
        row["reason"] = _portable_reason(
            f"valid raised {type(e).__name__}: ", e, ws.root
        )

    # Fail-closed invalid handling (no channel borrows another channel's refusal;
    # a missing invalid is not a silent pass; a raw exception is not a refusal).
    if fx.invalid is None:
        # An op with no invalid case must carry a reviewed statement that it has
        # no invalid input domain; otherwise its refusal contract is unproven.
        row["invalid_ok"] = bool(fx.no_invalid_domain)
        if not row["invalid_ok"] and not row["reason"]:
            row["reason"] = "no invalid fixture and no reviewed no_invalid_domain statement"
    else:
        ws2 = new_workspace()
        check = fx.invalid_check or is_refusal
        try:
            ctx2 = fx.setup(ws2)
            params2 = fx.invalid(ws2, ctx2)
            if fx.invalid_unmapped:
                with as_unmapped_principal():
                    invalid_resp = invoke(facade, op, params2)
            else:
                invalid_resp = invoke(facade, op, params2)
            # a refusal must be a controlled RETURNED response, judged by check
            row["invalid_ok"] = check(invalid_resp)
            if not row["invalid_ok"] and not row["reason"]:
                row["reason"] = _portable_reason(
                    "invalid not refused: ", invalid_resp, ws2.root
                )
            # anti-vacuous guard: an unmapped-principal refusal must be
            # distinguishable from the mapped call — either the mapped call carried
            # data or the unmapped call hard-refused. Both empty successes prove nothing.
            if row["invalid_ok"] and fx.invalid_unmapped:
                valid_has_data = row["valid_ok"] and not empty_result(valid_resp)
                invalid_hard = is_refusal(invalid_resp)
                if not (valid_has_data or invalid_hard):
                    row["invalid_ok"] = False
                    row["reason"] = _portable_reason(
                        "vacuous unmapped refusal: valid and invalid both empty "
                        "successes, principal not distinguished: ",
                        invalid_resp,
                        ws2.root,
                    )
        except Exception as e:
            # a raw exception (TypeError, crash, infra failure) is NOT a controlled
            # refusal — fail closed, do not count it as a pass
            row["invalid_ok"] = False
            if not row["reason"]:
                row["reason"] = _portable_reason(
                    f"invalid raised {type(e).__name__} "
                    "(not a controlled refusal): ",
                    e,
                    ws2.root,
                    limit=130,
                )
    return row


def build_matrix() -> list[dict]:
    return [drive(f, o, c) for (f, o, c) in OBLIGATIONS]


def deferred_leaks() -> list[str]:
    """An op the register does NOT flag gateway-exposed must not be reachable over
    the gateway dispatch. (Under the strict basis every op is deferred, so the
    meaningful invariant is exposure vs the declared gateway profile, not status.)

    This crosses only the private gateway dispatch — a LOWER BOUND, not a
    full-surface exposure reconciliation across MCP discovery, HTTP/UI,
    CLI/chat help, and gateway discovery."""
    off_profile = [e for e in REGISTER if not e["gateway"] and e["op"] is not None]
    leaks = []
    ws = new_workspace()
    for e in off_profile:
        try:
            r = CHANNELS["gateway"](e["facade"], e["op"], {"folder_context": ws.folder})
            if is_success(r):
                leaks.append(f"{e['facade']}/{e['op']} reachable over gateway but not in its profile")
        except Exception:
            pass
    return leaks


def main() -> int:
    rows = build_matrix()
    leaks = deferred_leaks()
    doc = {"schema": "coverage-matrix-1", "obligations": len(OBLIGATIONS),
           "deferred_leaks": leaks,
           "rows": sorted(rows, key=lambda r: (r["facade"], r["op"] or "", r["channel"]))}
    out = sys.argv[1] if len(sys.argv) > 1 else str(REPO / "docs" / "evidence" / "capability-coverage-matrix.json")
    Path(out).write_text(json.dumps(doc, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Behaviour tests for the offline floor tools (bin/rvnd-*).

These tools run with nothing installed. The tests assert BEHAVIOUR on foreign
and adversarial input, and — most importantly — the two honesty invariants that
make an advisory floor safe to ship:

  * never silently upgrade: the floor never emits an enforced/granted/verified
    verdict. `authoritative` is always False; stdout never contains grant words.
  * never silently downgrade: when a check cannot run, the tool says so rather
    than reporting a pass.

Tools are invoked as real subprocesses so exit codes and the stderr mode line
are exercised exactly as a caller would see them.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "rvnd-governance"
BIN = PLUGIN / "bin"
LIB = PLUGIN / "scripts" / "rvnd_floor_lib.py"

# Words that would mean the floor claimed authority it does not have.
_FORBIDDEN_ON_STDOUT = ("enforced", "granted", "grant ", "signed by", "binding decision:")


def _run(tool, payload=None, args=()):
    argv = [sys.executable, str(BIN / tool), *args]
    p = subprocess.run(
        argv,
        input=None if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout, p.stderr


def _assert_honest(stdout, stderr):
    """The two invariants that every floor result must satisfy."""
    assert "mode: advisory" in stderr, f"missing advisory mode line: {stderr!r}"
    low = stdout.lower()
    for word in _FORBIDDEN_ON_STDOUT:
        assert word not in low, f"floor stdout claimed authority via {word!r}: {stdout!r}"
    if stdout.strip():
        assert json.loads(stdout)["authoritative"] is False


# --------------------------------------------------------------------------
# rvnd-preview
# --------------------------------------------------------------------------
def _preview(lane, request):
    return _run("rvnd-preview", {"lane": lane, "request": request})


def test_preview_clean_request_allows():
    code, out, err = _preview(
        {"grade_rank": 2, "actions": ["read", "summarise"], "scope": {"purpose": None}},
        {"grade_rank": 2, "action": "summarise", "scope": {"purpose": "research"}},
    )
    assert code == 0
    assert json.loads(out)["verdict"] == "allow"
    _assert_honest(out, err)


def test_preview_grade_increase_denies():
    code, out, err = _preview(
        {"grade_rank": 1, "actions": ["read"], "scope": {}},
        {"grade_rank": 3, "action": "read", "scope": {}},
    )
    assert code == 4
    payload = json.loads(out)
    assert payload["verdict"] == "deny"
    assert any("grade increase" in r for r in payload["reasons"])
    _assert_honest(out, err)


def test_preview_action_not_in_allowlist_denies():
    code, out, err = _preview(
        {"grade_rank": 2, "actions": ["read"], "scope": {}},
        {"grade_rank": 2, "action": "delete", "scope": {}},
    )
    assert code == 4
    assert json.loads(out)["verdict"] == "deny"
    _assert_honest(out, err)


def test_preview_missing_scope_denies():
    code, out, err = _preview(
        {"grade_rank": 2, "actions": ["read"], "scope": {"dataset": None}},
        {"grade_rank": 2, "action": "read", "scope": {}},
    )
    assert code == 4
    assert any("missing scope" in r for r in json.loads(out)["reasons"])
    _assert_honest(out, err)


def test_preview_unrankable_grade_holds_never_allows():
    # Grades as opaque tokens with the engine absent: the lattice is not ours to
    # guess, so the floor must HOLD — never fabricate an allow.
    code, out, err = _preview(
        {"grade_rank": "L2", "actions": ["read"], "scope": {}},
        {"grade_rank": "L2", "action": "read", "scope": {}},
    )
    assert code == 3, f"expected HOLD, got exit {code}: {out}"
    payload = json.loads(out)
    assert payload["verdict"] == "hold"
    assert payload["verdict"] != "allow"
    assert any("lattice" in r or "not rankable" in r for r in payload["reasons"])
    _assert_honest(out, err)


def test_preview_malformed_is_fail_closed():
    for bad in ("not json", json.dumps([1, 2, 3]), json.dumps({"lane": {}})):
        p = subprocess.run(
            [sys.executable, str(BIN / "rvnd-preview")],
            input=bad, capture_output=True, text=True,
        )
        assert p.returncode == 2, f"malformed input should fail closed: {bad!r}"


# --------------------------------------------------------------------------
# rvnd-verify
# --------------------------------------------------------------------------
def test_verify_good_linkage_ok():
    chain = [
        {"hash": "h0", "prev_hash": None},
        {"hash": "h1", "prev_hash": "h0"},
        {"hash": "h2", "prev_hash": "h1"},
    ]
    code, out, err = _run("rvnd-verify", chain)
    assert code == 0
    payload = json.loads(out)
    assert payload["linkage_ok"] is True
    assert payload["entries"] == 3
    _assert_honest(out, err)


def test_verify_broken_linkage_is_tamper_evident():
    chain = [
        {"hash": "h0", "prev_hash": None},
        {"hash": "h1", "prev_hash": "TAMPERED"},
    ]
    code, out, err = _run("rvnd-verify", chain)
    assert code == 5
    payload = json.loads(out)
    assert payload["linkage_ok"] is False
    assert payload["broken_at_entry"] == 1
    _assert_honest(out, err)


def test_verify_never_claims_unchecked_signature_passed():
    # A garbage signature must NEVER be reported as passed: either the verifier
    # is absent (reported "NOT checked") or present (reported as a failure).
    entry = {"hash": "h0", "prev_hash": None, "public_key": "aa" * 32,
             "signature": "00" * 64, "body": "payload"}
    code, out, err = _run("rvnd-verify", entry)
    payload = json.loads(out)
    notes = " ".join(payload["notes"]).lower()
    claimed_pass = "signature: checked 1/1" in notes and "failed" not in notes
    assert not claimed_pass, f"floor claimed a garbage signature passed: {payload}"
    _assert_honest(out, err)


def test_verify_malformed_is_fail_closed():
    for bad in ("not json", json.dumps([]), json.dumps({"chain": {}})):
        p = subprocess.run(
            [sys.executable, str(BIN / "rvnd-verify")],
            input=bad, capture_output=True, text=True,
        )
        assert p.returncode == 2, f"malformed input should fail closed: {bad!r}"


# --------------------------------------------------------------------------
# rvnd-lint launcher (wiring + fail-closed reuse of the canonical linter)
# --------------------------------------------------------------------------
def test_lint_launcher_forwards_and_fails_closed_on_garbage():
    p = subprocess.run(
        [sys.executable, str(BIN / "rvnd-lint")],
        input="not json at all", capture_output=True, text=True,
    )
    assert p.returncode != 0


def test_lint_launcher_rejects_request_shown_as_grant():
    # A surface card with a granted status is exactly what the linter exists to
    # reject; the launcher must propagate that non-zero exit.
    card = {"card": {"id": "x", "status": "granted"}}
    p = subprocess.run(
        [sys.executable, str(BIN / "rvnd-lint")],
        input=json.dumps(card), capture_output=True, text=True,
    )
    assert p.returncode != 0


# --------------------------------------------------------------------------
# rvnd-probe
# --------------------------------------------------------------------------
def test_probe_reports_a_decision_shape():
    code, out, err = _run("rvnd-probe", args=("--json",))
    assert code == 0
    payload = json.loads(out)
    assert set(payload) >= {"present", "version", "compatible", "reason"}
    assert isinstance(payload["present"], bool)


# --------------------------------------------------------------------------
# op-drift tripwire: the lib's compatible range must equal the plugin's
# declared runtime.requires. If they diverge, adaptivity targets the wrong
# engine — fail loudly here.
# --------------------------------------------------------------------------
def test_compat_range_matches_declared_runtime():
    pkg = json.loads((PLUGIN / "package.json").read_text())
    requires = pkg["runtime"]["requires"]
    spec = next(r for r in requires if r.startswith("rvnd"))
    m = re.search(r">=\s*([0-9.]+)\s*,\s*<\s*([0-9.]+)", spec)
    assert m, f"could not parse rvnd requirement: {spec!r}"
    lo = tuple(int(x) for x in m.group(1).split("."))
    hi = tuple(int(x) for x in m.group(2).split("."))

    src = LIB.read_text()
    lib_lo = eval(re.search(r"ENGINE_MIN\s*=\s*(\([^)]*\))", src).group(1))
    lib_hi = eval(re.search(r"ENGINE_MAX_EXCL\s*=\s*(\([^)]*\))", src).group(1))
    assert lib_lo == lo, f"lib ENGINE_MIN {lib_lo} != declared {lo}"
    assert lib_hi == hi, f"lib ENGINE_MAX_EXCL {lib_hi} != declared {hi}"

"""Behaviour tests for rvnd-attest — the boundary attestation (plugin version).

The load-bearing invariant: the plugin/offline version fills only DECLARED fields
and leaves EVERY authoritative field null. It must never fabricate a boundary
RVND alone can attest — not for any input.
"""
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "rvnd"
BIN = PLUGIN / "bin"
SCHEMA = PLUGIN / "schemas" / "boundary-attestation.schema.json"
EXAMPLES = PLUGIN / "references" / "examples"

_AUTH_FIELDS = ("ok", "summary", "sessions", "leases", "chain", "certificates", "reconciliation")


def _attest(doc):
    p = subprocess.run(
        [sys.executable, str(BIN / "rvnd-attest")],
        input=json.dumps(doc), capture_output=True, text=True,
    )
    return p.returncode, p.stdout, p.stderr


def test_plugin_version_leaves_all_authoritative_fields_null():
    code, out, err = _attest({
        "boundary": {"scope": "/x/"},
        "declared": {"provider": "anthropic", "models": ["claude-opus-4-8"],
                     "user": "u@example.com", "tokens": {"input": 10, "output": 5}},
    })
    assert code == 0
    doc = json.loads(out)
    assert doc["mode"] == "advisory-plugin"
    # the invariant: every authoritative field is null
    for f in _AUTH_FIELDS:
        assert doc["authoritative"][f] is None, f"plugin version filled authoritative.{f}"
    # declared IS carried, marked not-verified
    assert doc["declared"]["provider"] == "anthropic"
    assert doc["declared"]["attested_not_verified"] is True
    assert "mode: advisory" in err


def test_authoritative_never_filled_for_any_input():
    # Even if the caller tries to smuggle authoritative data in, it is ignored.
    code, out, _ = _attest({
        "boundary": {"scope": "/x/"},
        "declared": {"provider": "openai"},
        "authoritative": {"boundaries_kept": {"permit": 999}, "chain": {"signature": "forged"}},
    })
    assert code == 0
    doc = json.loads(out)
    for f in _AUTH_FIELDS:
        assert doc["authoritative"][f] is None, f"authoritative.{f} accepted from caller input"


def test_missing_boundary_scope_is_fail_closed():
    code, _, _ = _attest({"declared": {"provider": "anthropic"}})
    assert code == 2


def test_both_reference_examples_validate_against_the_one_schema():
    import jsonschema  # dev dep
    schema = json.loads(SCHEMA.read_text())
    for name in ("boundary-attestation.plugin.json", "boundary-attestation.rvnd.json"):
        jsonschema.validate(json.loads((EXAMPLES / name).read_text()), schema)


def test_plugin_example_has_null_authoritative_rvnd_example_does_not():
    plugin = json.loads((EXAMPLES / "boundary-attestation.plugin.json").read_text())
    rvnd = json.loads((EXAMPLES / "boundary-attestation.rvnd.json").read_text())
    assert all(plugin["authoritative"][f] is None for f in _AUTH_FIELDS)
    assert all(rvnd["authoritative"][f] is not None for f in _AUTH_FIELDS)
    assert plugin["mode"] == "advisory-plugin" and rvnd["mode"] == "authoritative-rvnd"

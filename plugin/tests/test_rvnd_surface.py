"""Behaviour tests for the build-a-surface linter.

The linter is deterministic, offline, and FAIL-CLOSED. It must accept honest
surfaces/proposals and reject anything that could show a request as a grant or
smuggle an invented construct past validation. Critically, the structural floor
must enforce on its own with no jsonschema (RVND_LINT_NO_JSONSCHEMA=1), so every
structural case is run twice: once normally, once forcing the dependency-absent
path.
"""
import hashlib, json, os, subprocess, sys
from pathlib import Path

LINT = (Path(__file__).resolve().parents[1] / "rvnd" / "skills"
        / "build-a-surface" / "scripts" / "lint_surface.py")


def _run(payload, no_js=False):
    env = dict(os.environ)
    if no_js:
        env["RVND_LINT_NO_JSONSCHEMA"] = "1"
    p = subprocess.run([sys.executable, str(LINT)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def _both(payload):
    """Return (normal, no-jsonschema) results so structural floor is proven."""
    return _run(payload), _run(payload, no_js=True)


# -- cards / compositions ---------------------------------------------------

def test_valid_composition_passes():
    rc, out, err = _run({
        "name": "govern-flow", "server": "rvnd",
        "skills": ["govern-an-action"],
        "cards": ["context", "proposal", "patch", "decision", "receipt"],
        "fail_closed": True, "human_confirmation": True})
    assert rc == 0, err
    assert json.loads(out)["valid"] is True


def test_proposal_without_receipt_fails_closed():
    for rc, _, err in _both({
            "name": "bad", "server": "rvnd", "skills": ["govern-an-action"],
            "cards": ["context", "proposal", "patch"], "fail_closed": True}):
        assert rc == 1 and "receipt" in err


def test_non_fail_closed_composition_rejected():
    for rc, _, err in _both({"name": "leaky", "server": "rvnd",
                             "skills": ["verify-a-receipt"], "cards": ["context"],
                             "fail_closed": False}):
        assert rc == 1 and "fail_closed" in err


def test_proposal_card_granted_vocabulary_rejected():
    for rc, _, err in _both({"card": "proposal", "step": "propose",
                             "reads": ["propose"], "renders": ["x"],
                             "status_vocabulary": ["requested", "granted"],
                             "forbids_scores": True, "attributed": True}):
        assert rc == 1 and "granted" in err.lower()


def test_ratification_decision_card_passes():
    rc, out, err = _run({"card": "decision", "step": "confirm", "mode": "ratification",
                         "reads": ["approval_decide"], "renders": ["determinate verdict"],
                         "decision_vocabulary": ["approve", "deny"]})
    assert rc == 0, err
    assert json.loads(out)["kind"] == "card"


def test_residual_origination_card_with_approve_rejected():
    for rc, _, err in _both({"card": "decision", "step": "confirm",
                             "mode": "residual-origination", "reads": ["approval_request"],
                             "renders": ["unranked alternatives"],
                             "decision_vocabulary": ["approve", "alternative-a"]}):
        assert rc == 1 and "approve" in err.lower()


def test_residual_origination_card_unranked_passes():
    rc, out, err = _run({"card": "decision", "step": "confirm",
                         "mode": "residual-origination", "reads": ["approval_request"],
                         "renders": ["unranked alternatives"], "alternatives_min": 2,
                         "decision_vocabulary": ["alternative-a", "alternative-b"]})
    assert rc == 0, err


# -- proposal envelope ------------------------------------------------------

def _proposal(**over):
    p = {
        "proposal_id": "p1",
        "intent": {"text": "Maria must approve external publication",
                   "actor": "user_17", "host": "chat"},
        "scope": {"boundary_id": "bnd_press_kit", "members": ["folder:press-kit"]},
        "loomground": {"language_version": "0.8.2", "deltas": [
            {"operation": "add", "construct": {
                "type": "reservation", "kind": "external-publication",
                "by": "accountable-publisher"}}]},
        "residual": [],
        "validation": {"well_formed": True, "applyable": True},
        "versions": {"loomground_governance": "0.8.2"},
        "confirmation": {"required": True},
    }
    p.update(over)
    return p


def test_valid_proposal_passes():
    rc, out, err = _run(_proposal())
    assert rc == 0, err
    assert json.loads(out)["kind"] == "proposal"


def test_proposal_valid_under_structural_floor_only():
    rc, out, err = _run(_proposal(), no_js=True)
    assert rc == 0, err


def test_invented_construct_rejected_both_paths():
    bad = _proposal(loomground={"language_version": "0.8.2", "deltas": [
        {"operation": "add", "construct": {"type": "responsibly"}}]})
    for rc, _, err in _both(bad):
        assert rc == 1 and ("real typed construct" in err or "not a real" in err)


def test_arbitrary_construct_property_rejected():
    bad = _proposal(loomground={"language_version": "0.8.2", "deltas": [
        {"operation": "add", "construct": {"type": "reservation", "kind": "k",
                                           "by": "r", "invented": "x"}}]})
    for rc, _, err in _both(bad):
        assert rc == 1 and ("additionalProperties" in err or "invented" in err)


def test_bad_node_class_rejected_both_paths():
    bad = _proposal(loomground={"language_version": "0.8.2", "patch": {
        "nodes": [{"id": "a", "class": "robot"}], "cords": []}})
    for rc, _, err in _both(bad):
        assert rc == 1 and "robot" in err


def test_guard_domain_violation_rejected():
    bad = _proposal(loomground={"language_version": "0.8.2", "deltas": [
        {"operation": "add", "construct": {"type": "reservation", "kind": "k",
         "by": "r", "when": {"field": "id", "op": "=", "value": "x"}}}]})
    for rc, _, err in _both(bad):
        assert rc == 1 and "id" in err


def test_well_formed_false_but_applyable_rejected():
    for rc, _, err in _both(_proposal(validation={"well_formed": False, "applyable": True})):
        assert rc == 1 and "applyable" in err


def test_missing_applyable_rejected():
    for rc, _, err in _both(_proposal(validation={"well_formed": True})):
        assert rc == 1 and "applyable" in err


def test_missing_version_rejected_both_paths():
    for rc, _, err in _both(_proposal(versions={})):
        assert rc == 1 and "loomground_governance" in err


def test_missing_residual_rejected():
    bad = _proposal(); del bad["residual"]
    for rc, _, err in _both(bad):
        assert rc == 1 and "residual" in err


# -- standalone delta / observation -----------------------------------------

def test_valid_delta_passes():
    rc, out, err = _run({"operation": "remove", "construct": {
        "type": "cord", "from": "actor:researcher", "to": "gate:publish",
        "cord_type": "authority"}})
    assert rc == 0, err
    assert json.loads(out)["kind"] == "delta"


def test_bad_cord_type_rejected():
    for rc, _, err in _both({"operation": "add", "construct": {
            "type": "cord", "from": "a", "to": "b", "cord_type": "wire"}}):
        assert rc == 1 and "wire" in err


def test_valid_observation_passes():
    rc, out, err = _run({"nodes": [{"id": "g", "class": "gate"}],
                         "cords": [], "reservations": []})
    assert rc == 0, err
    assert json.loads(out)["kind"] == "observation"


def test_unknown_shape_fails_closed():
    rc, _, err = _run({"nonsense": True})
    assert rc == 2 and "classify" in err


def test_policy_workflow_skills_ship_as_mcp_drivers():
    package_root = Path(__file__).resolve().parents[1] / "rvnd"
    package = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    expected = {
        "onboard-a-policy",
        "govern-an-action",
        "resolve-a-conflict",
        "sign-off",
    }
    declared = {skill["name"] for skill in package["skills"]}
    assert expected <= declared
    for name in expected:
        skill_root = package_root / "skills" / name
        assert (skill_root / "SKILL.md").is_file()
        assert (skill_root / "manifest.yaml").is_file()
        assert (skill_root / "references" / "eval.json").is_file()
        assert not (skill_root / "scripts").exists()
        evaluation = json.loads((skill_root / "references" / "eval.json").read_text(encoding="utf-8"))
        assert evaluation["wraps_kernel"] is False
        assert evaluation["fail_closed"] is True


def test_package_metadata_matches_release_contract():
    plugin_root = Path(__file__).resolve().parents[1]
    package_root = plugin_root / "rvnd"
    package = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (plugin_root / "schemas" / "loomground-package.schema.json").read_text(
            encoding="utf-8"
        )
    )
    import jsonschema

    jsonschema.validate(package, schema)
    assert package["license"] == "AGPL-3.0-only"
    assert package["runtime"]["requires"] == ["rvnd>=0.6.8.4,<0.7"]
    for capability in ("rvnd.govern-an-action", "rvnd.sign-off", "governance.resolve-conflict"):
        assert package["capabilities"][capability] == {
            "mode": "read-write",
            "humanConfirmation": True,
        }


def test_multi_host_plugin_manifests_are_coherent():
    package_root = Path(__file__).resolve().parents[1] / "rvnd"
    package = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    codex = json.loads(
        (package_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude = json.loads(
        (package_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    root_mcp = json.loads((package_root / ".mcp.json").read_text(encoding="utf-8"))
    generic_mcp = json.loads(
        (package_root / "mcp" / "rvnd.mcp.json").read_text(encoding="utf-8")
    )

    for manifest in (codex, claude):
        assert manifest["name"] == package["name"]
        assert manifest["version"] == package["version"]
        assert manifest["license"] == package["license"]
    assert codex["skills"] == "./skills/"
    assert codex["mcpServers"] == "./.mcp.json"

    root_server = root_mcp["mcpServers"]["rvnd"]
    generic_server = generic_mcp["mcpServers"]["rvnd"]
    for field in ("command", "args", "env", "transport"):
        assert root_server[field] == generic_server[field]

    marketplace = json.loads(
        (package_root.parents[1] / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    repository_plugin = json.loads(
        (package_root.parents[1] / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    entry = marketplace["plugins"][0]
    assert marketplace["name"] == "rvnd"
    assert entry["name"] == package["name"]
    assert entry["version"] == package["version"]
    assert entry["source"] == "./plugin/rvnd"
    assert repository_plugin["name"] == package["name"]
    assert repository_plugin["version"] == package["version"]
    assert repository_plugin["skills"] == "./plugin/rvnd/skills/"
    assert repository_plugin["mcpServers"] == "./plugin/rvnd/.mcp.json"

    descriptor = json.loads(
        (package_root / "mcp" / "rvnd.mcp.json").read_text(encoding="utf-8")
    )["mcpServers"]["rvnd"]
    assert descriptor["command"] == "python3"
    assert descriptor["args"] == ["-m", "rvnd.mcp_server"]
    assert "PYTHONPATH" not in descriptor.get("env", {})


def test_vendored_governance_schemas_match_0_8_2_hashes():
    schema_root = (
        Path(__file__).resolve().parents[1]
        / "rvnd"
        / "schemas"
        / "loomground"
    )
    expected = {
        "patch.schema.json": "665e002ecb8453df69e2714d59a52f78667eb50de24731158a1a7659469841f2",
        "observation.schema.json": "56f52e99c0c4d7cb89a6c71bd470990541e8426fd1ef5b03212ce45ef12d53d5",
        "token.schema.json": "ec141eac8cfaecbcb0c3d9ac1abb548aebfb44fd972955b507d8dab69fa0284b",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((schema_root / name).read_bytes()).hexdigest() == digest

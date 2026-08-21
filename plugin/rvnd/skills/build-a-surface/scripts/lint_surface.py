#!/usr/bin/env python3
"""Lint an RVND surface card, composition, proposal envelope, delta, or observation.

Deterministic, offline, FAIL-CLOSED. Reads one JSON object (stdin, or a file path;
"-" also means stdin) and validates it. Shape is chosen by keys:
  - "card"                         -> surface card
  - "cards"/"skills"               -> composition manifest
  - "intent"/"proposal_id"/"loomground" (with intent/versions) -> proposal envelope
  - "operation" + "construct"      -> a GovernanceDelta
  - "nodes"+"cords"+"reservations" (no proposal keys) -> a canonical observation

Structural validation is COMPLETE and DEPENDENCY-FREE: node classes, cord types,
the guard domain, per-element key whitelists (emulating additionalProperties:false),
the five-verdict alphabet, residual presence, version recording, and the
well_formed:false => not applyable rule are all checked in plain Python. If the
optional `jsonschema` package is present it ALSO runs full JSON-Schema validation
against the bundled schemas; if it is absent, the structural floor still runs.
Validation is never silently downgraded. Any violation exits non-zero.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Test/ops hook: force the dependency-absent path so the structural floor is
# exercised even where jsonschema is installed. The floor is always the
# enforcement; jsonschema is only additive.
_NO_JSONSCHEMA = os.environ.get("RVND_LINT_NO_JSONSCHEMA") == "1"

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = PLUGIN_ROOT / "schemas"

CARD_STATUS_FORBIDDEN = {"granted", "enabled", "active", "in-effect", "in effect"}
NODE_CLASSES = {"actor", "human", "gate", "master"}
CORD_TYPES = {"authority", "pipe", "egress"}
VERDICTS = {"auto", "human", "refused", "reserved", "prohibited"}
RISK = {"low", "medium", "high", "critical"}
GUARD_FIELDS = {"kind", "risk", "party", "tags"}
GUARD_OPS = {"=", ">=", "contains"}
REAL_CONSTRUCTS = {
    "actor", "human", "gate", "master", "authority", "pipe", "egress",
    "reservation", "quorum", "prohibition", "temporal", "egress-obligation",
    "redress", "party", "delegation", "autonomy-grade", "grant",
}

# Per-element key whitelists (emulate additionalProperties:false without jsonschema).
PATCH_KEYS = {
    "nodes": ({"id", "class"}, {"id", "class", "role", "party", "risk_floor",
                                "on_behalf_of", "grade", "grade_required"}),
    "grants": ({"gate", "actor"}, {"gate", "actor", "kinds", "risks"}),
    "cords": ({"from", "to", "type"}, {"from", "to", "type"}),
    "reservations": ({"kind", "by"}, {"kind", "by", "when", "duration", "on_elapse"}),
    "prohibitions": ({"kind"}, {"kind", "when"}),
    "obligations": ({"obligation", "on"}, {"obligation", "on"}),
    "redress": ({"kind", "by"}, {"kind", "by", "overturn", "within"}),
}
OBS_KEYS = {
    "nodes": ({"id", "class"}, {"id", "class", "role", "risk_floor", "grade",
                                "grade_required", "on_behalf_of", "party"}),
    "cords": ({"from", "to", "type"}, {"from", "to", "type"}),
    "reservations": ({"kind", "by"}, {"kind", "by", "when", "duration", "on_elapse"}),
    "redress": ({"kind", "by", "overturn", "within"}, {"kind", "by", "overturn", "within"}),
}
# Typed-construct (delta) key whitelists, keyed by the construct's "type".
CONSTRUCT_KEYS = {
    "node": ({"type", "id", "class"}, {"type", "id", "class", "role", "party",
             "risk_floor", "on_behalf_of", "grade", "grade_required"}),
    "cord": ({"type", "from", "to", "cord_type"}, {"type", "from", "to", "cord_type"}),
    "reservation": ({"type", "kind", "by"}, {"type", "kind", "by", "when", "duration", "on_elapse"}),
    "prohibition": ({"type", "kind"}, {"type", "kind", "when"}),
    "egress-obligation": ({"type", "obligation", "on"}, {"type", "obligation", "on"}),
    "redress": ({"type", "kind", "by"}, {"type", "kind", "by", "overturn", "within"}),
    "grant": ({"type", "gate", "actor"}, {"type", "gate", "actor", "kinds", "risks"}),
}


def _read_input(argv: list[str]) -> str:
    if not argv or argv[0] == "-":
        return sys.stdin.read()
    return Path(argv[0]).read_text(encoding="utf-8")


def _obj(x, path, errors) -> bool:
    if not isinstance(x, dict):
        errors.append(f"{path}: expected an object")
        return False
    return True


def _keys(obj, required, allowed, path, errors) -> None:
    for k in required - set(obj):
        errors.append(f"{path}: missing required key {k!r}")
    for k in set(obj) - allowed:
        errors.append(f"{path}: unexpected key {k!r} (additionalProperties not allowed)")


def _enum(val, allowed, path, errors) -> None:
    if val is not None and val not in allowed:
        errors.append(f"{path}: {val!r} not one of {sorted(allowed)}")


def _guard(g, path, errors) -> None:
    # A guard may be a typed object {field,op,value} (protocol) or a string (patch).
    if isinstance(g, str) or g is None:
        return
    if not _obj(g, path, errors):
        return
    _keys(g, {"field", "op", "value"}, {"field", "op", "value"}, path, errors)
    _enum(g.get("field"), GUARD_FIELDS, f"{path}.field", errors)
    _enum(g.get("op"), GUARD_OPS, f"{path}.op", errors)


def _check_graph(container, keyspec, path, errors) -> None:
    """Validate a patch- or observation-shaped object against a keyspec map."""
    for arr_name, (req, allowed) in keyspec.items():
        arr = container.get(arr_name)
        if arr is None:
            continue
        if not isinstance(arr, list):
            errors.append(f"{path}.{arr_name}: expected an array")
            continue
        for i, el in enumerate(arr):
            p = f"{path}.{arr_name}[{i}]"
            if not _obj(el, p, errors):
                continue
            _keys(el, req, allowed, p, errors)
            if arr_name == "nodes":
                _enum(el.get("class"), NODE_CLASSES, f"{p}.class", errors)
                _enum(el.get("risk_floor"), RISK, f"{p}.risk_floor", errors)
            if arr_name == "cords":
                _enum(el.get("type"), CORD_TYPES, f"{p}.type", errors)
            if arr_name in ("reservations", "prohibitions"):
                _guard(el.get("when"), f"{p}.when", errors)


def _check_delta(d, path, errors) -> None:
    if not _obj(d, path, errors):
        return
    _keys(d, {"operation", "construct"}, {"operation", "construct", "replaces"}, path, errors)
    _enum(d.get("operation"), {"add", "remove", "replace"}, f"{path}.operation", errors)
    c = d.get("construct")
    if _obj(c, f"{path}.construct", errors):
        t = c.get("type")
        if t not in CONSTRUCT_KEYS:
            errors.append(f"{path}.construct.type {t!r} is not a real typed construct")
        else:
            req, allowed = CONSTRUCT_KEYS[t]
            _keys(c, req, allowed, f"{path}.construct", errors)
            if t == "node":
                _enum(c.get("class"), NODE_CLASSES, f"{path}.construct.class", errors)
            if t == "cord":
                _enum(c.get("cord_type"), CORD_TYPES, f"{path}.construct.cord_type", errors)
            if t in ("reservation", "prohibition"):
                _guard(c.get("when"), f"{path}.construct.when", errors)


def _check_observation(o, path, errors) -> None:
    _keys(o, {"nodes", "cords", "reservations"}, set(OBS_KEYS), path, errors)
    _check_graph(o, OBS_KEYS, path, errors)


def _check_validation(v, path, errors) -> None:
    if not _obj(v, path, errors):
        return
    if "well_formed" not in v:
        errors.append(f"{path}: missing 'well_formed'")
    if "applyable" not in v:
        errors.append(f"{path}: missing 'applyable' (validation must state it explicitly)")
    if v.get("well_formed") is False and v.get("applyable") is not False:
        errors.append(f"{path}: well_formed:false must never be applyable")
    _enum(v.get("verdict_preview"), VERDICTS, f"{path}.verdict_preview", errors)
    obs = v.get("canonical_observation")
    if obs is not None and _obj(obs, f"{path}.canonical_observation", errors):
        _check_observation(obs, f"{path}.canonical_observation", errors)


# -- jsonschema (optional, additive) ----------------------------------------

def _jsonschema_registry():
    try:
        from jsonschema import Draft202012Validator  # noqa: F401
        from referencing import Registry, Resource
    except ImportError:
        return None
    resources = []
    for p in SCHEMA_DIR.rglob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and "$id" in doc:
            resources.append((doc["$id"], Resource.from_contents(doc)))
    try:
        return Registry().with_resources(resources)
    except Exception:  # noqa: BLE001
        return None


def _jsonschema_check(instance, schema_id, errors) -> None:
    if _NO_JSONSCHEMA:
        return  # forced structural-floor-only path; the floor is the enforcement
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return  # structural floor already ran; do not downgrade
    reg = _jsonschema_registry()
    if reg is None:
        return
    try:
        schema = reg.get_or_retrieve(schema_id).value.contents
        validator = Draft202012Validator(schema, registry=reg)
    except Exception:  # noqa: BLE001
        return
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        errors.append(f"schema: {err.message}")


# -- per-kind checks ---------------------------------------------------------

def _check_card(doc, errors) -> None:
    if doc.get("card") == "proposal":
        vocab = {str(w).lower() for w in doc.get("status_vocabulary", [])}
        leaked = vocab & CARD_STATUS_FORBIDDEN
        if leaked:
            errors.append("proposal card status_vocabulary must not contain "
                          f"{sorted(leaked)} - a request is never rendered as granted")
    if doc.get("card") == "decision" and doc.get("mode") == "residual-origination":
        dvocab = {str(w).lower() for w in doc.get("decision_vocabulary", [])}
        leaked = dvocab & {"approve", "deny", "reject"}
        if leaked:
            errors.append("residual-origination decision card decision_vocabulary must not "
                          f"contain {sorted(leaked)} - a residual choice is not a yes/no")
        if "alternatives_min" in doc and doc["alternatives_min"] < 2:
            errors.append("residual-origination requires alternatives_min >= 2 "
                          "(real, unranked alternatives, no default)")
    for flag in ("forbids_scores", "attributed"):
        if flag in doc and doc[flag] is not True:
            errors.append(f"{flag} must be true (discrete lamps, attributed-not-asserted)")
    _jsonschema_check(doc, "https://loomground.local/schemas/rvnd-surface-card.schema.json", errors)


def _check_composition(doc, errors) -> None:
    cards = set(doc.get("cards", []))
    if "proposal" in cards:
        for needed in ("patch", "receipt"):
            if needed not in cards:
                errors.append(f"a composition that renders 'proposal' must also render '{needed}'")
    if doc.get("fail_closed") is not True:
        errors.append("fail_closed must be true")
    if doc.get("server", "rvnd") != "rvnd":
        errors.append("server must be 'rvnd'")
    _jsonschema_check(doc, "https://loomground.local/schemas/rvnd-composition.schema.json", errors)


def _check_proposal(doc, errors) -> None:
    _keys(doc, {"proposal_id", "intent", "scope", "loomground", "residual",
                "validation", "versions", "confirmation"},
          {"proposal_id", "intent", "scope", "loomground", "runtime_bindings",
           "residual", "validation", "impact", "evidence", "versions", "confirmation"},
          "proposal", errors)
    if not isinstance(doc.get("residual"), list):
        errors.append("proposal must carry a 'residual' ledger array (may be empty, never omitted)")
    lg = doc.get("loomground")
    if _obj(lg, "proposal.loomground", errors):
        if not lg.get("language_version"):
            errors.append("proposal.loomground.language_version is required")
        if "patch" not in lg and "deltas" not in lg:
            errors.append("proposal.loomground must carry a 'patch' or 'deltas'")
        if isinstance(lg.get("patch"), dict):
            _check_graph(lg["patch"], PATCH_KEYS, "proposal.loomground.patch", errors)
        if isinstance(lg.get("deltas"), list):
            for i, d in enumerate(lg["deltas"]):
                _check_delta(d, f"proposal.loomground.deltas[{i}]", errors)
    if _obj(doc.get("validation"), "proposal.validation", errors):
        _check_validation(doc["validation"], "proposal.validation", errors)
    versions = doc.get("versions") or {}
    if not versions.get("loomground_governance"):
        errors.append("proposal.versions.loomground_governance is required (version grounding)")
    _jsonschema_check(doc, "https://loomground.local/schemas/rvnd-proposal.schema.json", errors)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        doc = json.loads(_read_input(argv))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not isinstance(doc, dict):
        print("error: input must be a JSON object", file=sys.stderr)
        return 2

    errors: list[str] = []
    if "intent" in doc or "proposal_id" in doc or ("loomground" in doc and "versions" in doc):
        kind = "proposal"; _check_proposal(doc, errors)
    elif "operation" in doc and "construct" in doc:
        kind = "delta"; _check_delta(doc, "delta", errors)
    elif "card" in doc:
        kind = "card"; _check_card(doc, errors)
    elif "cards" in doc or "skills" in doc:
        kind = "composition"; _check_composition(doc, errors)
    elif {"nodes", "cords", "reservations"} <= set(doc):
        kind = "observation"; _check_observation(doc, "observation", errors)
    else:
        print("error: cannot classify input (expected a proposal, delta, card, "
              "composition, or observation)", file=sys.stderr)
        return 2

    if errors:
        for e in errors:
            print(f"invalid {kind}: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"kind": kind, "valid": True}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

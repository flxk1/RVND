# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for per-folder pinned-skills (#145, piece 1+2).

Covers:
- pin / unpin / list (idempotence, atomic save, idempotent re-pin)
- resolve_skills_for_query: asymmetric walk (self + ancestors only)
- resolve_skills_for_query: keyword filter
- resolve_skills_for_query: inherited_from provenance
- Empty store + missing file gracefully handled
- include_ancestors=False short-circuits the walk
"""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from workspaces import pinned_skills as pinned_module

from workspaces.pinned_skills import (
    PinnedSkillStore,
    list_pinned,
    pin_skill,
    resolve_skills_for_query,
    unpin_skill,
    load_pinned_skills,
)


def test_empty_store_for_unpinned_folder(tmp_path):
    """A folder with no pinning file returns an empty store, no exception."""
    fc = tmp_path / "wks"
    fc.mkdir()
    store = load_pinned_skills(str(fc), log_root=tmp_path / ".log")
    assert isinstance(store, PinnedSkillStore)
    assert store.skills == []


def test_pin_and_list_roundtrip(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    store = pin_skill(str(fc), "plugin-a:skill-x",
                       pinned_by="alex", note="for the AI gov work",
                       log_root=log_root)
    assert len(store.skills) == 1
    assert store.skills[0].id == "plugin-a:skill-x"
    assert store.skills[0].pinned_by == "alex"
    assert store.skills[0].note == "for the AI gov work"

    # list_pinned reads back the same data
    pinned = list_pinned(str(fc), log_root=log_root)
    assert len(pinned) == 1
    assert pinned[0].id == "plugin-a:skill-x"


def test_pin_is_idempotent(tmp_path):
    """Re-pinning the same id is a no-op for count but updates metadata."""
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    pin_skill(str(fc), "p:s", pinned_by="a", log_root=log_root)
    store = pin_skill(str(fc), "p:s", pinned_by="b", note="updated",
                       log_root=log_root)
    assert len(store.skills) == 1
    assert store.skills[0].pinned_by == "b"
    assert store.skills[0].note == "updated"


def test_unpin_returns_removed_flag(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    pin_skill(str(fc), "p:s", log_root=log_root)
    store, removed = unpin_skill(str(fc), "p:s", log_root=log_root)
    assert removed is True
    assert store.skills == []

    # Unpinning again is OK, returns removed=False
    store, removed = unpin_skill(str(fc), "p:s", log_root=log_root)
    assert removed is False


def test_pin_rejects_empty_skill_id(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    with pytest.raises(ValueError):
        pin_skill(str(fc), "", log_root=tmp_path / ".log")
    with pytest.raises(ValueError):
        pin_skill(str(fc), None, log_root=tmp_path / ".log")  # type: ignore


def test_resolve_self_only(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    pin_skill(str(fc), "p:s", log_root=log_root)
    out = resolve_skills_for_query(str(fc), log_root=log_root,
                                    include_ancestors=False)
    assert out["folder_context"] == str(fc.resolve())
    assert [s["id"] for s in out["skills"]] == ["p:s"]
    assert out["chain"] == [str(fc.resolve())]


def test_resolve_walks_ancestors_asymmetric(tmp_path):
    """Children inherit ancestor pins. Siblings + descendants do NOT
    contribute to a folder's resolved set."""
    parent = tmp_path / "parent"
    child = parent / "child"
    sibling = tmp_path / "sibling"
    grandchild = child / "grandchild"
    for d in (parent, child, sibling, grandchild):
        d.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".log"

    pin_skill(str(parent), "from-parent",  log_root=log_root)
    pin_skill(str(child),  "from-child",   log_root=log_root)
    pin_skill(str(sibling), "from-sibling", log_root=log_root)
    pin_skill(str(grandchild), "from-grandchild", log_root=log_root)

    # Child should see: from-parent (inherited), from-child. NOT from-sibling
    # (sibling), NOT from-grandchild (descendant).
    out = resolve_skills_for_query(str(child), log_root=log_root)
    ids = sorted(s["id"] for s in out["skills"])
    assert "from-parent"     in ids
    assert "from-child"      in ids
    assert "from-sibling"    not in ids
    assert "from-grandchild" not in ids


def test_resolve_inherited_from_metadata(tmp_path):
    """Inherited entries carry the ancestor folder in inherited_from."""
    parent = tmp_path / "parent"
    child = parent / "child"
    for d in (parent, child):
        d.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".log"

    pin_skill(str(parent), "from-parent", log_root=log_root)
    pin_skill(str(child),  "from-child",  log_root=log_root)

    out = resolve_skills_for_query(str(child), log_root=log_root)
    by_id = {s["id"]: s for s in out["skills"]}
    # Inherited from parent
    assert str(parent.resolve()) in by_id["from-parent"]["inherited_from"]
    # Own pin — inherited_from is empty
    assert by_id["from-child"]["inherited_from"] == ""


def test_resolve_query_filter_substring(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    pin_skill(str(fc), "ai-governance-watch:newsletter-research",
              log_root=log_root)
    pin_skill(str(fc), "legal-first-aid:nda-specialist", log_root=log_root)
    pin_skill(str(fc), "workspace:workspace-policy", log_root=log_root)

    out = resolve_skills_for_query(str(fc), query="newsletter",
                                    log_root=log_root)
    assert [s["id"] for s in out["skills"]] == \
           ["ai-governance-watch:newsletter-research"]

    out = resolve_skills_for_query(str(fc), query="POLICY",  # case-insensitive
                                    log_root=log_root)
    assert [s["id"] for s in out["skills"]] == ["workspace:workspace-policy"]


def test_resolve_empty_when_no_matches(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    pin_skill(str(fc), "p:s", log_root=log_root)
    out = resolve_skills_for_query(str(fc), query="nonexistent",
                                    log_root=log_root)
    assert out["skills"] == []


def test_resolve_returns_chain(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    grandchild = child / "grandchild"
    for d in (parent, child, grandchild):
        d.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".log"

    out = resolve_skills_for_query(str(grandchild), log_root=log_root)
    # chain[0] is self; chain extends upward through ancestors
    assert out["chain"][0] == str(grandchild.resolve())
    assert str(child.resolve())  in out["chain"]
    assert str(parent.resolve()) in out["chain"]


def test_corrupt_file_raises(tmp_path):
    """A corrupt JSON file is loud — we don't silently lose user data."""
    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    # Pin one to create the file
    pin_skill(str(fc), "p:s", log_root=log_root)
    # Corrupt it
    from workspaces.pinned_skills import _store_path
    sp = _store_path(str(fc), log_root=log_root)
    sp.write_text("this is not json")

    import json
    with pytest.raises(json.JSONDecodeError):
        load_pinned_skills(str(fc), log_root=log_root)


def test_companion_catalogue_loads(tmp_path):
    """The shipped catalogue file (under plugin/references/) loads cleanly."""
    from workspaces.pinned_skills import load_companion_catalogue
    cat = load_companion_catalogue()
    # Either the file is found and has families, or it isn't (dev variant).
    # In the repo layout it should be present.
    assert isinstance(cat, dict)
    if cat:
        assert "families" in cat
        assert isinstance(cat["families"], dict)


def _reset_companion_cache():
    pinned_module._COMPANION_CATALOGUE_CACHE = None
    pinned_module._COMPANION_CATALOGUE_MTIME = None
    pinned_module._COMPANION_CATALOGUE_PATH = None
    pinned_module._COMPANION_CATALOGUE_LAST_INTEGRITY = None


def test_companion_catalogue_cache_cannot_bypass_enforce(tmp_path, monkeypatch):
    catalogue = tmp_path / "skill-companions.json"
    catalogue.write_text(json.dumps({"version": 1, "families": {}}))
    monkeypatch.setattr(pinned_module, "_candidate_catalogue_paths", lambda: [catalogue])
    _reset_companion_cache()

    monkeypatch.setenv("WORKSPACE_CATALOGUE_MODE", "warn")
    assert pinned_module.load_companion_catalogue()["version"] == 1
    monkeypatch.setenv("WORKSPACE_CATALOGUE_MODE", "enforce")
    assert pinned_module.load_companion_catalogue() == {}


def test_companion_catalogue_verifier_exception_fails_closed_in_enforce(
    tmp_path, monkeypatch,
):
    from workspaces import catalogue_integrity

    catalogue = tmp_path / "skill-companions.json"
    catalogue.write_text(json.dumps({"version": 1, "families": {}}))
    monkeypatch.setattr(pinned_module, "_candidate_catalogue_paths", lambda: [catalogue])
    monkeypatch.setattr(
        catalogue_integrity, "verify_catalogue",
        lambda _data: (_ for _ in ()).throw(RuntimeError("verification unavailable")),
    )
    monkeypatch.setenv("WORKSPACE_CATALOGUE_MODE", "enforce")
    _reset_companion_cache()
    assert pinned_module.load_companion_catalogue() == {}


def test_suggest_companions_finds_siblings(tmp_path):
    from workspaces.pinned_skills import suggest_companions
    # AI gov family is a high-confidence family in the shipped catalogue
    r = suggest_companions("ai-governance-watch:newsletter-research")
    if not r["family"]:
        pytest.skip("companion catalogue not shipped in this dev layout")
    assert r["family"] == "ai-governance-watch"
    # The seed skill itself must be excluded
    assert "ai-governance-watch:newsletter-research" not in r["companions"]
    # And the family must have at least one other entry
    assert len(r["companions"]) >= 1


def test_suggest_companions_excludes_already_pinned(tmp_path):
    from workspaces.pinned_skills import suggest_companions
    seed = "ai-governance-watch:newsletter-research"
    sibling = "ai-governance-watch:newsletter-pattern-scan"
    r = suggest_companions(seed, exclude=[sibling])
    if not r["family"]:
        pytest.skip("companion catalogue not shipped in this dev layout")
    assert sibling not in r["companions"]


def test_suggest_companions_unknown_family_returns_empty():
    from workspaces.pinned_skills import suggest_companions
    r = suggest_companions("totally-unknown:nonsense-skill")
    assert r["family"] == ""
    assert r["companions"] == []


def test_record_dispatch_writes_event(tmp_path):
    """record_dispatch persists a 'skill-dispatch' event in the folder's log."""
    from workspaces.pinned_skills import record_dispatch
    from workspaces.mutation_log import MutationLog

    fc = tmp_path / "wks"
    fc.mkdir()
    log_root = tmp_path / ".log"

    out = record_dispatch(
        str(fc), "ai-governance-watch:newsletter-research",
        query="What does the AI Act say about GPAI",
        chosen_via="dashboard",
        actor="alex",
        log_root=log_root,
    )
    assert out["skill_id"] == "ai-governance-watch:newsletter-research"
    assert out["chosen_via"] == "dashboard"

    # Read the mutation log back and find the dispatch event
    log = MutationLog(str(fc), log_root=log_root)
    events = list(log.replay())
    dispatches = [e for e in events
                  if e.pair_id == "skill-dispatch"
                  and (e.extra or {}).get("dispatch") == "skill"]
    assert len(dispatches) == 1
    assert dispatches[0].extra["skill_id"] == "ai-governance-watch:newsletter-research"
    assert dispatches[0].extra["chosen_via"] == "dashboard"
    assert dispatches[0].actor == "alex"


def test_record_dispatch_rejects_empty_skill_id(tmp_path):
    from workspaces.pinned_skills import record_dispatch
    fc = tmp_path / "wks"; fc.mkdir()
    with pytest.raises(ValueError):
        record_dispatch(str(fc), "   ", log_root=tmp_path / ".log")


def test_resolver_tolerates_corrupt_ancestor(tmp_path):
    """resolve_skills_for_query swallows corrupt-ancestor JSON so a
    bad parent file doesn't kill the child's resolve."""
    parent = tmp_path / "parent"
    child = parent / "child"
    for d in (parent, child):
        d.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".log"

    # Pin in parent + child, then corrupt parent's file
    pin_skill(str(parent), "from-parent", log_root=log_root)
    pin_skill(str(child),  "from-child",  log_root=log_root)
    from workspaces.pinned_skills import _store_path
    _store_path(str(parent), log_root=log_root).write_text("not json")

    # Resolve from child still returns child's own pin
    out = resolve_skills_for_query(str(child), log_root=log_root)
    ids = [s["id"] for s in out["skills"]]
    assert "from-child" in ids
    # from-parent silently dropped because parent's file is corrupt
    assert "from-parent" not in ids

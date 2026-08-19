# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A skill body we cannot read must not dispatch uncapped.

`dispatch_skill` feeds a skill's self-declared grade ceiling into the oversight
chokepoint, and already fails closed when a body is found but cannot be PARSED —
clamping to L0 so it "must not dispatch UNCAPPED".

The reader had the opposite behaviour for a strictly worse case. A SKILL.md that
exists but cannot be READ — permissions, or bytes that are not UTF-8 — was
swallowed and returned None, which the caller cannot distinguish from "not a
Workspace skill, the host resolves it". A body declaring an L2 cap therefore
dispatched with no cap at all. Same ignorance as an unparseable body, opposite
outcome.

Three states, kept distinct:
  * no Workspace body        -> None      -> no ceiling from us (host's job)
  * body present, readable   -> the body  -> its declared ceiling
  * body present, unreadable -> raises    -> caller clamps to L0
"""
from __future__ import annotations

import importlib.util

import pytest

from rvnd.mcp_impl import SkillBodyUnreadable, _try_read_workspace_skill_body

ART22 = ("---\nname: probe\n---\n\nThe data subject shall not be subject to a decision "
         "based solely on automated processing which produces legal effects.\n")


@pytest.fixture
def skill_dir(tmp_path, monkeypatch):
    """Point the reader's candidate search at a temporary tree."""
    pkg = tmp_path / "runtime" / "src" / "rvnd"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *a, **kw):
        if name == "workspaces":
            return importlib.util.spec_from_file_location(
                "workspaces", str(pkg / "__init__.py"))
        return real_find_spec(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    d = tmp_path / "runtime" / "plugin" / "skills" / "probe"
    d.mkdir(parents=True)
    return d


def test_a_readable_body_is_returned(skill_dir):
    (skill_dir / "SKILL.md").write_text(ART22, encoding="utf-8")
    body = _try_read_workspace_skill_body("probe")
    assert body is not None and "solely on automated processing" in body


def test_an_absent_body_is_none_not_an_error(skill_dir):
    """No Workspace body is a legitimate answer — the host resolves it."""
    assert _try_read_workspace_skill_body("probe") is None


def test_a_body_that_exists_but_cannot_be_decoded_raises(skill_dir):
    """The defect: this used to return None, and the declared cap vanished."""
    (skill_dir / "SKILL.md").write_bytes(ART22.encode("utf-16"))
    assert (skill_dir / "SKILL.md").exists()
    with pytest.raises(SkillBodyUnreadable):
        _try_read_workspace_skill_body("probe")


def test_the_declared_ceiling_survives_a_readable_body(skill_dir):
    from rvnd.oversight_compose import compose_facets
    from rvnd.oversight_extractor import extract_oversight
    (skill_dir / "SKILL.md").write_text(ART22, encoding="utf-8")
    body = _try_read_workspace_skill_body("probe")
    assert compose_facets(extract_oversight(body)).grade_ceiling == "L2"


def test_unreadable_and_unparseable_fail_the_same_way(skill_dir):
    """The point of the fix: one ignorance, one outcome. Both clamp to L0."""
    def resolve(skill_id: str) -> str:
        from rvnd.oversight_compose import compose_facets
        from rvnd.oversight_extractor import extract_oversight
        try:
            body = _try_read_workspace_skill_body(skill_id)
        except SkillBodyUnreadable:
            return "L0"
        if not body:
            return ""
        try:
            return compose_facets(extract_oversight(body)).grade_ceiling or ""
        except Exception:                                   # noqa: BLE001
            return "L0"

    (skill_dir / "SKILL.md").write_bytes(ART22.encode("utf-16"))
    assert resolve("probe") == "L0", "an unreadable body must not dispatch uncapped"

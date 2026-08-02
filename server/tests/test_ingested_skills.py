# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the universal-skill-adapter store (ingested_skills).

Hermetic — no network, no model. Uses a tmp workspace folder so the
on-disk object lands under ``<tmp>/.workspace/skills/<uid>/``. Signing falls
back to the operator key, which ``ensure_keypair`` generates on demand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import ingested_skills as isk


VALID_SKILL = (
    "---\n"
    "name: sync-deal-triage\n"
    "description: 'Triage a sync licensing offer and flag the load-bearing terms.'\n"
    "---\n\n"
    "# Sync deal triage\n\n"
    "1. Pull fee, term, territory, exclusivity.\n"
    "2. Flag perpetual or all-media grants.\n"
)


def test_content_uid_is_stable_and_edit_sensitive():
    a = isk.content_uid(VALID_SKILL)
    b = isk.content_uid(VALID_SKILL.replace("exclusivity.\n", "exclusivity.   \n"))  # line-trailing ws
    assert a == b, "line-trailing whitespace must normalise to the same uid"
    c = isk.content_uid(VALID_SKILL.replace("Triage", "Review"))
    assert c != a, "a real edit must produce a new uid"


def test_validate_accepts_clean_skill():
    assert isk.validate_skill(VALID_SKILL) == []


@pytest.mark.parametrize("mutator,needle", [
    # Rule 1: description too long
    (lambda s: s.replace("'Triage a sync licensing offer and flag the load-bearing terms.'",
                         "'" + "x" * 1100 + "'"), "1024 limit"),
    # Rule 2: angle brackets
    (lambda s: s.replace("flag the load-bearing terms.",
                         "flag <placeholder> terms."), "angle brackets"),
    # Rule 3: folded scalar
    (lambda s: s.replace("description: 'Triage a sync licensing offer and flag the load-bearing terms.'",
                         "description: |\n  Triage a sync offer."), "folded scalar"),
    # Rule 4: body horizontal rule
    (lambda s: s.replace("# Sync deal triage", "# Sync deal triage\n\n---"),
     "horizontal rule"),
    # Rule 5: frontmatter that does not parse under strict YAML
    (lambda s: s.replace("'Triage a sync licensing offer and flag the load-bearing terms.'",
                         "@reserved-indicator breaks yaml"), "valid YAML"),
])
def test_validate_rejects_each_failure_mode(mutator, needle):
    failures = isk.validate_skill(mutator(VALID_SKILL))
    assert any(needle in f for f in failures), f"expected '{needle}' in {failures}"


def test_validate_accepts_unquoted_single_line_description():
    # Real Anthropic skills use unquoted single-line descriptions, often with
    # embedded double-quotes. These are valid and must pass.
    md = ('---\nname: orchestrator\n'
          'description: This skill should be used when the user asks to "audit this" '
          'or "is this high-risk" — route to the right expert.\n'
          '---\n\n# orchestrator\n\nRoute the artifact.\n')
    assert isk.validate_skill(md) == []


def test_ingest_prose_produces_valid_signed_object(tmp_path: Path):
    res = isk.ingest(tmp_path, "Quick way to estimate streams to break even on a release cost.",
                     source_format="prose", author="alex",
                     license="proprietary", monetization_model="attribution")
    assert res["ok"], res
    uid = res["uid"]
    obj_dir = tmp_path / ".workspace" / "skills" / uid
    assert (obj_dir / "skill.md").exists()
    assert (obj_dir / "manifest.json").exists()
    assert (obj_dir / "signature.json").exists()
    man = res["manifest"]
    assert man["skill_id"].startswith("user:alex/")
    assert man["source_format"] == "prose"
    assert man["ownership"]["monetization_model"] == "attribution"
    # The stored body must itself pass validation.
    assert isk.validate_skill(res["body"]) == []


def test_ingest_is_idempotent(tmp_path: Path):
    src = VALID_SKILL
    a = isk.ingest(tmp_path, src, source_format="anthropic-skill")
    b = isk.ingest(tmp_path, src, source_format="anthropic-skill")
    assert a["ok"] and b["ok"]
    assert a["uid"] == b["uid"]
    assert any("idempotent" in w for w in b["warnings"])


def test_ingest_rejects_bad_skill(tmp_path: Path):
    bad = VALID_SKILL.replace("flag the load-bearing terms.", "flag <x> terms.")
    res = isk.ingest(tmp_path, bad, source_format="anthropic-skill")
    assert not res["ok"]
    assert any("angle brackets" in f for f in res["failures"])


def test_find_by_skill_id_and_dispatch_body_is_populated(tmp_path: Path):
    res = isk.ingest(tmp_path, VALID_SKILL, source_format="anthropic-skill",
                     skill_id="user:alex/sync-deal-triage")
    obj = isk.find_by_skill_id(tmp_path, "user:alex/sync-deal-triage")
    assert obj is not None
    # This is the body=None gap being closed: an ingested skill ALWAYS has a body.
    assert obj["body"].strip().startswith("---")
    assert "Sync deal triage" in obj["body"]


def test_verify_passes_then_fails_on_tamper(tmp_path: Path):
    res = isk.ingest(tmp_path, VALID_SKILL, source_format="anthropic-skill")
    uid = res["uid"]
    v = isk.verify(tmp_path, uid)
    assert v["ok"], v
    # Tamper with the stored body; verification must fail.
    body_file = tmp_path / ".workspace" / "skills" / uid / "skill.md"
    body_file.write_text(res["body"].replace("Triage", "Tampered"), encoding="utf-8")
    v2 = isk.verify(tmp_path, uid)
    assert not v2["ok"]
    assert not v2["body_hash_ok"]


def _skill(name: str, desc: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: '{desc}'\n---\n\n# {name}\n\n{body}\n"


def test_version_upgrade_supersedes(tmp_path: Path):
    sid = "user:alex/triage"
    a = isk.ingest(tmp_path, _skill("triage", "v one"), source_format="anthropic-skill",
                   skill_id=sid, version="1.0.0")
    assert a["ok"] and a["action"] == "create"
    b = isk.ingest(tmp_path, _skill("triage", "v two body changed"),
                   source_format="anthropic-skill", skill_id=sid, version="1.1.0")
    assert b["ok"] and b["action"] == "upgrade"
    assert b["manifest"]["ownership"]["lineage"][0]["relation"] == "supersedes"
    assert b["manifest"]["ownership"]["lineage"][0]["uid"] == a["uid"]
    # Dispatch resolves to the newest version's body.
    obj = isk.find_by_skill_id(tmp_path, sid)
    assert "v two body changed" in obj["body"]


def test_version_downgrade_refused(tmp_path: Path):
    sid = "user:alex/triage"
    isk.ingest(tmp_path, _skill("triage", "v two"), source_format="anthropic-skill",
               skill_id=sid, version="2.0.0")
    res = isk.ingest(tmp_path, _skill("triage", "v one"), source_format="anthropic-skill",
                     skill_id=sid, version="1.0.0")
    assert not res["ok"]
    assert "downgrade" in res["error"]


def test_version_fork_on_conflict(tmp_path: Path):
    sid = "user:alex/triage"
    isk.ingest(tmp_path, _skill("triage", "v two"), source_format="anthropic-skill",
               skill_id=sid, version="2.0.0")
    res = isk.ingest(tmp_path, _skill("triage", "v one"), source_format="anthropic-skill",
                     skill_id=sid, version="1.0.0", on_conflict="fork")
    assert res["ok"] and res["action"] == "fork"
    assert res["skill_id"].endswith("~fork")
    assert res["manifest"]["ownership"]["lineage"][0]["relation"] == "forked-from"


def test_parse_version_orders_numerically():
    assert isk._parse_version("1.2.0") < isk._parse_version("1.10.0")
    assert isk._parse_version("2.0.0") > isk._parse_version("1.9.9")


CURSOR_RULE = (
    "---\n"
    "description: Use the internal RPC pattern for all service calls\n"
    "globs: src/**/*.ts\n"
    "alwaysApply: false\n"
    "---\n\n"
    "- Wrap every call in our RpcClient.\n"
    "- Never call fetch() directly.\n"
)

CLINE_RULE = "# API conventions\n\nAlways validate inputs at the boundary.\nReturn typed errors.\n"


def test_cursor_adapter_preserves_description_and_produces_valid_skill():
    md, fmt = isk.adapt_to_skill_md(CURSOR_RULE, source_format="cursor-rule")
    assert fmt == "cursor-rule"
    assert isk.validate_skill(md) == []
    fm, body = isk.parse_frontmatter(md)
    assert "RPC pattern" in fm["description"].strip("'")
    # globs preserved as provenance, not dropped
    assert "globs: src/**/*.ts" in body
    assert "RpcClient" in body


def test_auto_sniff_routes_cursor_rule():
    md, fmt = isk.adapt_to_skill_md(CURSOR_RULE, source_format="auto")
    assert fmt == "cursor-rule"


def test_cline_adapter_derives_name_from_heading():
    md, fmt = isk.adapt_to_skill_md(CLINE_RULE, source_format="cline-rule")
    assert fmt == "cline-rule"
    assert isk.validate_skill(md) == []
    fm, body = isk.parse_frontmatter(md)
    assert fm["name"] == "api-conventions"
    assert "validate inputs" in body


def test_cursor_rule_ingests_and_dispatches(tmp_path: Path):
    r = isk.ingest(tmp_path, CURSOR_RULE, source_format="cursor-rule", author="alex")
    assert r["ok"], r
    assert r["manifest"]["source_format"] == "cursor-rule"
    obj = isk.find_by_skill_id(tmp_path, r["skill_id"])
    assert obj is not None and "RpcClient" in obj["body"]


def test_adapter_sanitises_angle_brackets_and_rules(tmp_path: Path):
    nasty = ("---\ndescription: rule with <tags> inside\nglobs: '*'\n---\n\n"
             "Body line.\n\n---\n\nAfter a rule.\n")
    md, fmt = isk.adapt_to_skill_md(nasty, source_format="cursor-rule")
    assert isk.validate_skill(md) == []  # angle brackets + body --- both fixed


def test_export_reimport_signature_verifies(tmp_path: Path):
    """DoD #5: ownership survives export -> re-import; signature verifies on
    a second 'machine' (a second workspace dir, same operator key)."""
    src_ws = tmp_path / "ws-a"
    dst_ws = tmp_path / "ws-b"
    src_ws.mkdir()
    dst_ws.mkdir()
    res = isk.ingest(src_ws, VALID_SKILL, source_format="anthropic-skill",
                     author="alex")
    uid = res["uid"]
    # "Export" = copy the object folder into the destination workspace.
    import shutil
    shutil.copytree(src_ws / ".workspace" / "skills" / uid,
                    dst_ws / ".workspace" / "skills" / uid)
    v = isk.verify(dst_ws, uid)
    assert v["ok"], v
    obj = isk.load(dst_ws, uid)
    assert obj["manifest"]["ownership"]["author"] == "alex"

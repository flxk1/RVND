# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Span-norm rule registry: per span = one norm, placed on the legal map,
persisted per workspace and per user."""

from __future__ import annotations

from rvnd import rule_registry
from rvnd.rule_registry import RuleRegistry
from rvnd import legal_corpus


_CLAUSE = ("The controller shall erase personal data on request under "
           "Regulation (EU) 2016/679 unless a retention obligation applies.")


def test_a_span_is_placed_on_the_instruments_that_govern_it(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    r = reg.place_span(_CLAUSE, source_document="msa.md")
    anchors = {(a["entity"], a["relation"]) for a in r["anchors"]}
    assert ("gdpr", "cites") in anchors                 # cited instrument
    assert ("EU", "governed_by") in anchors             # its jurisdiction
    assert any(rel == "enforced_by" for _, rel in anchors)   # an enforcing regulator
    # exactly one norm captured for the span
    assert r["norm"]["modal"] in ("obligation", "prohibition", "permission", "right", "")


def test_persisted_per_workspace_and_per_user(tmp_path):
    workspace = tmp_path / "projectA"
    user_root = tmp_path / "userhome"
    reg = RuleRegistry(workspace, user="alex", user_root=user_root)
    reg.place_span(_CLAUSE, source_document="msa.md")
    # per-workspace file exists and reloads
    reg2 = RuleRegistry(workspace, user="alex", user_root=user_root)
    assert len(reg2.workspace_items()) == 1
    # per-user store has it, tagged with the workspace
    user_rows = reg2.user_items(user="alex")
    assert len(user_rows) == 1 and user_rows[0]["workspace"].endswith("projectA")


def test_user_store_aggregates_across_workspaces(tmp_path):
    user_root = tmp_path / "userhome"
    RuleRegistry(tmp_path / "A", user="alex", user_root=user_root).place_span(
        _CLAUSE, source_document="a.md")
    RuleRegistry(tmp_path / "B", user="alex", user_root=user_root).place_span(
        "Der Auftragsverarbeiter muss nach Art. 28 DSGVO Weisungen befolgen.",
        source_document="b.md")
    rows = RuleRegistry(tmp_path / "A", user="alex", user_root=user_root).user_items(user="alex")
    workspaces = {r["workspace"].split("/")[-1] for r in rows}
    assert {"A", "B"} <= workspaces                          # both projects' rules in one user view


def test_idempotent_on_same_span(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_span(_CLAUSE, source_document="msa.md")
    second = reg.place_span(_CLAUSE, source_document="msa.md")
    assert second["status"] == "updated"
    assert len(reg.workspace_items()) == 1


def test_place_document_is_one_norm_per_span(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    doc = ("The processor shall implement security measures under Regulation (EU) "
           "2016/679. The provider must register a high-risk system under the AI Act "
           "(Regulation (EU) 2024/1689).")
    out = reg.place_document(doc, source_document="memo.md")
    assert out["count"] >= 2                             # two spans → two norms
    # each placed rule carries a span + anchors
    for r in reg.workspace_items():
        assert r["span"]["text"] and r["anchors"]


def test_reverse_index_rules_at_entity(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_span(_CLAUSE, source_document="msa.md")
    at_gdpr = reg.rules_at("gdpr")
    assert len(at_gdpr) == 1 and at_gdpr[0]["span"]["text"].startswith("The controller")
    assert reg.rules_at("nonexistent") == []


def test_anchors_use_a_persisted_corpus_when_present(tmp_path):
    # seed the folder's corpus first; the registry should anchor against it
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    r = reg.place_span("Erasure under Regulation (EU) 2016/679.", source_document="x.md")
    assert any(a["entity"] == "gdpr" for a in r["anchors"])


def test_hook_never_raises(tmp_path):
    out = rule_registry.place_into_registry(str(tmp_path), "no operative rule here")
    assert isinstance(out, dict) and "placed" in out


# ── canonical URN spine — the rule layer's address ────────────────────────────

def test_canonical_urn_minted_on_place_span(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    a = reg.place_span(_CLAUSE, source_document="msa.md", pinpoint="cl. 4")
    assert a["canonical_urn"].startswith("urn:lg:source:rule-")
    # deterministic: the same span placed again keeps the same address
    b = reg.place_span(_CLAUSE, source_document="msa.md", pinpoint="cl. 4")
    assert b["canonical_urn"] == a["canonical_urn"]
    # distinct: a different pinpoint is a different rule, a different address
    c = reg.place_span(_CLAUSE, source_document="msa.md", pinpoint="cl. 5")
    assert c["canonical_urn"] != a["canonical_urn"]
    # substrate for compiles_to edges exists and starts empty
    assert a["obligation_urns"] == []


def test_reanchor_preserves_the_canonical_urn(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    "Preamble. " + _CLAUSE
    placed = reg.place_span(_CLAUSE, source_document="msa.md",
                            document_hash="h1", document_version=1)
    urn_before = placed["canonical_urn"]
    doc_v2 = "New preamble, longer than before. " + _CLAUSE
    out = reg.reanchor_document("msa.md", doc_v2, new_hash="h2", new_version=2)
    assert placed["id"] in out["migrated"]
    rec = reg.items[placed["id"]]
    assert rec["canonical_urn"] == urn_before
    assert rec["span"]["document_version"] == 2


def test_legacy_span_record_gains_its_urn_on_load(tmp_path):
    reg = RuleRegistry(tmp_path, user="alex")
    placed = reg.place_span(_CLAUSE, source_document="msa.md")
    # strip the spine fields, as a record written before the spine would be
    p = tmp_path / "legal-corpus" / "rule-items.jsonl"
    import json as _json
    rows = [_json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        r.pop("canonical_urn", None)
        r.pop("obligation_urns", None)
    p.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    reg2 = RuleRegistry(tmp_path, user="alex")
    healed = reg2.items[placed["id"]]
    assert healed["canonical_urn"] == placed["canonical_urn"]
    assert healed["obligation_urns"] == []

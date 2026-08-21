# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Grounder — attribution, fixity, and support-gate tests.

Covers erasure, sweepable creator names, per-folder write locking, content
fixity, and claim-support checks with an escalate-not-retract posture.
"""

from __future__ import annotations

import json
import multiprocessing
import os

import pytest

from rvnd.grounder import GroundingLedger


@pytest.fixture()
def ledger(tmp_path):
    return GroundingLedger(tmp_path, log_root=tmp_path / "log")


# ── erasure path ─────────────────────────────────────────────────────────────

def _attributed(ledger):
    return ledger.register_work(
        title="Paper", url="https://x.test/p", date="2020",
        creators=[{"name": "Doe, Jane"}, {"name": "Roe, Richard"}])


def test_forget_subject_removes_creator_keeps_work(ledger):
    w = _attributed(ledger)
    res = ledger.forget_subject("Doe, Jane")
    assert res["status"] == "ok"
    assert res["works_touched"] == [w["id"]]
    rec = ledger.works[w["id"]]
    assert {c["name"] for c in rec["creators"]} == {"Roe, Richard"}
    assert rec["creator_erased"] is True
    # ledger on disk reflects it
    led2 = GroundingLedger(ledger.folder, log_root=ledger.log_root)
    assert all(c["name"] != "Doe, Jane"
               for c in led2.works[w["id"]]["creators"])


def test_forget_subject_removes_corpus_entity(ledger):
    _attributed(ledger)
    ledger.link_creators_to_corpus()
    from rvnd.legal_corpus import EntityRegistry
    assert any(e["name"] == "Doe, Jane"
               for e in EntityRegistry(ledger.folder,
                                       log_root=ledger.log_root).entities.values())
    res = ledger.forget_subject("doe, jane")            # case-insensitive
    assert res["entity_removed"] is True
    reg = EntityRegistry(ledger.folder, log_root=ledger.log_root)
    assert not any(e["name"] == "Doe, Jane" for e in reg.entities.values())


def test_forget_subject_claims_routed_to_human(ledger):
    w = _attributed(ledger)
    c = ledger.ground_claim("Doe, Jane showed the effect in 2020.", [w["id"]])
    res = ledger.forget_subject("Doe, Jane")
    assert c["id"] in res["claims_for_human_review"]
    # claim text untouched — options, never answers
    assert "Doe, Jane" in ledger.claims[c["id"]]["text"]


def test_forget_subject_audited_as_purge(ledger, tmp_path):
    _attributed(ledger)
    ledger.forget_subject("Doe, Jane")
    from rvnd.mutation_log import MutationLog
    events = list(MutationLog(tmp_path, log_root=tmp_path / "log").replay())
    purge = [e for e in events
             if e.extra.get("kind") == "grounding-subject-forgotten"]
    assert purge and purge[0].event == "purge"


def test_creator_names_sweepable_in_audit_haystack(ledger, tmp_path):
    """erase_sweep matches str values in event extras — creators_text makes
    grounder events discoverable by subject name."""
    _attributed(ledger)
    from rvnd.erasure import sweep
    report = sweep(str(tmp_path), "Doe, Jane", log_root=tmp_path / "log")
    assert sum(len(v) for v in report.hits_by_kind.values()) >= 1


# ── write lock ───────────────────────────────────────────────────────────────

def _register_batch(args):
    folder, log_root, start = args
    led = GroundingLedger(folder, log_root=log_root)
    for i in range(start, start + 25):
        led.register_work(title=f"W{i}", url=f"https://x.test/{i}")
    return True


def test_two_writers_lose_nothing(tmp_path):
    os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
    folder, log_root = str(tmp_path), str(tmp_path / "log")
    with multiprocessing.Pool(2) as pool:
        pool.map(_register_batch, [(folder, log_root, 0),
                                   (folder, log_root, 25)])
    led = GroundingLedger(folder, log_root=log_root)
    assert len(led.works) == 50                       # no lost updates (reloaded from versum)


def test_lock_reloads_other_writers_state(tmp_path):
    a = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    b = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    a.register_work(title="From A", url="https://x.test/a")
    b.register_work(title="From B", url="https://x.test/b")
    assert len(b.works) == 2                          # b saw a's write


# ── content fixity ───────────────────────────────────────────────────────────

def test_content_auto_hash(ledger):
    page = "<html><body>The actual retrieved page.</body></html>"
    w = ledger.register_work(title="Page", url="https://x.test/page",
                             content=page)
    sha = ledger.works[w["id"]]["identifiers"]["sha256"]
    import hashlib
    assert sha == hashlib.sha256(page.encode("utf-8")).hexdigest()
    assert w["id"] not in ledger.coverage()["web_works_missing_fixity"]
    # content itself is never stored (only its sha256) — scan the versum sink
    store = ledger._versum_store()
    blob = "".join(p.read_text("utf-8") for p in store.rglob("*.json")) \
        if store.exists() else ""
    assert "actual retrieved page" not in blob


def test_explicit_sha_not_overwritten_by_content(ledger):
    w = ledger.register_work(title="P", url="https://x.test/p2",
                             identifiers={"sha256": "ff" * 32},
                             content="different content")
    assert ledger.works[w["id"]]["identifiers"]["sha256"] == "ff" * 32


# ── claim-support gate scaffold ──────────────────────────────────────────────

def test_check_claim_unavailable_without_endpoint(ledger, monkeypatch):
    monkeypatch.delenv("WORKSPACE_LOCAL_LLM_URL", raising=False)
    w = ledger.register_work(title="W", url="https://x.test/w")
    c = ledger.ground_claim("Claim.", [w["id"]], quote="evidence passage")
    res = ledger.check_claim_support(c["id"])
    assert res["status"] == "unavailable"
    assert ledger.claims[c["id"]]["status"] == "asserted"   # untouched


def test_check_claim_requires_evidence(ledger):
    w = ledger.register_work(title="W", url="https://x.test/w")
    c = ledger.ground_claim("Bare claim.", [w["id"]])
    res = ledger.check_claim_support(c["id"])
    assert res["status"] == "no-evidence"


def test_failing_verdict_escalates_never_retracts(ledger, monkeypatch):
    w = ledger.register_work(title="W", url="https://x.test/w")
    c = ledger.ground_claim("Checked claim.", [w["id"]], quote="quote")
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda *a, **k: {
        "ok": True, "category": "does_not_support", "model_used": "mock"})
    res = ledger.check_claim_support(c["id"])
    assert res["verdict"] == "does_not_support"
    assert res["escalate"] is True
    rec = ledger.claims[c["id"]]
    assert rec["status"] == "asserted"                # NOT auto-retracted
    assert rec["support_check"]["verdict"] == "does_not_support"
    assert c["id"] in ledger.coverage()["support_failures"]


def test_supports_verdict_recorded_no_escalation(ledger, monkeypatch):
    w = ledger.register_work(title="W", url="https://x.test/w")
    c = ledger.ground_claim("Good claim.", [w["id"]], quote="quote")
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda *a, **k: {
        "ok": True, "category": "supports", "model_used": "mock"})
    res = ledger.check_claim_support(c["id"])
    assert res["escalate"] is False
    assert c["id"] not in ledger.coverage()["support_failures"]


def test_gold_set_template_exists_and_parses():
    from rvnd.loomground_assets import grounder_gold_path
    p = grounder_gold_path(template=True)
    rows = [json.loads(l) for l in p.read_text("utf-8").splitlines()
            if l.strip()]
    labelled = [r for r in rows if "label" in r]
    assert {r["label"] for r in labelled} == {"supports", "does_not_support",
                                              "insufficient"}


# ── local-LLM integration #3: creator-role classification ────────────────────

def test_classify_creator_roles_proposes_never_overwrites(ledger, monkeypatch):
    ledger.register_work(title="Mixed", url="https://x.test/m",
                         creators=[{"name": "Doe, Jane"},
                                   {"name": "European Commission"},
                                   {"name": "Kept, Role", "role": "editor"}])
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda text, cats, **k: {
        "ok": True,
        "category": "organisation" if ("Commission" in text) else "person"})
    res = ledger.classify_creator_roles()
    assert res["status"] == "ok" and res["count"] == 2
    w = next(iter(ledger.works.values()))
    roles = {c["name"]: c.get("role") for c in w["creators"]}
    assert roles["Doe, Jane"] == "author"
    assert roles["European Commission"] == "org"
    assert roles["Kept, Role"] == "editor"            # human value untouched
    assert all(c.get("role_source") == "local-llm"
               for c in w["creators"] if c["name"] != "Kept, Role")


def test_classify_creator_roles_drives_citation_formatting(ledger, monkeypatch):
    from rvnd.grounder import format_citation
    w = ledger.register_work(title="Report", url="https://x.test/r", date="2024",
                             creators=[{"name": "European Data Protection Board"}])
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda *a, **k: {
        "ok": True, "category": "organisation"})
    ledger.classify_creator_roles()
    out = format_citation(ledger.works[w["id"]], "apa")
    assert "European Data Protection Board" in out     # org never name-split


def test_classify_creator_roles_unavailable_leaves_ledger_untouched(
        ledger, monkeypatch):
    ledger.register_work(title="W", url="https://x.test/w",
                         creators=[{"name": "Doe, Jane"}])
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda *a, **k: {
        "ok": False, "error": "no endpoint"})
    res = ledger.classify_creator_roles()
    assert res["status"] == "unavailable"
    w = next(iter(ledger.works.values()))
    assert "role" not in w["creators"][0]


def test_classify_creator_roles_garbage_category_skipped(ledger, monkeypatch):
    ledger.register_work(title="W", url="https://x.test/w2",
                         creators=[{"name": "Ambiguous Name"}])
    import rvnd.local_llm as ll
    monkeypatch.setattr(ll, "classify", lambda *a, **k: {
        "ok": True, "category": "maybe??"})
    res = ledger.classify_creator_roles()
    assert res["status"] == "ok" and res["count"] == 0  # never guess

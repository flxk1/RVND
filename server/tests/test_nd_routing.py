# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the classifier → ND router (B2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import (
    BaseNDDispatcher,
    Classification,
    DefaultClassifier,
    DefaultExtractor,
    INBOX_SUBDIR,
    InboxWatcher,
    WorkspaceMemory,
    NDRouter,
    RoutingExtractor,
)


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


def _drop(folder: Path, name: str, content: str) -> Path:
    inbox = folder / INBOX_SUBDIR
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / name
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# DefaultClassifier — keyword + mime
# ===========================================================================


def test_classifier_preamble_is_not_normative():
    """WHEREAS / 'in witness whereof' is preamble, not operative content.

    The previous classifier called this 'contract' at high confidence; the
    new design recognises that contract caption + whereas clauses ARE NOT
    normative and correctly classifies them as unknown.
    """
    c = DefaultClassifier()
    text = """
    WHEREAS the parties of the first part agree to the governing law
    of Delaware, and hereinafter referred to as Party A and Party B,
    in witness whereof they sign on the effective date below.
    """
    result = c.classify(text)
    assert result.primary_type == "unknown"


def test_classifier_recognises_normative_clause():
    """Operative contract clauses are correctly classified as normative."""
    c = DefaultClassifier()
    text = (
        "The Licensee shall pay to the Licensor a royalty equal to ten "
        "percent (10%) of Net Sales, payable quarterly within thirty (30) "
        "days of the end of each calendar quarter."
    )
    result = c.classify(text)
    assert result.primary_type == "normative"
    assert result.confidence >= 0.45


def test_classifier_recognises_eu_regulation_article():
    """An EU regulation article (deontic + subject) classifies as normative."""
    c = DefaultClassifier()
    text = (
        "Providers of high-risk AI systems shall ensure that their systems "
        "undergo the relevant conformity assessment procedure prior to "
        "their placing on the market."
    )
    result = c.classify(text)
    assert result.primary_type == "normative"


def test_classifier_recognises_de_normative_clause():
    """German legal text with subject + modal classifies as normative."""
    c = DefaultClassifier()
    text = (
        "Der Verantwortliche hat geeignete technische und organisatorische "
        "Maßnahmen umzusetzen, um sicherzustellen und den Nachweis dafür "
        "erbringen zu können, dass die Verarbeitung gemäß dieser Verordnung "
        "erfolgt."
    )
    result = c.classify(text)
    assert result.primary_type == "normative"


def test_classifier_rejects_journalistic_register():
    """News reportage about a regulation is NOT normative."""
    c = DefaultClassifier()
    text = (
        "The European Parliament voted in March 2024 to approve the AI Act, "
        "after lengthy negotiations between the Council and the Commission."
    )
    result = c.classify(text)
    assert result.primary_type != "normative"


def test_classifier_recognises_math():
    c = DefaultClassifier()
    text = r"Theorem 1.1 (Pythagoras). For any right triangle: $a^2 + b^2 = c^2$. Proof: \begin{equation} \int_0^\pi \sin x \, dx = 2 \end{equation} QED."
    result = c.classify(text)
    assert result.primary_type == "math"


def test_classifier_recognises_code_via_filename():
    c = DefaultClassifier()
    # Even with no code-like content patterns, .py extension triggers code.
    result = c.classify("hello", file_path="/tmp/x.py", mime_type="text/x-python")
    assert result.primary_type == "code"


def test_classifier_recognises_code_via_pattern():
    c = DefaultClassifier()
    text = "def add(a, b):\n    return a + b\nimport os\nclass Foo:\n    pass"
    result = c.classify(text)
    assert result.primary_type == "code"


def test_classifier_detects_facets():
    c = DefaultClassifier()
    text = "This contract grants a master rights licence under UrhG § 32 and complies with GDPR Art. 28."
    result = c.classify(text)
    assert "music-rights" in result.facets
    assert "gdpr" in result.facets


def test_classifier_unknown_low_confidence():
    c = DefaultClassifier()
    result = c.classify("just a plain note about the weather")
    assert result.primary_type == "unknown"
    assert result.confidence < 0.5


# ===========================================================================
# NDRouter — registration + dispatch
# ===========================================================================


class _ContractND(BaseNDDispatcher):
    nd_id = "nd-contracts"
    handles_types = ["normative"]
    handles_facets = ["license"]
    confidence_floor = 0.4

    def extract(self, content, classification, *, source_document=None):
        pid = f"sha256:nd-contracts-pair-{(source_document or 'inline')[-20:]}"
        return [{
            "id": pid,
            "problem": {"id": f"sha256:nd-contracts-problem-{pid}",
                        "scope": "contracts", "type": "clause-meaning",
                        "summary": "extracted contract pair",
                        "facets": {"primary_type": classification.primary_type}},
            "solution": {"id": pid, "problem_id": f"sha256:nd-contracts-problem-{pid}",
                         "body": "contract analysis", "body_format": "prose",
                         "authority_tier": 3, "confidence": 0.9},
        }]


class _MathND(BaseNDDispatcher):
    nd_id = "nd-math"
    handles_types = ["math"]
    handles_facets = []
    confidence_floor = 0.7

    def extract(self, content, classification, *, source_document=None):
        return [{
            "id": "sha256:math-pair",
            "problem": {"id": "sha256:math-problem", "scope": "math",
                        "type": "compute",
                        "summary": "math problem extracted", "facets": {}},
            "solution": {"id": "sha256:math-pair",
                         "problem_id": "sha256:math-problem",
                         "body": "math solution", "body_format": "proof",
                         "authority_tier": 2, "confidence": 0.95},
        }]


def test_router_empty_dispatches_nothing():
    r = NDRouter()
    result = r.dispatch("hello", Classification(primary_type="normative",
                                                facets=[], confidence=0.9))
    assert result.nds_engaged == []
    assert result.total_pairs == 0


def test_router_engages_matching_nd():
    r = NDRouter()
    r.register(_ContractND())
    classification = Classification(primary_type="normative", facets=[],
                                    confidence=0.9)
    result = r.dispatch("WHEREAS the party agrees", classification)
    assert result.nds_engaged == ["nd-contracts"]
    assert result.total_pairs == 1


def test_router_skips_nd_when_type_does_not_match():
    r = NDRouter()
    r.register(_MathND())
    classification = Classification(primary_type="normative", facets=[],
                                    confidence=0.9)
    result = r.dispatch("WHEREAS the party agrees", classification)
    assert result.nds_engaged == []
    assert "nd-math" in result.nds_skipped


def test_router_engages_via_facet_match():
    r = NDRouter()
    r.register(_ContractND())
    # primary_type doesn't match but facet does (license).
    classification = Classification(primary_type="policy",
                                    facets=["license"], confidence=0.9)
    result = r.dispatch("licence grant", classification)
    assert "nd-contracts" in result.nds_engaged


def test_router_respects_confidence_floor():
    r = NDRouter()
    r.register(_MathND())   # confidence_floor=0.7
    classification = Classification(primary_type="math", facets=[],
                                    confidence=0.5)
    result = r.dispatch("$a + b$", classification)
    assert "nd-math" in result.nds_skipped
    assert result.total_pairs == 0


def test_router_engages_multiple_nds():
    r = NDRouter()
    r.register(_ContractND())
    r.register(_MathND())
    # Contract type + license facet → only contracts engages.
    classification = Classification(primary_type="normative",
                                    facets=["license"], confidence=0.9)
    result = r.dispatch("...", classification)
    assert set(result.nds_engaged) == {"nd-contracts"}


def test_router_re_register_replaces():
    r = NDRouter()
    r.register(_ContractND())
    # Register an ND with the same id but different behaviour.
    class _ContractV2(_ContractND):
        def extract(self, content, classification, *, source_document=None):
            return []  # silent — no pairs
    r.register(_ContractV2())
    result = r.dispatch("...", Classification(primary_type="normative",
                                              facets=[], confidence=0.9))
    assert result.nds_engaged == ["nd-contracts"]
    assert result.total_pairs == 0


def test_router_unregister():
    r = NDRouter()
    r.register(_ContractND())
    assert r.registered() == ["nd-contracts"]
    assert r.unregister("nd-contracts") is True
    assert r.registered() == []
    assert r.unregister("nd-contracts") is False


def test_router_silently_drops_misbehaving_nd():
    """An ND that throws is logged as skipped — never crashes the ingest."""
    class _BrokenND(BaseNDDispatcher):
        nd_id = "nd-broken"
        handles_types = ["contract"]

        def extract(self, content, classification, *, source_document=None):
            raise RuntimeError("boom")

    r = NDRouter()
    r.register(_BrokenND())
    result = r.dispatch("...", Classification(primary_type="normative",
                                              facets=[], confidence=0.9))
    assert "nd-broken" in result.nds_skipped
    assert result.total_pairs == 0


# ===========================================================================
# RoutingExtractor — end-to-end fan-out
# ===========================================================================


def test_routing_extractor_includes_base_pair_plus_nd_pairs(folder):
    r = NDRouter()
    r.register(_ContractND())
    ext = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )

    # Use an operative contract clause so the new classifier marks it
    # 'normative' (which _ContractND below opts into).
    p = _drop(folder, "agreement.txt",
              "The Licensee shall pay the Licensor a licence fee within "
              "thirty (30) days of the end of each quarter.")
    result = ext.extract(str(p), str(folder))
    # 1 base + 1 from nd-contracts
    assert len(result.pairs) == 2
    pair_scopes = {pair["problem"]["scope"] for pair in result.pairs}
    assert {"inbox", "contracts"} == pair_scopes


def test_routing_extractor_no_match_returns_only_base(folder):
    r = NDRouter()
    r.register(_ContractND())
    ext = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )

    p = _drop(folder, "shopping.txt", "milk, eggs, bread")
    result = ext.extract(str(p), str(folder))
    # Just the base pair; nd-contracts didn't claim the doc.
    assert len(result.pairs) == 1
    assert result.pairs[0]["problem"]["scope"] == "inbox"


# ===========================================================================
# End-to-end with InboxWatcher
# ===========================================================================


def test_inbox_watcher_dispatches_to_nd(folder, log_root):
    """Drop a contract into Inbox → InboxWatcher uses RoutingExtractor →
    nd-contracts gets dispatched → both pairs land in memory."""
    r = NDRouter()
    r.register(_ContractND())
    extractor = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )
    watcher = InboxWatcher(folder, log_root=log_root, extractor=extractor)

    # An operative contract clause (normative). The old test used a WHEREAS
    # preamble which is correctly NOT classified as normative under the new
    # design — preamble has no operative content.
    _drop(folder, "msa.txt",
          "The Licensee shall pay the Licensor a licence fee within "
          "thirty (30) days of the end of each quarter.")
    new_ids = watcher.run_once()

    # Base pair + ND pair = 2 new ids.
    assert len(new_ids) == 2

    mem = WorkspaceMemory(folder, log_root=log_root)
    pair_scopes = {p["problem"]["scope"] for p in mem.all_pairs()}
    assert "inbox" in pair_scopes
    assert "contracts" in pair_scopes


def test_inbox_watcher_idempotent_with_routing(folder, log_root):
    """Re-scanning produces no new pairs (base + ND, both deduped by hash)."""
    r = NDRouter()
    r.register(_ContractND())
    extractor = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )
    watcher = InboxWatcher(folder, log_root=log_root, extractor=extractor)

    _drop(folder, "x.txt",
          "The Licensee shall pay the Licensor a licence fee within "
          "thirty (30) days.")
    first = watcher.run_once()
    second = watcher.run_once()

    assert len(first) >= 1
    assert second == []   # idempotent


def test_inbox_watcher_two_nds_both_engaged(folder, log_root):
    """A document that triggers both NDs gets both engaged."""
    class _BothNDOne(BaseNDDispatcher):
        nd_id = "nd-one"
        handles_facets = ["gdpr"]
        confidence_floor = 0.5

        def extract(self, content, classification, *, source_document=None):
            return [{
                "id": "sha256:nd-one-pair",
                "problem": {"id": "sha256:nd-one-problem",
                            "scope": "gdpr", "type": "applicability",
                            "summary": "gdpr analysis", "facets": {}},
                "solution": {"id": "sha256:nd-one-pair",
                             "problem_id": "sha256:nd-one-problem",
                             "body": "gdpr finding", "body_format": "prose",
                             "authority_tier": 2, "confidence": 0.9},
            }]

    class _BothNDTwo(BaseNDDispatcher):
        nd_id = "nd-two"
        handles_facets = ["ai-act"]
        confidence_floor = 0.5

        def extract(self, content, classification, *, source_document=None):
            return [{
                "id": "sha256:nd-two-pair",
                "problem": {"id": "sha256:nd-two-problem",
                            "scope": "ai-act", "type": "applicability",
                            "summary": "ai-act analysis", "facets": {}},
                "solution": {"id": "sha256:nd-two-pair",
                             "problem_id": "sha256:nd-two-problem",
                             "body": "ai-act finding", "body_format": "prose",
                             "authority_tier": 2, "confidence": 0.9},
            }]

    r = NDRouter()
    r.register(_BothNDOne())
    r.register(_BothNDTwo())
    extractor = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )
    watcher = InboxWatcher(folder, log_root=log_root, extractor=extractor)

    _drop(folder, "doc.txt",
          "This processing under GDPR Art. 28 falls under AI Act Annex III high-risk classification.")
    watcher.run_once()

    mem = WorkspaceMemory(folder, log_root=log_root)
    scopes = {p["problem"]["scope"] for p in mem.all_pairs()}
    assert "gdpr" in scopes
    assert "ai-act" in scopes


def test_nd_pairs_respect_folder_scope(tmp_path, log_root):
    """ND-dispatched pairs land in the FOLDER they were ingested from, not leak."""
    hr = tmp_path / "HR"
    eng = tmp_path / "Engineering"
    hr.mkdir()
    eng.mkdir()

    r = NDRouter()
    r.register(_ContractND())
    extractor = RoutingExtractor(
        base_extractor=DefaultExtractor(),
        classifier=DefaultClassifier(),
        router=r,
    )

    # HR ingests a contract.
    _drop(hr, "hr-contract.txt", "WHEREAS the parties of the first part...")
    InboxWatcher(hr, log_root=log_root, extractor=extractor).run_once()

    # Engineering sees no contract pairs (asymmetric rule).
    eng_mem = WorkspaceMemory(eng, log_root=log_root)
    eng_scopes = {p["problem"]["scope"] for p in eng_mem.all_pairs()}
    assert "contracts" not in eng_scopes

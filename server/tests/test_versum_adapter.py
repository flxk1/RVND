"""RVND consumes Versum through one adapter and Solver through neutral observations."""

from versum.store.graph import Claim, Concept, Edge, save_claims, save_concepts, save_edges
from versum.composition import Composition, Participant, save_compositions

from rvnd.adapters.versum import VersumKnowledgeStore, VersumSolverSource


def _store(tmp_path):
    root = tmp_path / ".versum"
    root.mkdir()
    save_claims(root / "claims.csv", [
        Claim("claim-a", "urn:source:a", text="A", predicate="causes",
              dimension="causal"),
    ], "generic")
    save_concepts(root / "concepts.csv", [
        Concept("concept-a", label="A"), Concept("concept-b", label="B"),
        Concept("concept-c", label="C"),
    ])
    save_edges(root / "semantic_edges.csv", [
        Edge("edge-1", "concept-a", "concept-b", "part_of",
             confidence="0.8", dimension="structural"),
        Edge("edge-2", "concept-b", "concept-c", "rhymes_with",
             confidence="0.5", dimension="causal"),
    ])
    return VersumKnowledgeStore(tmp_path)


def test_store_reads_through_versum_public_loaders(tmp_path):
    store = _store(tmp_path)
    snap = store.snapshot()
    assert store.available
    assert len(snap.claims) == 1
    assert len(snap.concepts) == 3
    assert len(snap.edges) == 2
    assert len(snap.digest) == 64
    assert store.subgraph("concept-b", depth=1)["edges"] == list(snap.edges)


def test_solver_source_composes_versum_edges(tmp_path):
    source = VersumSolverSource(_store(tmp_path))
    paths = source.paths(start="concept-a", max_depth=2)
    composed = next(p for p in paths if p.object == "concept-c")
    assert composed.dimension.value == "causal"
    assert composed.confidence == 0.4
    assert source.observation()["producer"] == "loomground-versum"


def test_solver_observation_carries_native_compositions(tmp_path):
    store = _store(tmp_path)
    claim_id = store.claims()[0]["item_id"]
    save_compositions(store.root / "compositions.jsonl", [Composition(
        "cmp:1", "deontic",
        (Participant("bearer", "actor:controller", (claim_id,)),
         Participant("action", "action:erase", (claim_id,))),
        method_version="rule-nd@1")])

    observation = VersumSolverSource(store).observation()
    assert observation["compositions"][0]["composition_id"] == "cmp:1"


def test_missing_index_fails_loudly(tmp_path):
    store = VersumKnowledgeStore(tmp_path)
    assert not store.available
    try:
        store.claims()
    except FileNotFoundError as exc:
        assert "index the folder" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing Versum index silently fell back")


def test_workspace_query_uses_versum_when_index_exists(tmp_path, monkeypatch):
    _store(tmp_path)
    from rvnd import mcp_impl

    monkeypatch.setattr(mcp_impl, "_log_root", lambda: tmp_path / "log")
    result = mcp_impl.workspace_query(str(tmp_path), subject="concept-a")
    assert result["knowledge_backend"] == "loomground-versum"
    assert result["triples"][0]["source_pair"] == "edge-1"


def test_reason_uses_versum_without_recording_local_graph(tmp_path, monkeypatch):
    _store(tmp_path)
    from rvnd import mcp_server

    monkeypatch.setattr(mcp_server, "_log_root", lambda: tmp_path / "log")
    result = mcp_server.reason(str(tmp_path), start="concept-a", record=False)
    assert result["knowledge_backend"] == "loomground-versum"
    assert any(row["object"] == "concept-c" for row in result["inferences"])

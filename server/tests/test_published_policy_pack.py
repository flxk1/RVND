# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Published policy packs remain inside the child's approved RVND lane."""

import pytest

import rvnd
import rvnd.published_policy_pack as policy_pack
from rvnd.governance_lane import GovernanceLane
from rvnd.published_policy_pack import (
    PolicyPackDenied,
    REQUIRED_REVIEWS,
    import_published_policy_pack,
    import_published_policy_pack_into_versum,
)


def _lane(**changes):
    values = {
        "lane_id": "child-lane",
        "agent": "child:one",
        "max_grade": "L1",
        "action_classes": ("classify", "recommend"),
        "folder": "/child",
        "policy_fingerprint": "sha256:active",
        "approved_by": "rvnd",
        "rationale": "bounded child policy",
    }
    values.update(changes)
    return GovernanceLane(**values)


def _payload(**changes):
    values = {
        "pack_id": "publisher/child-safe",
        "publisher": "publisher",
        "policy_fingerprint": "sha256:active",
        "action_kinds": ["classify"],
        "reviews": {name: f"rvnd:{name}:passed" for name in REQUIRED_REVIEWS},
    }
    values.update(changes)
    return values


def _import(monkeypatch, payload=None, **changes):
    lane = changes.pop("lane", _lane())
    monkeypatch.setattr(policy_pack, "get_lane",
                        lambda folder, child_agent, log_root=None: lane)
    values = {
        "folder": "/child",
        "child_agent": "child:one",
        "declared_action_kinds": ("classify",),
        "known_action_kinds": ("classify", "recommend"),
        "active_policy_fingerprint": "sha256:active",
        "review_attestations": {
            name: f"rvnd:{name}:passed" for name in REQUIRED_REVIEWS
        },
    }
    values.update(changes)
    return import_published_policy_pack(payload or _payload(), **values)


def test_import_boundary_is_published_from_package_root():
    assert rvnd.import_published_policy_pack is import_published_policy_pack
    assert (
        rvnd.import_published_policy_pack_into_versum
        is import_published_policy_pack_into_versum
    )
    assert rvnd.PolicyPackDenied is PolicyPackDenied


def test_pack_is_bound_to_declared_kind_reviews_and_child_lane(monkeypatch):
    imported = _import(monkeypatch)
    assert imported.action_kinds == ("classify",)
    assert imported.governance_lane_id == "child-lane"
    assert set(imported.reviews) == set(REQUIRED_REVIEWS)
    assert set(imported.language_contracts) == {"governance", "deontic"}
    assert imported.language_contracts["governance"]["package"] == (
        "loomground-governance"
    )
    assert imported.language_contracts["deontic"]["package"] == (
        "loomground-deontic"
    )


@pytest.mark.parametrize("kinds,match", [
    (None, "must declare"),
    ([], "at least one"),
    (["classify", "classify"], "duplicate"),
])
def test_pack_requires_explicit_action_kinds(monkeypatch, kinds, match):
    with pytest.raises(PolicyPackDenied, match=match):
        _import(monkeypatch, _payload(action_kinds=kinds))


def test_unknown_kind_is_denied_even_when_adapter_declares_it(monkeypatch):
    with pytest.raises(PolicyPackDenied, match="unknown action kinds"):
        _import(monkeypatch, _payload(action_kinds=["publish"]),
                declared_action_kinds=("publish",))


def test_known_but_undeclared_kind_is_denied(monkeypatch):
    with pytest.raises(PolicyPackDenied, match="undeclared action kinds"):
        _import(monkeypatch, _payload(action_kinds=["recommend"]))


@pytest.mark.parametrize("review", REQUIRED_REVIEWS)
def test_every_review_dimension_is_mandatory(monkeypatch, review):
    reviews = _payload()["reviews"]
    reviews.pop(review)
    with pytest.raises(PolicyPackDenied, match=f"mandatory RVND {review} review"):
        _import(monkeypatch, review_attestations=reviews)


def test_publisher_review_claims_do_not_supply_rvnd_attestations(monkeypatch):
    with pytest.raises(PolicyPackDenied, match="mandatory RVND child_safety"):
        _import(monkeypatch, _payload(), review_attestations={})


def test_fingerprint_must_match_pack_active_policy_and_child_lane(monkeypatch):
    with pytest.raises(PolicyPackDenied, match="active policy fingerprint"):
        _import(monkeypatch, _payload(policy_fingerprint="sha256:stale"))
    with pytest.raises(PolicyPackDenied, match="bound to the child"):
        _import(monkeypatch, lane=_lane(policy_fingerprint="sha256:other"))


def test_child_lane_must_cover_every_pack_action_kind(monkeypatch):
    with pytest.raises(PolicyPackDenied, match="outside the child governance lane"):
        _import(monkeypatch, _payload(action_kinds=["classify", "recommend"]),
                declared_action_kinds=("classify", "recommend"),
                lane=_lane(action_classes=("classify",)))


def test_approved_pack_persists_only_through_ingest_to_versum(tmp_path):
    from rvnd.governance_lane import register_lane
    from rvnd.adapters.versum import load_dimensioned_subgraphs

    folder = tmp_path / "child"
    folder.mkdir()
    log_root = tmp_path / "log"
    register_lane(
        folder,
        _lane(folder=str(folder)),
        log_root=log_root,
    )

    result = import_published_policy_pack_into_versum(
        _payload(),
        folder=str(folder),
        child_agent="child:one",
        declared_action_kinds=("classify",),
        known_action_kinds=("classify", "recommend"),
        active_policy_fingerprint="sha256:active",
        review_attestations={
            name: f"rvnd:{name}:passed" for name in REQUIRED_REVIEWS
        },
        log_root=str(log_root),
    )

    assert result["write"]["written"] is True
    stored = load_dimensioned_subgraphs(folder / ".versum")
    assert len(stored) == 1
    assert stored[0].value["nd"]["facet"] == "nD"
    assert any(
        node["node_type"] == "published-policy-pack"
        for node in stored[0].value["nodes"]
    )
    assert {
        node["properties"]["name"]
        for node in stored[0].value["nodes"]
        if node["node_type"] == "policy-language-contract"
    } == {"governance", "deontic"}


def test_missing_policy_language_contract_denies_before_persistence(
        tmp_path, monkeypatch):
    folder = tmp_path / "child"
    folder.mkdir()
    monkeypatch.setattr(
        policy_pack.installed_policy_language_packages()[0][1],
        "language_version",
        lambda: "",
    )
    monkeypatch.setattr(policy_pack, "get_lane",
                        lambda *args, **kwargs: _lane(folder=str(folder)))

    with pytest.raises(PolicyPackDenied, match="governance policy language"):
        import_published_policy_pack_into_versum(
            _payload(),
            folder=str(folder),
            child_agent="child:one",
            declared_action_kinds=("classify",),
            known_action_kinds=("classify",),
            active_policy_fingerprint="sha256:active",
            review_attestations={
                name: f"rvnd:{name}:passed" for name in REQUIRED_REVIEWS
            },
        )
    assert not (folder / ".versum").exists()


def test_denied_pack_never_creates_versum_store(tmp_path):
    folder = tmp_path / "child"
    folder.mkdir()
    with pytest.raises(PolicyPackDenied, match="no approved RVND governance lane"):
        import_published_policy_pack_into_versum(
            _payload(),
            folder=str(folder),
            child_agent="child:one",
            declared_action_kinds=("classify",),
            known_action_kinds=("classify",),
            active_policy_fingerprint="sha256:active",
            review_attestations={
                name: f"rvnd:{name}:passed" for name in REQUIRED_REVIEWS
            },
            log_root=str(tmp_path / "log"),
        )
    assert not (folder / ".versum").exists()

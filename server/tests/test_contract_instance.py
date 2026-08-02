# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the contract-instance model (contract_instance.py).

Discipline under test: typed-where-present, honest-where-absent. Identity is
(contract_id, version); same version + different document hash is refused
(amendments are new versions, never silent rewrites); supersession is an
explicit chain; parties and contracts project onto the world map."""

from decimal import Decimal

import pytest

from workspaces.contracts.instance import (ContractError, ContractInstance,
                                     ContractRegistry, PartyRef,
                                     _lei_checksum_ok)
from workspaces.legal_corpus import EntityRegistry
from workspaces.temporal import Date, Duration, Money, RelativeDeadline, Term


def make_lei(prefix: str = "529900T8BM49AURSDO") -> str:
    """Brute-force the two ISO 7064 check digits for an 18-char LEI prefix."""
    assert len(prefix) == 18
    for i in range(100):
        cand = f"{prefix}{i:02d}"
        if _lei_checksum_ok(cand):
            return cand
    raise AssertionError("no valid check digits found")


VALID_LEI = make_lei()


# ── PartyRef ──────────────────────────────────────────────────────────────────

class TestPartyRef:
    def test_minimal_valid(self):
        p = PartyRef(entity_code="acme-gmbh", role="processor")
        assert p.entity_kind == "legal_person"

    def test_empty_code_rejected(self):
        with pytest.raises(ContractError):
            PartyRef(entity_code="", role="processor")

    @pytest.mark.parametrize("bad_role", ["", "Processor", "the processor", "rôle"])
    def test_role_must_be_slug(self, bad_role):
        with pytest.raises(ContractError):
            PartyRef(entity_code="acme", role=bad_role)

    def test_bad_entity_kind_rejected(self):
        with pytest.raises(ContractError):
            PartyRef(entity_code="acme", role="licensor", entity_kind="instrument")

    def test_natural_person_allowed(self):
        PartyRef(entity_code="jane-doe", role="artist", entity_kind="natural_person")

    def test_valid_lei_accepted(self):
        PartyRef(entity_code="acme", role="controller", lei=VALID_LEI)

    def test_lei_wrong_length_rejected(self):
        with pytest.raises(ContractError):
            PartyRef(entity_code="acme", role="controller", lei="ABC123")

    def test_lei_bad_checksum_rejected(self):
        bad = VALID_LEI[:-2] + ("00" if VALID_LEI[-2:] != "00" else "01")
        with pytest.raises(ContractError):
            PartyRef(entity_code="acme", role="controller", lei=bad)

    def test_roundtrip(self):
        p = PartyRef(entity_code="acme", role="controller", name="ACME GmbH",
                     lei=VALID_LEI)
        assert PartyRef.from_dict(p.to_dict()) == p


# ── ContractInstance ──────────────────────────────────────────────────────────

def dpa(version: int = 1, **kw) -> ContractInstance:
    base = dict(
        contract_id="dpa-acme-2026", version=version,
        title="Data Processing Agreement", contract_type="dpa",
        parties=(PartyRef(entity_code="acme-gmbh", role="processor"),
                 PartyRef(entity_code="kunde-ag", role="controller")),
        effective_date=Date("2026-07-01"),
        term=Term(start=Date("2026-07-01"), duration=Duration.parse("P2Y")),
        governing_law="DE",
        events={"signing": Date("2026-06-15")},
        document_hash=f"sha256:{'a' * 31}{version}",
        language="de",
    )
    base.update(kw)
    return ContractInstance(**base)    # type: ignore[arg-type]


class TestContractInstance:
    def test_valid_instance(self):
        c = dpa()
        assert c.ref == "dpa-acme-2026@1"

    @pytest.mark.parametrize("bad_id", ["", "Has Spaces", "UPPER", "-leading"])
    def test_bad_contract_id_rejected(self, bad_id):
        with pytest.raises(ContractError):
            dpa(contract_id=bad_id)

    def test_version_zero_rejected(self):
        with pytest.raises(ContractError):
            dpa(version=0)

    def test_untyped_date_rejected(self):
        with pytest.raises(ContractError):
            dpa(effective_date="2026-07-01")  # type: ignore[arg-type]

    def test_untyped_event_rejected(self):
        with pytest.raises(ContractError):
            dpa(events={"signing": "2026-06-15"})  # type: ignore[dict-item]

    def test_bare_string_party_rejected(self):
        with pytest.raises(ContractError):
            dpa(parties=("ACME GmbH",))  # type: ignore[arg-type]

    def test_supersedes_format_enforced(self):
        with pytest.raises(ContractError):
            dpa(version=2, supersedes="dpa-acme-2026")     # missing @version

    def test_supersedes_must_point_backwards(self):
        with pytest.raises(ContractError):
            dpa(version=2, supersedes="dpa-acme-2026@2")
        with pytest.raises(ContractError):
            dpa(version=2, supersedes="dpa-acme-2026@3")

    def test_supersedes_valid(self):
        c = dpa(version=2, supersedes="dpa-acme-2026@1")
        assert c.supersedes == "dpa-acme-2026@1"

    def test_event_dates_include_canonical_aliases(self):
        ev = dpa().event_dates()
        assert ev["signing"] == Date("2026-06-15")
        assert ev["effective_date"] == Date("2026-07-01")
        assert ev["term_end"] == Date("2028-07-01")

    def test_resolve_deadline_against_events(self):
        rd = RelativeDeadline(event="signing", offset=Duration.parse("P30D"))
        assert dpa().resolve_deadline(rd) == Date("2026-07-15")

    def test_resolve_deadline_unknown_event_is_none(self):
        rd = RelativeDeadline(event="delivery", offset=Duration.parse("P30D"))
        assert dpa().resolve_deadline(rd) is None

    def test_party_by_role(self):
        c = dpa()
        assert c.party_by_role("processor")[0].entity_code == "acme-gmbh"
        assert c.party_by_role("witness") == ()

    def test_missing_fields_cold_start(self):
        bare = ContractInstance(contract_id="x")
        missing = bare.missing_fields()
        for f in ("parties", "effective_date", "term", "governing_law",
                  "contract_type", "language"):
            assert f in missing
        assert dpa().missing_fields() == []

    def test_money_typed(self):
        c = dpa(total_value=Money(amount=Decimal("25000"), currency="EUR"))
        assert c.total_value.currency == "EUR"

    def test_roundtrip(self):
        c = dpa(total_value=Money(amount=Decimal("25000"), currency="EUR"))
        assert ContractInstance.from_dict(c.to_dict()) == c


# ── ContractRegistry ──────────────────────────────────────────────────────────

class TestContractRegistry:
    def test_register_and_reload(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        out = reg.register(dpa())
        assert out["status"] == "created"
        reg2 = ContractRegistry(tmp_path)        # fresh load from disk
        got = reg2.get("dpa-acme-2026")
        assert got is not None and got.effective_date == Date("2026-07-01")

    def test_idempotent_same_hash(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        out = reg.register(dpa())
        assert out["status"] == "updated"
        assert len(reg.instances) == 1

    def test_same_version_different_hash_refused(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        with pytest.raises(ContractError, match="version 2"):
            reg.register(dpa(document_hash="sha256:" + "b" * 32))

    def test_supersede_chain(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        v2 = dpa(version=2, supersedes="dpa-acme-2026@1",
                 document_hash="sha256:" + "b" * 32)
        reg.supersede("dpa-acme-2026@1", v2)
        assert reg.chain("dpa-acme-2026") == ["dpa-acme-2026@1", "dpa-acme-2026@2"]
        assert reg.get("dpa-acme-2026").version == 2          # latest wins
        assert reg.get("dpa-acme-2026", 1).version == 1       # history kept

    def test_supersede_unknown_target_refused(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        v2 = dpa(version=2, supersedes="dpa-acme-2026@1",
                 document_hash="sha256:" + "b" * 32)
        with pytest.raises(ContractError):
            reg.supersede("dpa-acme-2026@1", v2)

    def test_register_with_dangling_supersedes_refused(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        v2 = dpa(version=2, supersedes="dpa-acme-2026@1",
                 document_hash="sha256:" + "b" * 32)
        with pytest.raises(ContractError, match="not registered"):
            reg.register(v2)

    def test_supersede_requires_declared_link(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        v2 = dpa(version=2, document_hash="sha256:" + "b" * 32)   # no supersedes
        with pytest.raises(ContractError, match="must declare supersedes"):
            reg.supersede("dpa-acme-2026@1", v2)

    def test_by_party(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        assert [c.ref for c in reg.by_party("acme-gmbh")] == ["dpa-acme-2026@1"]
        assert reg.by_party("stranger") == []

    def test_all_latest(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        v2 = dpa(version=2, supersedes="dpa-acme-2026@1",
                 document_hash="sha256:" + "b" * 32)
        reg.register(v2)
        latest = reg.all_latest()
        assert len(latest) == 1 and latest[0].version == 2

    def test_world_map_projection(self, tmp_path):
        ents = EntityRegistry(tmp_path)
        reg = ContractRegistry(tmp_path)
        reg.register(dpa(), entity_registry=ents)
        # contract is an entity
        found = ents.search(kind="contract")
        assert [e["code"] for e in found] == ["dpa-acme-2026"]
        # parties are entities
        assert ents.search(kind="legal_person")
        # edges: party_to_contract + subject_to governing law
        keys = set(ents.edges.keys())
        assert "acme-gmbh|party_to_contract|dpa-acme-2026" in keys
        assert "kunde-ag|party_to_contract|dpa-acme-2026" in keys
        assert "dpa-acme-2026|subject_to|DE" in keys

    def test_persisted_jsonl_is_inspectable(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        reg.register(dpa())
        text = (tmp_path / "contracts" / "instances.jsonl").read_text(encoding="utf-8")
        assert '"contract_id": "dpa-acme-2026"' in text

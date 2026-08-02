# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The obligations read op: a board projection of the obligation registry that
never advances a state — the clock moves only through the tick op.

Claims under test (written before the logic):
  O1  empty folder → ok, every open bucket empty, no candidates
  O2  instantiated obligations land in the pending bucket with their resolved
      deadline (relative deadlines resolve via the contract's event dates);
      an unresolvable deadline is surfaced, never guessed
  O3  after a tick past the deadline the breached_candidate bucket carries the
      obligation and the candidates feed names it
  O4  state filter returns exactly that state; an unknown state is refused
      with the valid states listed
  O5  obligation_id returns one obligation plus its recorded history
  O6  contract_ref narrows to that contract's obligations
  O7  the read is side-effect-free: two board reads bracket-equal, and no
      state moved without a tick
  O8  the facade routes op="obligations" and its help lists the op

Run: python -m pytest server/tests/test_contract_obligations_read.py -q
"""
from __future__ import annotations

import pytest

from workspaces.contracts.instance import ContractInstance, ContractRegistry, PartyRef
from workspaces.obligation_runtime import ObligationRegistry
from workspaces.obligation_scheduler import ObligationScheduler
from workspaces.predicate import parse_condition
from workspaces.temporal import Date

import workspaces.mcp_server as S
from workspaces.mcp_impl import contract_obligations


def dpa(version: int = 1, **kw) -> ContractInstance:
    base = dict(
        contract_id="dpa-acme", version=version, contract_type="dpa",
        parties=(PartyRef(entity_code="acme", role="processor"),
                 PartyRef(entity_code="kunde", role="controller")),
        effective_date=Date("2026-07-01"),
        events={"signing": Date("2026-06-15"),
                "personal_data_breach": Date("2026-08-10")},
        document_hash=f"sha256:{'a' * 31}{version}", language="en")
    base.update(kw)
    return ContractInstance(**base)


RULE_NOTIFY = {
    "id": "rule:notify72",
    "norm": {"modal": "obligation", "subject": "processor",
             "action": "notify the controller of a personal data breach",
             "condition": "no later than 72 hours after the personal data breach",
             "condition_struct": parse_condition(
                 "no later than 72 hours after the personal data breach").to_dict()},
}
RULE_DELETE = {
    "id": "rule:delete",
    "norm": {"modal": "obligation", "subject": "processor",
             "action": "delete all personal data upon termination",
             "condition": ""},
}


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    return tmp_path


def seed(folder):
    contracts = ContractRegistry(folder, log_root=folder / "log")
    contracts.register(dpa())
    obligations = ObligationRegistry(folder, log_root=folder / "log")
    obligations.instantiate(dpa(), [RULE_NOTIFY, RULE_DELETE])
    return obligations


def test_empty_folder_is_an_empty_board(folder):                  # O1
    out = contract_obligations(str(folder))
    assert out["ok"] is True
    assert all(rows == [] for rows in out["buckets"].values())
    assert out["candidates"] == [] and out["unresolved_deadlines"] == []


def test_instantiated_obligations_show_pending_with_deadline(folder):   # O2
    seed(folder)
    out = contract_obligations(str(folder))
    pending = out["buckets"]["pending"]
    assert len(pending) == 2
    notify = next(r for r in pending if "notify" in r["summary"])
    assert notify["deadline"] == "2026-08-13"      # 72h after the breach event
    unresolved = next(r for r in pending if "delete" in r["summary"])
    assert unresolved["deadline"] is None
    assert unresolved["obligation_id"] in out["unresolved_deadlines"]


def test_tick_past_deadline_fills_candidates(folder):             # O3
    seed(folder)
    ObligationScheduler(folder, log_root=folder / "log").tick(Date("2026-09-01"))
    out = contract_obligations(str(folder))
    breached = out["buckets"]["breached_candidate"]
    assert len(breached) == 1 and "notify" in breached[0]["summary"]
    assert breached[0]["obligation_id"] in out["candidates"]


def test_state_filter_and_unknown_state_refused(folder):          # O4
    seed(folder)
    out = contract_obligations(str(folder), state="pending")
    assert out["ok"] and len(out["obligations"]) == 2
    bad = contract_obligations(str(folder), state="overdue")
    assert bad["ok"] is False and "pending" in bad["valid_states"]


def test_single_obligation_carries_history(folder):               # O5
    seed(folder)
    ObligationScheduler(folder, log_root=folder / "log").tick(Date("2026-09-01"))
    board = contract_obligations(str(folder))
    oid = board["buckets"]["breached_candidate"][0]["obligation_id"]
    out = contract_obligations(str(folder), obligation_id=oid)
    assert out["ok"] and out["obligation"]["obligation_id"] == oid
    assert out["history"], "a ticked obligation must carry its transitions"
    missing = contract_obligations(str(folder), obligation_id="nope")
    assert missing["ok"] is False


def test_contract_ref_narrows(folder):                            # O6
    seed(folder)
    ref = contract_obligations(str(folder))["buckets"]["pending"][0]["contract_ref"]
    out = contract_obligations(str(folder), contract_ref=ref)
    assert out["ok"] and len(out["obligations"]) == 2
    none = contract_obligations(str(folder), contract_ref="other@1")
    assert none["ok"] and none["obligations"] == []


def test_read_is_side_effect_free(folder):                        # O7
    seed(folder)
    first = contract_obligations(str(folder))
    second = contract_obligations(str(folder))
    assert first == second
    assert second["counts"]["pending"] == 2       # nothing advanced without a tick


def test_facade_routes_and_documents_the_op(folder):              # O8
    seed(folder)
    out = S.workspace_contract("obligations", {"folder_context": str(folder)})
    assert out["ok"] is True and out["counts"]["pending"] == 2
    ops = {o["op"] for o in S.workspace_contract("help")["ops"]}
    assert "obligations" in ops

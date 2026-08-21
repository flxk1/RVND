# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Queue hygiene: idempotent opens, the per-raiser flood guard, priority and
decide_by projection, and deadline-driven escalation.

Claims under test (written before the logic):
  H1  the same idempotency key returns the same decision with a dedup flag —
      open or already decided — and a fresh key opens fresh
  H2  a deduplicated open never re-notifies the holders
  H3  the flood guard refuses a raiser's opens past the cap in words and
      records the refusal; decided entries do not count against the cap;
      other raisers are unaffected
  H4  pending rows project priority, decide_by and the overdue flag; an
      invalid priority is refused at open
  H5  pending sorts urgent before untagged before low, ties by age
  H6  a passed decide_by escalates immediately when a ladder is declared —
      before the escalate_after_s window would fire
  H7  the facade passes the hygiene params through

Run: python -m pytest server/tests/test_decision_hygiene.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import rvnd.decisions.queue as DQ
import rvnd.mcp_server as S
from rvnd.mcp_impl import decision_open, decision_pending

SURFACE = {
    "query": "q",
    "options": [{"id": "a", "label": "A", "conclusion": "a",
                 "supporting": [], "consequences": []},
                {"id": "b", "label": "B", "conclusion": "b",
                 "supporting": [], "consequences": []}],
}


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    return str(tmp_path / "ws")


def opened(folder, raiser="crm-bot", **kw):
    return decision_open(folder, SURFACE, raiser, auto_notify=False, **kw)


def test_idempotency_key_dedupes(folder):                        # H1
    first = opened(folder, idempotency_key="erase-K-2026")
    again = opened(folder, idempotency_key="erase-K-2026")
    assert again["decision_id"] == first["decision_id"]
    assert again["deduplicated"] is True and again["state"] == "open"
    fresh = opened(folder, idempotency_key="other-key")
    assert fresh["decision_id"] != first["decision_id"]
    q = DQ.DecisionQueue(folder)
    q.close(first["decision_id"], "dana")
    decided = opened(folder, idempotency_key="erase-K-2026")
    assert decided["deduplicated"] is True and decided["state"] == "decided"


def test_dedup_never_renotifies(folder, monkeypatch):            # H2
    calls = []
    import rvnd.decisions.outbox as OB
    monkeypatch.setattr(OB, "notify",
                        lambda *a, **k: calls.append(1) or
                        {"ok": True, "holders": 0, "sent": []})
    decision_open(folder, SURFACE, "crm-bot", idempotency_key="k1")
    decision_open(folder, SURFACE, "crm-bot", idempotency_key="k1")
    assert len(calls) == 1, "the duplicate open must not notify again"


def test_flood_guard_caps_per_raiser(folder, monkeypatch):       # H3
    monkeypatch.setattr(DQ.DecisionQueue, "RAISER_OPEN_CAP", 3)
    for _ in range(3):
        assert opened(folder)["ok"] is True
    out = opened(folder)
    assert out["ok"] is False and "flood guard" in out["error"]
    assert opened(folder, raiser="other-flow")["ok"] is True
    q = DQ.DecisionQueue(folder)
    some_open = next(e for e in q.items.values()
                     if e["state"] == "open" and e["raised_by"] == "crm-bot")
    q.close(some_open["decision_id"], "dana")
    assert opened(folder)["ok"] is True, "a decided entry frees the cap"
    from rvnd.mutation_log import MutationLog
    from pathlib import Path as P
    import os
    kinds = [(e.extra or {}).get("kind") for e in
             MutationLog(P(folder), log_root=P(os.environ["WORKSPACE_L0_LOG_ROOT"])).replay()]
    assert "decision.open_refused" in kinds


def test_projection_and_priority_validation(folder):             # H4
    bad = opened(folder, priority="asap")
    assert bad["ok"] is False and "priority" in bad["error"]
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    opened(folder, priority="high", decide_by=soon)
    opened(folder, decide_by=past)
    rows = decision_pending(folder)["pending"]
    high = next(r for r in rows if r["priority"] == "high")
    assert high["decide_by"] == soon and high["overdue"] is False
    late = next(r for r in rows if r["decide_by"] == past)
    assert late["overdue"] is True


def test_pending_sorts_by_priority_then_age(folder):             # H5
    opened(folder, priority="low")
    opened(folder)                                # untagged = normal
    opened(folder, priority="urgent")
    rows = decision_pending(folder)["pending"]
    assert [r["priority"] for r in rows] == ["urgent", "", "low"]


def test_passed_deadline_escalates_before_window(folder):        # H6
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    did = opened(folder, escalate_to="management",
                 escalate_after_s=86400, decide_by=past)["decision_id"]
    row = next(r for r in decision_pending(folder)["pending"]
               if r["decision_id"] == did)
    assert row["competence"] == "management"
    assert "escalated" in row["assignment_basis"]


def test_facade_passes_hygiene_params(folder):                   # H7
    out = S.workspace_dispatch("decision_open", {
        "folder_context": folder, "surface": SURFACE, "raised_by": "crm-bot",
        "auto_notify": False, "idempotency_key": "fk", "priority": "urgent",
        "decide_by": "2027-01-01T00:00:00+00:00"})
    assert out["ok"] is True
    again = S.workspace_dispatch("decision_open", {
        "folder_context": folder, "surface": SURFACE, "raised_by": "crm-bot",
        "auto_notify": False, "idempotency_key": "fk"})
    assert again["deduplicated"] is True
    row = decision_pending(folder)["pending"][0]
    assert row["priority"] == "urgent"

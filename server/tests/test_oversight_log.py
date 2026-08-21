# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Log-write glue for admissions + ground-id taint walk."""

import tempfile
from pathlib import Path

from rvnd.lens import AdmissionVerdict, Admission
from rvnd.mutation_log import LogEvent, MutationLog
from rvnd.oversight_log import (
    TaintFinding, record_admission, taint_walk, mark_tainted)


# ── admission → lifecycle event ──────────────────────────────────────────────

def test_admit_writes_admit_event():
    with tempfile.TemporaryDirectory() as d:
        v = AdmissionVerdict(Admission.ADMIT, "style-pref", "covered")
        rec = record_admission(v, folder=d, content_hash="abc",
                               actor="alice", log_root=d)
        assert "audit_id" in rec
        log = MutationLog(Path(d), log_root=Path(d))
        events = [e for e in log.replay() if e.pair_id == "learn:abc"]
        assert len(events) == 1
        assert events[0].event == "admit"
        assert events[0].extra["class"] == "style-pref"


def test_hold_and_reject_map_to_their_verbs():
    with tempfile.TemporaryDirectory() as d:
        record_admission(AdmissionVerdict(Admission.HOLD, "x", "default-deny"),
                         folder=d, content_hash="h1", log_root=d)
        record_admission(AdmissionVerdict(Admission.REJECT, "y", "forbidden"),
                         folder=d, content_hash="h2", log_root=d)
        log = MutationLog(Path(d), log_root=Path(d))
        kinds = {e.pair_id: e.event for e in log.replay()
                 if e.pair_id.startswith("learn:")}
        assert kinds["learn:h1"] == "hold"
        assert kinds["learn:h2"] == "reject"


def test_aggregate_only_rides_in_extra():
    with tempfile.TemporaryDirectory() as d:
        v = AdmissionVerdict(Admission.ADMIT, "volume", "agg",
                             aggregate_only=True)
        record_admission(v, folder=d, content_hash="agg1", log_root=d)
        log = MutationLog(Path(d), log_root=Path(d))
        e = next(e for e in log.replay() if e.pair_id == "learn:agg1")
        assert e.extra["aggregate_only"] is True


# ── ground-id taint walk ─────────────────────────────────────────────────────

def _verdict_event(audit_id, pairs, footprint=(), event="system"):
    return LogEvent(event=event, folder_path="/f", pair_id=f"v:{audit_id}",
                    channel="system", actor="agent:bot",
                    extra={"obligation_pairs": list(pairs),
                           "footprint": list(footprint)})


def test_taint_walk_surfaces_citers():
    events = [
        _verdict_event("1", ["pair:a"]),
        _verdict_event("2", ["pair:b"]),
        _verdict_event("3", ["pair:a", "pair:c"]),
    ]
    found = taint_walk(events, "pair:a")
    ids = {f.pair_id for f in found}
    assert ids == {"v:1", "v:3"}


def test_taint_flags_stake_bearing():
    events = [
        _verdict_event("1", ["pair:a"], footprint=("financial",)),
        _verdict_event("2", ["pair:a"], footprint=()),
    ]
    found = taint_walk(events, "pair:a")
    by_pair = {f.pair_id: f.stake_bearing for f in found}
    assert by_pair["v:1"] is True
    assert by_pair["v:2"] is False


def test_taint_reads_grounds_dicts():
    e = LogEvent(event="system", folder_path="/f", pair_id="v:9",
                 channel="system",
                 extra={"grounds": [{"id": "pair:z"}, {"id": "pair:a"}]})
    found = taint_walk([e], "pair:z")
    assert len(found) == 1


def test_taint_no_match_empty():
    events = [_verdict_event("1", ["pair:a"])]
    assert taint_walk(events, "pair:missing") == []


def test_mark_tainted_writes_sweep_and_separates_incidents():
    with tempfile.TemporaryDirectory() as d:
        findings = [
            TaintFinding("a1", "v:1", "agent:bot", 1.0, "pair:a", "system",
                         stake_bearing=True),
            TaintFinding("a2", "v:2", "agent:bot", 2.0, "pair:a", "system",
                         stake_bearing=False),
        ]
        summary = mark_tainted(findings, folder=d, failed_ground="pair:a",
                               reason="superseded", log_root=d)
        assert summary["tainted_count"] == 2
        assert summary["stake_bearing_count"] == 1
        assert len(summary["incidents"]) == 1
        assert "audit_id" in summary
        log = MutationLog(Path(d), log_root=Path(d))
        assert any(e.pair_id == "taint:pair:a" for e in log.replay())


def test_end_to_end_replay_then_taint():
    with tempfile.TemporaryDirectory() as d:
        log = MutationLog(Path(d), log_root=Path(d))
        log.append(_verdict_event("1", ["pair:a"], footprint=("personal-data",)))
        log.append(_verdict_event("2", ["pair:b"]))
        found = taint_walk(log.replay(), "pair:a")
        assert len(found) == 1
        assert found[0].stake_bearing is True

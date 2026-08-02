# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A worked example of Workspace Oversight governing one agent across a day.

Run from the runtime dir:   PYTHONPATH=src python3 examples/oversight_demo.py
No network, no LLM, no install — pure stdlib.
"""

from workspaces.oversight import assess
from workspaces.action_gate import ActionRequest, StandingApproval
from workspaces.breaker import Breaker, Lease, Tripwire, default_tripwires
from workspaces.oversight_drift import drift_tripwire, evaluate as evaluate_drift
from workspaces.drift_monitor import DriftReport


def show(title, o):
    print(f"\n— {title}")
    print(f"   verdict={o.verdict}  grade={o.effective_grade}  breaker={o.breaker_state}")
    print(f"   {o.reason}")
    if o.bundle and o.bundle.dossier:
        d = o.bundle.connector_payload()["doubt"]
        print(f"   doubt: min_conf={d['min_confidence']} ood={d['ood_percentile']} "
              f"weak_cites={d['weakest_citation_count']}")


# The agent holds a live L3 lease, renewed at 09:00 (epoch 1000, ttl 8h).
NOW = 1000.0
lease = Lease("billing-bot", "L3", expires_at=NOW + 8 * 3600, ttl_seconds=8 * 3600)
breaker = Breaker(lease, tripwires=default_tripwires() + [drift_tripwire()])

# A standing approval: routine invoices under €100, capped at €5000 total/day.
appr = StandingApproval("billing-bot", "pay-invoice", "pair:invoices-small",
                        max_total=5000.0)

print("=" * 64)
print("Workspace Oversight — one agent, one day (all deterministic, no LLM)")
print("=" * 64)

# 1. A routine €80 invoice — covered by the standing approval → GO, no human.
o = assess(ActionRequest("billing-bot", "pay-invoice", "L3",
                         ("financial",), magnitude=80.0),
           breaker=breaker, standing_approvals=[appr], now=NOW + 60)
show("09:01  pay €80 invoice (routine)", o)

# 2. A €4000 invoice — still under the daily cap → GO.
o = assess(ActionRequest("billing-bot", "pay-invoice", "L3",
                         ("financial",), magnitude=4000.0),
           breaker=breaker, standing_approvals=[appr], now=NOW + 120)
show("09:02  pay €4000 invoice", o)

# 3. A profiling action (decide a supplier's credit tier) — high-risk:
#    CONDITIONAL + a doubt dossier attaches to the human's copy.
o = assess(ActionRequest("billing-bot", "supplier-credit-scoring", "L3",
                         ("personal-data",)),
           breaker=breaker, scope="profiling",
           grounds=[{"id": "pair:old-guidance", "authority_tier": 5,
                     "verified": False}],
           dossier_material={"confidences": [0.58, 0.91],
                             "ood_percentile": 96.0,
                             "blind_spots": ["sibling-folder history not visible"]},
           now=NOW + 3600)
show("10:00  score a supplier's creditworthiness (high-risk)", o)

# 4. Structural drift detected at noon (a new tool appeared in the catalogue) —
#    the regulator's model is stale → the breaker quarantines automatically.
sig = evaluate_drift(DriftReport(folder="/billing", as_of=NOW,
                                 structural=[{"metric": "catalogue:new-tool"}],
                                 window_n=200))
o = assess(ActionRequest("billing-bot", "pay-invoice", "L3",
                         ("financial",), magnitude=50.0),
           breaker=breaker, standing_approvals=[appr],
           metrics=sig.metrics, now=NOW + 3 * 3600)
show("12:00  pay €50 invoice — but structural drift fired", o)
print(f"        (drift: {sig.reason})")

# 5. The lease is never renewed (the human went home). By 18:00 it has lapsed —
#    autonomy decays to L0 with no action. The same €50 invoice now blocks.
#    (Clear the earlier quarantine first to isolate the lease effect.)
breaker.clear(by="alice", rationale="new tool reviewed and footprinted")
o = assess(ActionRequest("billing-bot", "pay-invoice", "L3",
                         ("financial",), magnitude=50.0),
           breaker=breaker, standing_approvals=[appr], now=NOW + 9 * 3600)
show("18:00  pay €50 invoice — lease lapsed, nobody renewed", o)

print("\n" + "=" * 64)
print("Note: every line above is a signed audit event in the folder's log.")
print("The stop at 18:00 needed no button — absence of renewal IS the stop.")
print("=" * 64)

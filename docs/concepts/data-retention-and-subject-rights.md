<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Data retention and subject rights

How RVND meets GDPR storage-limitation (Art. 5(1)(e)) and the data-subject
rights (Chapter III) given that its core record is an **append-only,
tamper-evident, hash-chained audit log**. The short version: retention here is
**erasure-driven, not time-driven**, and that is a deliberate design choice, not
a missing feature.

## Why there is no time-based retention job

A naive "purge everything older than N days" scheduler is the wrong instrument
for this system, and adding one would actively break the tamper-evidence the
whole record rests on:

- The audit chain is append-only by design — *"checks (chain) always run over
  the whole log; tampering does not expire"* (`incidents.py`). Deleting old
  events by the clock would create gaps indistinguishable from tampering and
  defeat `verify_chain`.
- Storage-limitation under Art. 5(1)(e) is about **personal data**, not about
  the audit record of governance decisions. RVND separates the two: personal
  data is removed by **targeted erasure**, while the signed record that an
  erasure happened is retained (a purge tombstone), because the *fact* of
  processing and its lawful basis is itself accountability evidence (Art. 5(2)).

So RVND has no retention scheduler and no `max_age`/TTL on stored data — and
that absence is intentional. Retention is enforced at the level of the personal
data, on a lawful-basis event, by erasure.

## The subject rights, mapped

| Right | Article | Status in RVND |
|---|---|---|
| **Erasure** | 17 | **Implemented + tested.** Three-state erasure (request → dry-run sweep → execute), cascade to descendants, composite tombstone, forgotten-subjects ledger blocking re-ingest, replay-safe (RV-10). The load-bearing retention mechanism. |
| **Access — "what do you hold about me"** | 15(1) | **Served by the erasure sweep.** `erasure.sweep(folder, subject)` is a complete subject-*index*: every event/draft/card referencing the subject, grouped by kind and folder, with counts — and it **names its blind spots** (`drafts_sealed`/`cards_sealed`) rather than reporting a sealed folder as clean. Pinned by `test_subject_rights_068.py`. |
| **Portability — the full payload, machine-readable** | 20 | **Deliberate gap.** The sweep returns locations + *redacted* previews, not raw personal data. A full-payload export is a new **personal-data-egress surface** and must be designed with its own access control (who is authorised to receive another subject's raw data?) — it is not bolted on here. Extension point: replay each sweep hit's `pair_id`/`audit_id` to its full content behind an authenticated access-request flow. |
| **Rectification** | 16 | **Deliberate gap.** No correct-in-place path. On an append-only chain, rectification is *redact-and-reappend*, not edit — the same machinery erasure uses (`draft_store.redact`, `card_store.redact`) plus a corrected re-ingest. Recorded as a scoped follow-up. |
| **Restriction / objection** | 18 / 21 | Not implemented as distinct handlers; the party kill-switch (halt an agent's processing) and lock disable-with-ack are the nearest live controls. Scoped follow-up. |

## Retention in practice (operator guidance)

- To honour a retention period, run erasure for the relevant subject(s) when the
  period lapses — driven by the operator's schedule or an external trigger, not
  an internal clock over the audit log.
- The **forgotten-subjects ledger** makes erasure durable: an erased subject
  cannot be silently re-ingested (`EraseGuardHit`), so retention holds against
  re-collection, not just at a point in time.
- Sealed folders are a blind spot for draft/card sweeps by construction —
  **unseal before an access or erasure request** so the sweep sees everything;
  the sweep will name the sealed folders it could not inspect so the gap is
  never silent.

## What this is not

RVND is local-first and typically single-operator; it is not a multi-tenant DSAR
portal. The access/portability/rectification gaps above are honest scoped
follow-ups, recorded here rather than papered over — a governance tool should
state its own subject-rights posture as plainly as it states its customers'.

---

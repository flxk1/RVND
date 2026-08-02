# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND data-pump adapter — the replay verifier as a data pump over RVND's
Ed25519 **signed audit chain**.

Internal by design: experimental, never wired into the runtime — production
use requires an injected Ed25519 replay verifier (see below).

Every governed run is a triple ``problem → candidate → verdict`` signed into the
chain. That chain is already the labeled, replayable dataset — no corpus to build.
This adapter reads the chain, maps each event onto a
:func:`loomground_solver.harvest` record, **re-checks the signature** of every kept
run, and emits a training set plus an **autonomy-graded** proposal to swap the
local generator's adapter (harvest → train adapter → propose swap on gate).

The default signature check is *presence only* — enough to drop unsigned/forged-
absent runs, NOT a cryptographic guarantee. Inject a real Ed25519 replay verifier
(``verifier=``) for production re-verification. The default oversight grade is
escalating, so a default-constructed pump never auto-applies a swap.

Host-side glue: the RVND / signer imports are lazy so the adapter is testable
without the full server, and every dependency (``chain_reader``, ``verifier``) can
be injected to swap or fake an implementation. Nothing in the universal solver
imports this — the direction is one-way."""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from loomground_solver import harvest, to_jsonl


class RvndDataPump:
    """Turn RVND's signed audit chain into training data + a swap proposal.

    ``chain_reader() -> iterable[event]`` yields governed-run events; the default
    lazily reads RVND's mutation log. ``verifier(record) -> bool`` re-checks a run
    (default: presence of a signature only — inject a real Ed25519 replay check for
    production). ``oversight_level`` grades the proposal: only ``"autonomous"`` may
    auto-apply the swap — every other level escalates to a human gate. The default
    is the escalating grade, so a default-constructed pump is safe."""

    # verdict vocabulary that counts as a PASS (kept for SFT / as the chosen side).
    PASS_VERDICTS = frozenset({"PASS", "PASSED", "APPROVE", "APPROVED", "OK", "GRANT"})

    def __init__(self, *,
                 chain_reader: Optional[Callable[[], Iterable[dict]]] = None,
                 verifier: Optional[Callable[[dict], bool]] = None,
                 oversight_level: str = "oversight",
                 min_examples: int = 1):
        self._read = chain_reader
        self._verify = verifier
        self._oversight = oversight_level
        self._min_examples = min_examples

    # ── chain access (lazy, host-side) ───────────────────────────────────────
    def _events(self) -> Iterable[dict]:
        if self._read is None:
            from workspaces.mutation_log import read_chain   # lazy, host-side
            self._read = read_chain
        return self._read()

    @classmethod
    def _to_record(cls, event: dict) -> dict:
        verdict = str(event.get("verdict", "")).upper()
        return {"problem": event.get("problem", ""),
                "candidate": event.get("candidate", ""),
                "passed": verdict in cls.PASS_VERDICTS,
                "signature": event.get("signature"),
                "rationale": event.get("rationale", ""),
                "trace": event.get("trace")}

    def _signature_ok(self, record: dict) -> bool:
        # A record only counts as verified if its signature re-checks. The default
        # is a presence check; inject a real replay verifier for production.
        if self._verify is not None:
            return bool(self._verify(record))
        return bool(record.get("signature"))

    # ── the pump ─────────────────────────────────────────────────────────────
    def harvest_chain(self, *, reverify: bool = True) -> dict:
        """Read the chain → records → :func:`harvest`. With ``reverify`` (default),
        every kept run's signature is re-checked; unsigned/forged runs are dropped
        and counted in ``stats.dropped_unverified``. Returns
        ``{"training", "proposal"}``."""
        records = [self._to_record(e) for e in self._events()]
        verify = self._signature_ok if reverify else None
        training = harvest(records, verify=verify)
        return {"training": training,
                "proposal": self._proposal(training["stats"])}

    def jsonl(self, *, reverify: bool = True) -> str:
        """Convenience: the harvested SFT examples as JSONL, ready for the local
        Phi/Qwen backend."""
        return to_jsonl(self.harvest_chain(reverify=reverify)["training"])

    # ── autonomy grading ─────────────────────────────────────────────────────
    def _proposal(self, stats: dict) -> dict:
        n = stats["kept_examples"]
        if n < self._min_examples:
            return {"action": "none", "examples": n,
                    "reason": "insufficient verified examples"}
        # Only an autonomous grade may auto-apply the swap; anything else must be
        # signed off by a human first (the decision-space discipline, at the
        # governance layer).
        gate = "auto" if self._oversight == "autonomous" else "escalate"
        return {"action": "train_adapter", "examples": n,
                "preference_pairs": stats["preference_pairs"], "gate": gate}

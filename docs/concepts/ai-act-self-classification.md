<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# RVND's own EU AI Act classification

RVND generates AI Act artefacts *for its users* — role allocation, risk
registers, duty identification over Regulation (EU) 2024/1689 prose. This
document is the other direction: **RVND classifying itself** as a deployed
system. The distinction matters — a governance tool that models everyone
else's obligations should be able to state its own. This is the Art.
classification record (AIA-001) that the test suite, which exercises the
*subject-matter* capability, does not by itself provide.

*Reference: Regulation (EU) 2024/1689. This is a good-faith self-assessment,
not legal advice; re-verify against current delegated/implementing acts and
guidance before relying on it. Dates and phase-in are as understood at time of
writing (2026-07).*

## What RVND is

A local-first governance layer for AI agents: a deterministic core (regex
Privacy Lock, Ed25519 hash-chained audit log, policy/oversight gates,
egress-lock firewall) plus an **optional** AI-assisted component — the Tier C
semantic PII classifier, a small local-model ensemble that runs only when a
local backend is configured (default: a deterministic non-ML mock). RVND does
not ship or serve a model of its own; it consumes local models the operator
supplies and brokers calls to third-party cloud models through a fail-closed
proxy.

## Is RVND an "AI system" (Art. 3(1))?

Partly. The deterministic core is not an AI system. The Tier C classifier is a
machine-learning inference component and, where an operator enables it, brings
RVND within scope as a system that *includes* an AI component. The
classification below is written to that scope; the deterministic core is
governed by ordinary software/security obligations, not the Act.

## Role (Art. 3, Art. 25)

**Provider** of the RVND tool. Operators who run it to govern their own agents
are **deployers** of RVND and, typically, providers/deployers of the *agents*
RVND governs. RVND is not a provider of a general-purpose AI model (Art.
51–55): it does not supply a model, it orchestrates the operator's. Materially
modifying a bundled model would re-open the role question for that operator.

## Risk tier

- **Not a prohibited practice (Art. 5).** RVND performs no social scoring, no
  biometric categorisation, no emotion recognition, no untargeted scraping, no
  manipulative/exploitative technique. Its design intent is the opposite —
  attenuating autonomous action toward human oversight. The plugin's
  fail-closed linter and `test_operate_prohibition_hard_stop` enforce that a
  prohibited act is refused at any autonomy grade.
- **Not high-risk (Annex III / Annex I).** RVND is not a safety component of a
  regulated product, and it is not itself the decision-maker in an Annex III
  domain. It is oversight *tooling*: it helps a human supervise other systems.
  The Tier C classifier's only effect is to flag suspected PII for redaction
  before egress — a data-protection safeguard, not a decision producing legal
  or similarly significant effects on a person.
- **Limited/minimal-risk, with an Art. 50 transparency touchpoint.** The
  operative classification is minimal-risk for the core and limited-risk where
  the AI-assisted tier is enabled. The one transparency obligation that can
  attach is Art. 50: where RVND emits AI-originated content for external
  publication, it must be marked as such.

## Obligations that do attach, and where they live

- **Art. 50 transparency.** RVND's disclosure envelope marks AI-origin content
  and refuses an external publish without named parties. Covered by
  `test_disclosure.py` (NO-GO without disclosure; content-tamper and
  signature-tamper both detected).
- **Art. 14 human oversight (as a design principle RVND embodies, and offers
  its deployers).** The oversight dial computes a human-in-the-loop level, a
  prohibited act is an unconditional refusal, and — the property most easily
  overlooked — a human can STOP an operation in flight: the party kill-switch
  refuses an agent's next governed step the moment a supervisor flips it.
  Covered by the oversight suite and `test_ai_act_human_oversight_068.py`
  (in-flight stop, auditable intervention, reversible re-activation).
- **Art. 12 record-keeping.** The signed, hash-chained audit log records every
  governed step and every oversight intervention; interventions are appended
  PartyStatus events, not silent flags.
- **Art. 4 AI literacy.** Applies to operators running the AI-assisted tier;
  the deploy quickstarts and this document support that duty. Operator
  training records are the deployer's responsibility.

## Open item deliberately left as a design decision

The party **kill-switch is currently reversible** (the status projection is
latest-wins, so a later `active` event reactivates even a `killed` agent). For
an Art. 14 stop control this is defensible — a human can both stop and resume
— but if `killed` is intended to be *terminal*, that is a governance decision
to make explicitly rather than inherit from the projection. Recorded here and
pinned by `test_ai_act_human_oversight_068.py::test_reactivation_restores_operation`
so the behaviour cannot change silently.

---

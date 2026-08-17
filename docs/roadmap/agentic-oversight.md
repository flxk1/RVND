<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Roadmap — delegated authority and agentic oversight

Status: **draft, not committed scope.** This is a research and engineering
roadmap across the Loomground family and RVND. It names what exists, what is
missing, and what would have to be true for a claim to be made. It commits no
release date and no version.

Each plane repository carries its own slice of this roadmap, scoped to what that
plane owns. This document is the family-level map and RVND's own slice.

---

## 1. The thesis

Nine open problems in agentic oversight are set out in §3. They are usually
treated as nine research programmes. In this architecture they are better read as
**one missing object seen from nine angles.**

The delegation relation is conventionally drawn as:

```
Principal ──mandate──▶ Agent ──permissions──▶ Actions ──effects──▶ World
```

Loomground already models three of those four arrows, in language, today:

- **permissions** — grants, autonomy grades, gates, guards, prohibitions,
  reservations, quorum (`loomground-governance`, specification §6–§7);
- **the principal relation** — the `on-behalf-of` binding, the acyclic principal
  chain, the no-amplification invariant, party resolution along the chain, and
  human-rooted answerability (specification §6);
- **effects** — the signed hash chain and its `effect-observed` counter-entries,
  which reconcile what was *decided* against what actually *happened*.

It does not model the **mandate**: the purpose for which authority was conferred.
Authority is recorded; the reason for it is not.

That single absence is why the hardest of the nine problems resist a solution:

- *Semantic monitoring* (§3.3) is not merely hard but **undecidable as posed** —
  "the agent pursued the wrong goal" is a proposition about the mandate, and
  there is no mandate to evaluate it against.
- *Dynamic escalation* (§3.2) needs the risk of **continuing**, which cannot be
  estimated without knowing what continuing is for.
- The *principal–agent problem* (§3.5) is definitionally mandate divergence.
- *Meaningful human control* (§3.9) lists "ability to specify goals" as one of
  its constituents; the other six are already modelled here, and that one is not.
- *Minimum information for oversight* (§3.1) has a candidate answer only once a
  mandate exists: the mandate, plus the places the reasoning could not settle.

**Workstream A is therefore the mandate.** Most of the rest composes from it and
from machinery that already ships.

### Four further positions this roadmap takes

**(i) Oversight information must be selected by the reasoner's own uncertainty,
not sampled from its output.**

The empirical result motivating this work — that ordinary action traces raise a
user's confidence without improving their ability to detect agent errors — is a
finding *against* shipping traces. A trace is a sample of output. It grows with
action count, it is uniform in density, and it puts the reader's attention
everywhere except where the system was unsure.

The family already computes a better object. `loomground-solver` records the
**negative space** of a decision (unfired defeaters, untriggered exceptions,
gaps), the **epistemic status** of premises (contested, presupposed, unknown),
the **undecided** set of the decision space, and `gap_fork` escalations. That
material is bounded by the number of things the reasoning could not resolve — not
by the number of actions taken — and it is precisely where errors live.

This roadmap calls that object the **oversight brief** (Workstream C).

**(ii) The oversight regress terminates at a deterministic floor, not at a more
capable overseer.**

"Who oversees the oversight agent?" has an answer in this architecture, and it is
not "a bigger model". It is: *nobody oversees it directly.* The overseer emits a
**replayable proof object**, and a model-free checker re-derives it. Versum's
deterministic floor, the solver's `test_dependency_inversion` gate, `replay.py`,
and the published conformance vectors are that floor.

What a human then supervises is a small, fixed, model-free contract — a
supervision burden that does **not** grow with agent capability. That is a
falsifiable position, and Workstream I is where it is tested rather than
asserted.

**(iii) Corrigibility is already in the family's vocabulary.**

`loomground-deontic` ships the eight Hohfeld incidents, including the
power/liability and immunity/disability correlative pairs. Those are the formal
language of correctability:

| Relation | Reading |
|---|---|
| principal holds a **power** over the agent | the principal can pause, correct, constrain, terminate |
| agent is under a **liability** | the agent is susceptible to having its position changed — the correlative of that power |
| agent holds an **immunity** | the agent cannot be so changed |
| principal holds a **disability** | the principal *cannot* intervene — the correlative of that immunity |

Corrigibility failure is exactly the immunity/disability pair holding between an
actor and the root of its principal chain. `deontic.correlative` and
`deontic.opposite` already compute these relations. Corrigibility therefore
becomes a **structural well-formedness property of a policy graph**, checkable in
the same way the no-amplification invariant is checked today — not an aspiration
about model disposition (see the honesty note in §7).

**(iv) Autonomy becomes dynamic without becoming computed in-language.**

`vocabulary/grades.json` states that grade is "a configuration attribute, not a
token field and not guardable". `vocabulary/guard-domain.json` forbids "any
computed value", and says why: it is "the wall between an expressive notation and
a programming language."

That wall should stay up. The proposal

```
Autonomy level = f(risk, uncertainty, reversibility, context, competence)
```

is satisfiable without breaching it. Compute `f` **outside** the language, and
admit its result as **declared, provenance-bearing token properties** — exactly
how `risk` already works. The token gains `reversibility` and `uncertainty` as
declared, guardable, non-computed fields. The function lives in the host; the
language records what was declared, by whom, and on what basis. Re-grading is a
**fresh activation with a different token**, never a runtime mutation of a grade,
which keeps every autonomy change declarative and auditable.

---

## 2. What already exists

Stated plainly, so the roadmap is not credited with work already done.

| Capability | Where | Bears on |
|---|---|---|
| Acyclic principal chain, `on-behalf-of` | governance §6 | §3.5, §3.6 |
| No-amplification invariant (grants and grade) | governance §6 | §3.5, §3.7 |
| Party resolution along the chain; human-rooted answerability | governance §6 | §3.6 |
| Autonomy grades L0–L4, gate-owned threshold | governance §7.1 | §3.2 |
| Verdict lattice; `reserved`, `prohibited`, quorum, redress | governance §6–§7 | §3.2, §3.9 |
| Verdict-labelled ordered log trace | governance §7.4 | §3.1 |
| Negative space, attack topology, contradiction invariant | solver `fingerprint` | §3.3 |
| Epistemic status of premises; OPEN-dominant strict-AND fold | solver | §3.1, §3.3 |
| Decision space: accepted / undecided / rejected | solver `decision` | §3.2 |
| Gap fork — escalate rather than fabricate a resolution | solver | §3.3 |
| Validated derived topology (typed DAG, kind-based governance) | solver `topology` | §3.9 |
| Replayable provenance; verifier data pump | solver `replay`, `datapump` | §3.4, §3.8 |
| Span-grounded claims; provenance spine; typed nD systems | versum | §3.3, §3.4, §3.9 |
| Hohfeld incidents incl. power/liability/immunity/disability | deontic | §3.7 |
| Obligation runtime as dated, gated state | norm | §3.7 |
| Connection algebra, instrument lifecycle, escalate-don't-guess | legal | §3.6 |
| Signed hash chain; `effect-observed` counter-entries | RVND | §3.6 |
| Oversight certificate with reviewer-declared **assistance** | RVND | §3.4, §3.8 |
| Air-gap enforcement; fail-closed privacy lock | RVND | §3.7 |

The `assistance` field deserves emphasis, because it sets a precedent this
roadmap generalises. It records *how* a human review was formed — unaided, or
aided by a model of the same family as the system under review (a correlated
failure). Crucially, the runtime never infers it: an absent declaration reads
`UNDECLARED`, never `UNAIDED`, because "a system-minted `unaided` would be an
unfalsifiable claim about private human conduct."

Workstream E applies that same discipline symmetrically, to agents.

---

## 3. The nine problems, mapped

| # | Problem | Owning planes | Footing that exists | The gap |
|---|---|---|---|---|
| 1 | Scalability of oversight | solver, patchbay, console | negative space; epistemic status; `undecided` | no bounded oversight brief; no minimum-information contract |
| 2 | When to intervene | governance, solver, RVND | grades; risk; guards; reservations | grade is static configuration; no reversibility or uncertainty axis |
| 3 | Failures the agent does not flag | versum, solver | span claims; fingerprints; contradiction invariant | no mandate; no trajectory-to-mandate divergence detector |
| 4 | Reasoning vs. action signals | solver, versum, RVND | replayable provenance; span grounding | self-report ranked equal to grounded evidence; no monitor-independence record |
| 5 | Principal–agent | governance, legal | principal chain; attenuation | no mandate on the cord; no scope-of-authority doctrine |
| 6 | Attribution in multi-agent systems | RVND, legal, versum | signed chain; effect events; chain projection | record stops at the host boundary; not third-party verifiable |
| 7 | Corrigibility | deontic, governance, RVND | Hohfeld incidents; reserved; time stop | no interruptibility invariant; no revocation propagation |
| 8 | Human competence paradox | norm, RVND, console | oversight certificate; escalate discipline | verification gap unmeasured |
| 9 | Meaningful human control | versum, governance, patchbay | nD systems; verdicts; redress | MHC is not a registered coordinate system; unmeasured |

---

## 4. Workstreams

**Landed so far.** A1–A2 (mandate + attenuation), B1–B3 (`reversibility`,
`uncertainty`, re-grading as a fresh activation) and a new J (consignment and
transfer) are in the governance language, exercised by 14 conformance vectors.
F1 (the intervention profile) is in the deontic plane, D3 (root-presupposition
ordering) in the solver, and H1 (the MHC coordinate system) in versum. **A3 and
F2 remain**, and F2 is blocked on a design decision recorded in the governance
slice — a grant is over a `kind` at a gate and a `kind` has no target, so the
invariant as written has nothing to range over.

### A — The mandate  ·  A1–A2 landed, A3 open

*Problems 3, 5, 9; unblocks 1 and 2.*

- **A1 · `mandate` declaration** (governance). An attribute on the authority
  cord naming a declared purpose, exactly as `on-behalf-of` rides that cord
  today. It names; it does not compute. Lockstep: specification, grammar, schema,
  vocabulary, `llms.txt`, and a conformance vector must agree.
- **A2 · Mandate attenuation invariant** (governance). A delegate's mandate MUST
  lie within its delegator's, with the same fail-closed treatment as
  no-amplification. This is what makes a deep chain analysable: in
  `board → employee → enterprise agent → planning agent → procurement agent`, the
  mandate can only ever narrow. A chain that widens it is ill-formed and has no
  effect.
- **A3 · Mandate and trajectory as span-grounded claims** (versum). The mandate
  is anchored to an exact span of an exact source — the policy, the ticket, the
  instruction. Divergence is then *evidenced* rather than asserted, and the
  evidence is a citation.
- **A4 · Scope of authority** (legal). Actual and apparent authority, and acting
  beyond the mandate. Legal supplies doctrine as data and a bridge; the solver
  does the reasoning; legal grows none of its own.

### B — Dynamic autonomy, without computed guards  ·  B1–B3 landed

*Problem 2.*

- **B1 · `reversibility` on the token** (governance) — declared, ordered,
  guardable with the operators `risk` already permits.
- **B2 · `uncertainty` on the token** (governance) — likewise declared and
  provenance-bearing.
- **B3 · Re-grading is a fresh activation** (governance). Autonomy changes by
  re-entering a gate with a different token. No runtime grade mutation exists,
  so every autonomy change is journalled by construction.
- **B4 · The escalation calculus** (solver). Compose the
  risk/uncertainty/reversibility/context/competence tuple over the existing
  `proportionality` and `epistemic_status` layers. The output is a *declaration
  with a replayable derivation*, not a verdict.
- **B5 · Admission** (RVND). Admit a computed autonomy declaration into the token
  with its provenance. An underdetermined tuple escalates; it never defaults to a
  grade.

### C — The oversight brief

*Problem 1; the delivery surface for 3 and 9.*

- **C1 · `oversight_brief()`** (solver). A bounded selection over negative space,
  the `undecided` set, contested and presupposed premises, gap escalations, and
  mandate divergence. **Its size is a function of what went unresolved, not of
  how many actions were taken.** That is the scalability claim, and C4 is where
  it is tested.
- **C2 · Control topology view** (patchbay). Render the brief as a topology of
  principals, agents, monitors, checkpoints and escalation paths — not as a log.
- **C3 · Terminal brief** (console). The same object on the deterministic
  surface.
- **C4 · The evaluation gate.** *Every oversight surface ships with an
  error-detection measurement, or it does not ship.* The measurement is the rate
  at which a reviewer detects **injected** divergences — never self-reported
  confidence. **Confidence that rises without detection rising is recorded as a
  failure of that surface**, not as adoption. This gate exists specifically so
  the brief cannot quietly become the thing the trace already was.

### D — Semantic monitoring

*Problem 3.*

- **D1 · Trajectory composition** (versum). An action sequence composed into a
  process concept, using the composition grammars versum already has.
- **D2 · Divergence detection** (solver). Compose the trajectory and test it
  against the mandate through the existing `compose_paths`, `subsumption` and
  `cross_subsumption` layers. Divergence is either a failure to subsume, or
  literal satisfaction while a defeater fires.
- **D3 · Early-assumption propagation.** The characteristic agentic failure — an
  early assumption that propagates through fifty later actions — is already
  modelled: it is a `PRESUPPOSED` premise under the OPEN-dominant fold. Surface
  the **root presupposition**, not its fifty descendants. This is the single
  largest compression available to oversight: one line replaces fifty.
- **D4 · Proxy optimisation** (solver). The literal-compliance,
  purpose-violation case, through `interpret.audit`.

### E — Signal trust typing

*Problem 4.*

- **E1 · Falsifiability rank.** Not all oversight signals are equal, and the
  ordering is by *how they can be falsified*:

  ```
  replayable derivation  >  span-grounded claim  >  independently verified
  outcome  >  observed tool call  >  declared plan  >  self-reported reasoning
  ```

  Self-reported reasoning sits last for a stated reason: it is an unfalsifiable
  claim about a private mechanism. This is the same objection RVND already
  applied to a system-minted `unaided`, applied symmetrically to agents. A
  self-report may inform; it may never be the **sole** basis for a release.
- **E2 · Monitor independence** (RVND). A monitor drawn from the same model
  family as the system it monitors is a correlated-failure risk. Record it as a
  declaration; never infer it. Direct extension of the oversight certificate.

### F — Corrigibility  ·  F1 landed, F2 blocked

*Problem 7.*

- **F1 · Intervention position profile** (deontic). A profile over the existing
  incidents for the intervention verbs — pause, correct, constrain, terminate. No
  fourth operator and no new vocabulary; a profile over what ships.
- **F2 · Interruptibility invariant** (governance). Well-formedness: the root of
  an actor's principal chain retains a power over the intervention kinds, and no
  actor may be granted an immunity against them. Fail-closed, in the shape of the
  existing invariants.
- **F3 · Revocation propagation** (RVND). Revoking a delegator's grant attenuates
  every delegate along the chain, the propagation is journalled, and it reaches
  **already-running** work by binding revocation to the existing time-based stop.
- **F4 · Persistence and escape** (RVND). An agent that copies state or acquires
  resources outside the governed boundary has escaped revocation regardless of
  the graph. Name it in the threat model and gate what can be gated; say plainly
  what cannot.

### G — Attribution and the chain of agency

*Problem 6.*

- **G1 · The agency record** (RVND). A portable, signed, replayable
  reconstruction: who delegated what, to whom, under which mandate, with which
  verdict, producing which observed effect. Composed from the principal-chain
  projection, the signed chain, and the `effect-observed` entries — all three of
  which exist.
- **G2 · Cross-host chaining** (RVND). Today the record stops at the host
  boundary; a real chain crosses organisations. The specification already grounds
  delegation in RFC 8693 chained actor claims, which is the right shape to
  extend. The trust anchor is a genuine open dependency (§7).
- **G3 · Causation and responsibility** (legal). Attribution of an act along the
  chain, with the solver's `causal_construction` doing the reasoning and legal
  supplying doctrine as data.
- **G4 · Third-party verifiable export.** The record verifies **without the host
  that produced it**. That property, not the format, is what makes it the
  equivalent of a chain of custody.

### H — Meaningful human control, measured  ·  H1 landed

*Problem 9.*

The proposed constituents

```
Effective oversight = observability × intervenability × comprehensibility
                      × authority × timeliness
```

are a five-axis contextual coordinate system, which is exactly what versum's
typed, namespaced, versioned nD systems are for.

- **H1 · Register `mhc/v1`** (versum) as a typed nD system, by declarative
  configuration. No engine change.
- **H2 · The collapse rule** (solver). A product in which any zero collapses the
  whole **is** the OPEN-dominant strict-AND fold already in `issue_aggregation`.
  Reuse it; do not rebuild it. An axis at zero escalates.
- **H3 · Show the collapsing axis** (patchbay). The difference between oversight
  that exists formally and oversight that exists functionally is *which axis went
  to zero*, and that is what the operator must see.
- **H4 · Comprehensibility is not self-measurable.** It is the one axis that
  cannot be computed from the system alone, because it is a property of the
  human. It is measured through C4 or it is not measured at all.

### J — Consignment and transfer  ·  landed

*Problem 6; found by testing the language against a real protocol.*

A handoff moves material from one governed domain to another. The language
could say whether an action released and not **to whom**, so every governance
question about a handoff was a question about an absent term — while §6 already
spoke of "where the party differs *across a boundary*", naming a boundary it
never defined.

- **J1 · `consign`** on a terminal gate — where that gate's release goes. A
  consignee is a declared id, not a node: the four node classes stand, and no
  second master is introduced, because §7.3 already decides each egress path
  independently.
- **J2 · `transfer <kind> to <consignee> within {purposes}`** — what the
  material travels under, with an attenuation invariant that is the mandate
  rule on a **lateral** relation: a transfer is not a delegation, no principal
  chain forms, but an actor still cannot hand on a purpose it was not given.

*Limit, stated.* It records the purposes and bounds them by what the releaser
held. Whether a recipient honours them is conduct, outside the language.

### I — The regress floor

*Problem 8.*

- **I1 · State the termination argument** as a design document with a testable
  claim, not a slogan.
- **I2 · Strengthen replay** so an oversight decision is re-derivable **without
  the model that produced it**. This is the load-bearing engineering step: if it
  holds, the regress stops.
- **I3 · Conformance vectors for the oversight surface.** Governance §9 already
  records its interoperability criterion as **open**, because both existing
  implementations were authored within the same AI-assisted project. An
  independently produced implementation reproducing these vectors is what would
  close it. The roadmap should not claim otherwise.
- **I4 · Measure the verification gap** (norm, RVND, console). Report it as a
  quantity. Supervisor competence and assistance are **declared, never inferred**
  — the `UNDECLARED` discipline again.

---

## 5. Phasing

The order follows the family's own discipline: language first, deterministic
floor next, enforcement late, falsification last and non-negotiable.

| Phase | Theme | Contents | Needs a model? |
|---|---|---|---|
| **0** | Name the objects | A1–A3, B1–B3, F1–F2, H1 | No |
| **1** | Make them computable | D1–D4, B4, C1, H2, E1 | No (rails only) |
| **2** | Make them visible | C2, C3, H3, G1 | No |
| **3** | Make them enforceable | B5, F3, F4, G2, G4, E2 | No |
| **4** | Make them falsifiable | C4, H4, I1–I4 | Human subjects |

Phase 0 is language work under the lockstep gate and implies a governance minor
version. Phase 4 is not a postscript: **no oversight claim in this roadmap is
made until its Phase 4 gate passes.**

---

## 6. Acceptance gates

Each is falsifiable. A workstream is not complete when the code merges; it is
complete when its gate passes.

1. **Brief size is O(unresolved), not O(actions)** — measured over runs of 10,
   100 and 1000 actions on the same mandate. A brief that grows with action count
   has failed.
2. **Zero runtime grade mutations** — every autonomy change is a fresh, journalled
   activation (test, not convention).
3. **Injected-divergence detection rate** rises with the brief, and D3 surfaces
   the root presupposition rather than its descendants.
4. **Every signal carries a falsifiability rank**, and no release rests solely on
   a self-report.
5. **Mandate attenuation holds on every well-formed graph** — a conformance
   vector, and a widening chain is ill-formed.
6. **An N-hop cross-host chain reconstructs and verifies without the host.**
7. **No well-formed graph admits an actor immune to intervention** (conformance
   vector); revocation reaches running work within a bounded, measured time.
8. **The verification gap is reported as a quantity**; competence and assistance
   read `UNDECLARED` when absent.
9. **MHC axes are computed per decision**, and a zero axis escalates.

**Meta-gate.** Rising reviewer confidence without a rising detection rate is
recorded as a **failure** of the surface that produced it. This gate applies to
every surface in Workstreams C and H, including surfaces this roadmap is fond of.

---

## 7. Risks, and what this roadmap does not claim

- **The mandate could degenerate into free text.** Mitigation: it is span-grounded
  in versum, attenuation-checked in governance, and must be *consumable by the
  divergence detector*. A mandate the detector cannot use is not a mandate.
- **The brief could become the new trace.** This is the most likely failure mode,
  because it is the comfortable one. C4 is the only defence, which is why it
  gates rather than follows.
- **Token inflation.** Every added property must be exercised by a conformance
  vector under the lockstep gate, or it does not go in.
- **The corrigibility invariant is structural, not behavioural.** It constrains
  what a policy graph may *grant*. It prevents conferring an immunity against
  intervention; it does not and cannot prevent a model from behaving badly, and
  it says nothing about a system acting outside the governed path. Stated
  bluntly so the invariant is not over-read.
- **Cross-host attestation needs a trust anchor** the family does not supply.
  Named as a dependency rather than designed around.
- **Enforcement scope is unchanged.** RVND governs actions routed through its MCP
  server or governed `operate()` path. Nothing in this roadmap widens that, and
  host-wide containment still requires the OS-level egress lock.
- **§9 interoperability remains open.** Two implementations authored inside the
  same project give differential checking, not independence. I3 does not change
  that by itself.

---

## 8. RVND's own slice

What this repository owns, as distinct from the planes it consumes:

| Item | Workstream |
|---|---|
| Admission of computed autonomy declarations, with provenance; escalate on an underdetermined tuple | B5 |
| Revocation propagation along the principal chain, reaching running work | F3 |
| Persistence-and-escape gating, and the threat-model entry for what cannot be gated | F4 |
| The agency record, and its third-party verifiable export | G1, G4 |
| Cross-host chaining over the RFC 8693 shape | G2 |
| Monitor-independence declaration on the oversight certificate | E2 |
| Verification-gap reporting | I4 |

RVND adds no reasoning of its own for any of these. Divergence detection,
escalation calculus and the brief are the solver's; mandate storage and the MHC
coordinate system are versum's; the declarations and invariants are governance's.
RVND is the terminal runtime: authorization, oversight, custody, the signed
chain, and enforcement.

---

## 9. Related documents

- `docs/concepts/knowledge-pipeline.md` — the one-way plane pipeline this
  roadmap works within.
- `docs/concepts/architecture-model.md` — the projection model.
- `docs/reviews/threat-model.md` — where F4 and G2 entries land.
- The per-plane slices of this roadmap in each family repository.

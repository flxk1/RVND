<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Loomground & RVND — general programme roadmap

Status: **draft.** Distinct from `RVND/docs/roadmap/agentic-oversight.md`, which covers
one strand (S7) in depth. This is the whole programme.

---

## 1. Where the family stands

| Repository | Version | Maturity | Note |
|---|---|---|---|
| `loomground-governance` | 0.8.2 | spec **stable**, pre-1.0 | language card status `stable`; lockstep gate green |
| `loomground-deontic` | 0.1.3 | draft | two boundary decisions open before 1.0 |
| `loomground-solver` | 0.4.0 | working | 463 tests; dependency-inversion gate is the universality proof |
| `loomground-versum` | 0.13.0 | working | 339 passed / 10 skipped; evidence ledger verified 2026-07-24 |
| `loomground-norm` | attr | working | all 9 modules implemented; 24 tests is thin coverage |
| `loomground-legal` | attr | early | data + bridges only, by design |
| `loomground-patchbay` | — | release-gated | 98 tracked files; gate green |
| `RVND` | attr | **beta** | ~5840 tests |
| `rvnd-console` | 0.0.1 | early | surface may change |

The shape is unusual and worth naming: **the specification is more mature than the
runtime.** Governance is stable at 0.8.2 while RVND is beta. That is the right order
for this project — the language is the artifact with the longest half-life — but it
means the programme's credibility rests on the spec, and the spec's central claim is
the one still open (S2).

---

## 2. Eight strands

### S1 · Language to 1.0

Governance is `stable` at 0.8.2 but pre-1.0, where a minor version may still change
compatibility. Reaching 1.0 means freezing the node/cord vocabulary, the token, the
verdict alphabet, the declarations, and the grade axis.

The oversight roadmap (S7) proposes additions to exactly those surfaces — a `mandate`
attribute, `reversibility` and `uncertainty` on the token, an interruptibility
invariant. **That is a sequencing constraint, not a conflict:** either those land
before 1.0, or 1.0 ships without them and they wait for 2.0. Deciding which is the
single most consequential scheduling call in the programme.

Recommendation: land the Phase 0 language work first, then freeze. The additions are
small, they are lockstep-disciplined, and shipping 1.0 without a mandate would freeze
the gap this programme exists to close.

**Status.** The `mandate` attribute and its attenuation invariant have landed —
lockstep across ten artefacts and five vectors, with all 52 vectors reproducing and
the 47 pre-existing ones byte-identical, so a graph declaring no mandate is
unconstrained. The remaining Phase 0 language items are `reversibility` and
`uncertainty` on the token, and the interruptibility invariant. The freeze decision is
therefore no longer hypothetical: 1.0 can now ship *with* a mandate whenever the
remaining two are settled or deliberately deferred.

### S2 · Conformance independence — the open claim

Specification §9 defines interoperability as *two implementations produced
independently of each other reproducing every vector*, and records that criterion as
**open**: both existing implementations were authored within the same AI-assisted
project, so they provide differential checking, not independence.

This is the most important unresolved item in the family, and it is not a coding
problem. No amount of internal work closes it. It requires an implementer outside the
project reading only the published artefacts — `standard/spec/`, the EBNF grammar, the
schemas, the vocabulary — and reproducing the vectors.

It is also the highest-leverage academic hook the programme has (see the publication
roadmap, P7): an independent implementation is simultaneously a scientific
requirement, a credibility event, and a natural collaboration.

### S3 · Pipeline integrity

The one-way pipeline — **Language → Ingest → Versum → Solver → Patchbay → RVND** —
with Versum as the single persistent knowledge plane is the family's strongest
architectural commitment, and it is largely enforced today (reasoning reads Versum
only and fails closed on an unindexed workspace).

Remaining: workspaces still on the explicitly-labelled `legacy-pair-overlay` need
migrating. The label is honest, which is good practice; the debt is still a debt.

### S4 · Dependency hygiene and release order — **first pass landed**

`loomground-norm` and `loomground-legal` could not install in CI at all. Both declare
`loomground-solver>=0.2,<0.3` and neither had a resolvable pin, so `pip install .`
went to the index, found no `loomground-solver`, and the job died before a single test
ran. Two planes were permanently red, which means **their suites had never gated a
change**.

Both are now pinned at `solver-v0.2.1` — the revision RVND itself pins, so the family
resolves to one solver. The declared ranges were left alone: they are correct, and both
suites pass against 0.2.1. Verified by replaying the CI sequence in a clean virtual
environment (dev requirements, package install, pytest, build).

Two records were also wrong, in opposite directions, and are corrected: legal claimed
it needed `>=0.3` for `RelationAlgebra` (it is present in 0.2.1), and justified its
`<0.3` ceiling on the ground that no solver 0.3 was cut (0.3.0 and 0.4.0 both exist).
The ceiling is kept on the accurate ground — the family's consumed line is 0.2.x
because RVND pins it there, so a higher floor would break the shared install,
`loomground-ingest` included.

A second bug surfaced underneath: norm's `conftest.py` resolved the family root two
levels up, from a `work/`-nested layout that no longer applies, so in a plain sibling
checkout every sibling import failed and all three test files skipped. A green local
run meant nothing. Fixed to match legal's shim; the local run goes from 3 skipped to
24 passed.

**Still open.** The family has no distribution story: the built distributions declare
index-installable ranges that no index can satisfy, and every consumer resolves through
git pins instead. That is workable but it means `pip install loomground-legal` cannot
work for anyone outside the project. Deciding it — publish to an index, or state
plainly that these are git-installed packages — is the remaining half of this strand.

### S5 · The prerequisite that turned out not to exist

This strand was written as "skeleton completion": `loomground-norm`'s `rule_registry`
and `obligation_scheduler` were said to carry unfinished host seams, blocking S7's
obligation work (N1).

**That was wrong, and it came from a stale README rather than from the code.** Both
modules are implemented — span placement, re-pinning, orphan tracking and search in
one; deadline arithmetic and per-state follow-up proposals in the other — with their
host couplings already inverted into injected ports, and eighteen tests between them.
No TODO, no `NotImplementedError`, no stub body. What the README called "the port it
is waiting on" is the plane boundary working as designed: `rule_registry` deliberately
does not anchor norms onto governing instruments (legal-domain), and
`obligation_scheduler` deliberately only proposes (governance). The labels are
corrected upstream.

**N1 was never blocked**, and the sequencing below no longer holds S7 behind this.

What is real, and narrower: twenty-four tests across nine modules. It went unnoticed
because a `conftest` root-resolution bug made the suite skip rather than run, so a
green local run on that plane meant nothing. Coverage is the precondition for trusting
the plane; it is not a workstream.

### S6 · Runtime maturity — beta to 1.0

RVND's honesty about its own scope is a feature and should survive to 1.0: it governs
actions routed through its MCP server or governed `operate()` path, host-wide
containment requires the OS-level egress lock, tamper-evidence holds against a
key-directory adversary only with the opt-in protections, and erasure cannot recall
copies that already left.

To 1.0: close the threat-model items, make the opt-in key protections the default
where feasible, and keep every scope limit stated on the surface rather than in a
footnote.

### S7 · The oversight programme

Nine problems, workstreams A–I, five phases, falsifiable gates. Covered in full in
`RVND/docs/roadmap/agentic-oversight.md` and its eight per-plane slices.

Its Phase 0 intersects S1; its Phase 4 (measurement) is the source of publication P3.

Landed so far: the mandate and its attenuation invariant (A1–A2, governance); the
intervention profile that makes corrigibility a checkable Hohfeld relation rather than
an aspiration (F1–F2, deontic); and root-presupposition ordering, which reports one
cause where the fold reported fifty consequences (D3, solver).

### S8 · Adoption surface

Console, Patchbay, the packaged skills and plugins, published policy-pack import. The
strategic question here is not features but **who the first outside user is** — and
the answer that most helps S2 is *an implementer*, not an end user.

---

## 3. Sequencing

| Order | Strand | Why here |
|---|---|---|
| 1 | **S4** dependency hygiene | First pass landed; distribution story still open |
| 2 | **S7 Phase 0** language additions | Partly landed — the mandate is in; `reversibility`/`uncertainty` and the interruptibility invariant remain |
| 3 | **S1** language 1.0 | The freeze decision is now live: the mandate exists |
| — | **S5** norm coverage | Not a prerequisite (see S5); worth doing before leaning on that plane |
| 5 | **S2** independence | Runs in parallel from now; gated on outside people, not on code |
| 6 | **S3** overlay migration | Ongoing |
| 7 | **S7 Phases 1–3** | The build |
| 8 | **S6** runtime 1.0 | After enforcement work lands |
| 9 | **S7 Phase 4** measurement | Gates every oversight claim, and produces P3 |

S2 is deliberately not last. It has the longest lead time of anything here because it
depends on someone else's calendar.

---

## 4. Debts, stated plainly

- **Two planes had never been CI-verified** (S4) — now pinned and verified; the family's
  distribution story is still undecided.
- **The interoperability criterion is open** (S2), and no internal work closes it.
- **`loomground-norm` has 24 tests across 9 modules.** The modules are implemented; the
  coverage is thin, and a `conftest` bug hid that until it was fixed.
- **`loomground-norm` has only 3 test files**, and until the shim fix none of them ran.
  Coverage on that plane is thin regardless of the fix.
- **Versum's concept layer is partly experimental** — normalization, canon convergence, typed pair compositions and model deepening are marked experimental, not operational.
- **Enforcement scope is narrower than "governance" suggests** to a casual reader. The README is honest; the risk is downstream summaries that are not.

---

## 5. What would make this programme succeed

Three things, in order of how much they matter and how little they are about code:

1. **An independent implementation.** It converts a single-author specification into a
   standard-shaped artefact. Nothing else available does that.
2. **One measured oversight result.** A demonstration that uncertainty-selected
   information improves error detection where traces do not would be the programme's
   first empirical claim rather than its first design claim — and the roadmap's
   meta-gate means a null result is also a publishable, honest outcome.
3. **Keeping the honesty discipline.** The `UNDECLARED`-not-`UNAIDED` reasoning, the
   escalate-don't-guess rule, the explicit `legacy-pair-overlay` label, the open §9
   status. That discipline is the most distinctive thing here and the easiest to lose
   under pressure to claim more.

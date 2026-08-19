<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Session handoff — agentic-oversight roadmap

**From:** remote Claude Code session `session_01K4mrKgpmWpZopYgMQsoQuD` (Anthropic cloud, iOS-initiated)
**Date:** 2026-08-18
**For:** local Claude Code sessions continuing this work

This remote session could only act inside the nine repositories it was provisioned
with. Two classes of work are therefore left for a local session: **anything that
creates a repository or publishes to an index** (account-level; needs your own
credential), and the follow-on wiring that depends on it.

---

## 1. Done — merged to `main`, do not redo

All nine roadmap PRs merged; two releases cut and tagged.

| repo | version | what landed |
|---|---|---|
| loomground-governance | **0.9.0** (`dab4c35`) | `mandate` + attenuation; `reversibility`/`uncertainty` on the token; `consign`/`transfer` + transfer-attenuation; §7.1 named as where correctability lives. 62 conformance vectors. |
| loomground-solver | **0.5.0** (`1a3637f`) | `epistemic.root_causes` (D3). **The six oversight modules were removed before release** — see §2. |
| loomground-versum | 0.13.0 | MHC as a registered nD system; delegation profile; `trajectory`. |
| loomground-deontic | 0.1.3 | intervention profile over the existing Hohfeld incidents. |
| loomground-norm | 0.1.0 | two modules + the solver pin that first got its CI green. |
| loomground-legal | 0.2.1 | roadmap slice. |
| loomground-patchbay / rvnd-console | — | roadmap slices. |
| flxk1/RVND | — | programme roadmap; `test_gate_cap_closure.py` (F2 enforcement). |

Roadmap Phases 0 and 1 are complete. Every item carries a commit; the RVND
roadmap (`docs/roadmap/agentic-oversight.md`) has the full table.

---

## 2. The six extracted repos — LOCAL SESSION OWNS THIS

Six narrow packages were built here, one question each, then **removed from
solver before 0.5.0 shipped** (so no released solver ever contained them). They
are committed locally under `/home/user/` and captured in the tarball
`loomground-oversight-planes.tar.gz` (git history intact) that was sent to the
user in-chat.

| repo | question | module | tests | pins solver |
|---|---|---|---|---|
| loomground-mandate | Did this run serve the purpose it was given? | divergence | 22 | v0.4.0 |
| loomground-escalation | How much autonomy do these factors leave? | escalation | 31 | v0.4.0 |
| loomground-proxy | Does this measurement still stand for what it claims? | proxy | 26 | v0.4.0 |
| loomground-falsifiability | How could this evidence be shown wrong? | falsifiability | 16 | v0.4.0 |
| loomground-collapse | Which term took this conjunction to zero? | collapse | 14 | v0.4.0 |
| loomground-brief | What is the minimum a supervisor must read? | brief | 16 | **v0.5.0** |

**All 125 tests passed here in a clean venv.** They depend only on
`loomground-solver` (shared verdict, OPEN-dominant fold, injected ports); no
sibling imports another; nothing imports back down into solver.

**Known caveat that will bite CI if unaddressed:**
- `loomground-brief` needs `epistemic_status.root_causes`, which only exists in
  **solver 0.5.0**. Its `pyproject.toml` declares `loomground-solver>=0.5,<0.6`
  and its `requirements-dev.txt` should pin the **`solver-v0.5.0` tag (`1a3637f`)**
  — it currently pins solver `main@c36866b`, which works but is not a release tag.
- The other five pin `solver-v0.4.0` (`081dbad`), range `>=0.4,<0.5`.
- Every repo's `requirements-dev.txt` also pins governance `9e91cf8d` (0.8.2) and
  deontic `6fc6df57`. Those satisfy solver 0.4.0's `governance>=0.8,<0.9`. If you
  bump brief to a solver that requires governance 0.9, bump its governance pin too.

**To publish (local):**
```bash
tar xzf loomground-oversight-planes.tar.gz
for r in mandate escalation proxy falsifiability collapse brief; do
  (cd loomground-$r && gh repo create flxk1/loomground-$r --public --source=. --push)
done
```
Then confirm each repo's CI is green (reuse + 3.10/3.14 verify + build).

> Note from the user during this session: initial pushes had mixed test results.
> Treat the tarball as the source of truth — it carries the KIND_ORDER export fix
> and the brief pin bump. Reconcile any already-pushed repo against it.

---

## 3. Open — wiring that depends on §2

- **Add 6 pins to RVND** (`pyproject.toml`): once the repos exist and are green,
  pin each by git SHA. RVND's `resolve-pins` CI fails against non-existent repos,
  so this cannot land before §2.

---

## 4. Open — decisions the user is holding

- **RVND release chain (HELD by user).** To move RVND off its current
  solver 0.2.1 / governance 0.8.2 pins onto current releases:
  1. bump `governance>=0.8,<0.9` → `<0.10` in **solver** and **versum**, release each;
  2. bump `solver>=0.2,<0.3` → `<0.6` in **norm** and **legal**, release each;
  3. repin RVND to governance 0.9.0 and solver 0.5.0.
  Four intermediate release PRs, strictly in that order. Nothing is broken today —
  RVND's pins are internally consistent; this is forward motion, not a fix.

- **RVND: four uncapped gate paths.** `cross_workspace`, `workspace_orchestrate`,
  `workflows`, `adapters/norm` reach `action_gate.gate` without a Breaker cap.
  `server/tests/test_gate_cap_closure.py` registers them (fails if the set grows).
  Fixing = routing through `cap_grade` against the acting agent's Breaker state —
  a behaviour change on two live MCP tools (`cross_workspace_read`,
  `workspace_orchestrate`, both default L2). Decision, not cleanup.

- **Phase A4 / G3.** Both are the legal plane supplying doctrine as data (scope of
  authority; causation/responsibility). Decision: ship as a corpus in
  `loomground-legal`, or as a profile the solver reads through a port. They sit in
  no roadmap phase until that is chosen.

- **`loomground-falsifiability`: `OBSERVED_TOOL_CALL`.** The last deployment
  concept in a shipped ordering — same shape as the escalation ladder that was
  made caller-supplied. Cheap to revisit now that it's its own repo.

---

## 5. Facts a local session will want

- **Tarball:** `loomground-oversight-planes.tar.gz` — six repos, `.git` intact.
- **Tags:** `solver-v0.5.0` = `1a3637f`; `loomground-governance-v0.9.0` = `dab4c35`.
- **RVND current pins:** solver `f8ac006` (0.2.1), versum `1147d7f`, governance
  `b69e0e1` (0.8.2), deontic `c93f4de`, legal `3638910`, norm `72f3962`, plus
  ingest/factual/epistemic.
- **Commit convention (CI-enforced):** subjects ≤72 chars; NO AI co-author
  trailer; end body with `Assisted by Claude (Anthropic); not an author or
  copyright holder.` Branch names `claude/<slug>`.
- **Every repo is REUSE/SPDX clean** — keep it that way (Apache-2.0 code,
  CC-BY-4.0 prose).
- **The extraction principle** (why the six moved out): the kernel's claim is that
  it holds no subject areas. Agentic oversight is a subject area. The
  dependency-inversion gate does not police subject vocabulary arriving as
  ordinary code; these six were exactly that. Keep new oversight vocabulary in its
  own repo above solver, not inside it. `epistemic.root_causes` stayed because
  ordering a premise graph is reasoning, not oversight.

---

## 6. Session hygiene

- No scheduled triggers remain (checked: list is empty).
- All nine PR subscriptions closed automatically on merge.
- Nothing is mid-flight or waiting on the remote session.

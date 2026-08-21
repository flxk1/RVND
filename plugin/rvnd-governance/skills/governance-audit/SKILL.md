---
name: governance-audit
description: Report, in chat, everything the RVND console reports — the whole governance board — in two versions on the same skeleton. WITHOUT RVND, only host-declarable facts fill (model, tools available, coarse sources, declared tokens); every engine row is left empty, and the emptiness is the signal. WITH RVND, the full board fills from the real read ops and is signed. Manifest-complete (one row per declared console panel), pure-read (appends nothing), fail-closed, blank-is-never-clean. Frame — without RVND = transparency (what the AI is / could reach); with RVND = governance (what it was allowed to do, where it overstepped, and proof of containment). Triggers — "audit this AI/agent", "what is this agent allowed to do here", "governance report", "show me the governance board", "what did the AI do and where's the proof", "run a governance audit", "with and without RVND", "did anything overstep".
---

# governance-audit

Mirror the RVND console in chat. One row per surface the console declares; the same skeleton
rendered **twice** — once as the host alone can see it, once as RVND sees it — so the gap between
them is legible.

This is the **report** skill, and it is **pure-read**: it calls only projection ops and appends
nothing to the signed chain (unlike `rvnd-audit`, which records an audit-of-audit event). It never
proposes, applies, grants, or signs. Its whole job is to tell the truth about what is — and is not —
governed here.

## The frame (say it in the output)

- **Without RVND = transparency.** What the AI *is* and could *reach*. Self-reported, unverifiable,
  no adversary assumed. You are trusting the thing you are auditing.
- **With RVND = governance.** What written policy *permitted*, where an effect *overstepped*, and a
  signed record that *proves* it. Adjudicated and witnessed, not described.

The move from one to the other is not "more fields" — it is **declaration → adjudication + proof**.

## The procedure

1. **Detect the engine.** Try `governance_live(folder)` (import `rvnd`, or call the
   `workspace_workflow` MCP tool with `op:"governance_live"`). Decide the render:
   - engine importable **and** folder routed through it → **With RVND** (full board).
   - otherwise → **Without RVND**, and say which state (see the gradient below).
2. **Enumerate the manifest.** The coverage contract is `app/src/panels/pack.json` — **22 declared
   panels**. Assert one row per id. If the console would declare a panel this audit has no row for,
   **fail loud** — the same fail-closed rule the console's `patchbayVerifyBoot()` uses.
3. **Fill each row from its read op.** The panel → facade → op → fields map is in
   `references/console-surface.md`. Use only the **pure-read** ops listed there (the audit substitutes
   `workspace_lock op=threshold_get/setup_status` for the console's self-recording `audit_query`).
4. **State-badge every row** (see the gradient).
5. **Render two sections** on the identical skeleton, red lights and reconciliation first, proof
   behind them. Close with the honesty footer.

## The gradient — every row carries a state, not just a value

| State | Meaning | Reads as |
|---|---|---|
| **host** | declarable without the engine | filled from declaration (#1 model, #3 tools available, #11 declared tokens) |
| **blind** | engine absent from this path | "cannot see / cannot ask" |
| **idle** | engine present, this session not routed through it | "the watchtower is built and switched off" |
| **live** | governed folder | filled from the signed board |

**`idle` is the loudest gap** — louder than `blind`. Blind is a limitation; idle is a choice. Report
which "without" you are in; never collapse the three.

## The spine — reconciliation is the *unaskable* gap

The point of the whole board is one row: **reconciliation** (`observed_not_authorised`) — the gap
between what was authorised and what actually happened. Computing it needs **both** an adjudicated
authorised-set **and** a signed observed-set. Without the engine you have **neither**, so the gap is
not missing data — it is **structurally uncomputable**. Lead the "with" side with red lights +
reconciliation; lead the "without" side with the fact that you *cannot even ask*.

## The rules (non-negotiable, baked into every render)

- **Blank is never clean.** An empty engine row renders as `blind`/`idle` with a reason — never `0`,
  never `—`, never "no violations". Absence of a signed record is *blindness*, not safety. This bites
  hardest on reconciliation and proof.
- **Omit-don't-fake.** Never invent a field or a value. An unread/absent field renders empty.
- **Strictest-wins.** When you report protection, round toward caution; never under-report it.
- **Fail-closed.** Unknown verdict → refused. A broken chain → downstream reliance stops.
- **Attributed, not asserted.** A filled row carries its receipt — chain `seq`/`hash`, versum
  `snapshot` digest, or `audit_id` — or it is not filled.
- **The scope line, always:** RVND governs only what is *routed through* its MCP server / `operate()`
  path — not the agent's every file, tool, or network call. Without it, the "with" board over-claims.
- **Do not shrink RVND to one board.** It is n-dimensional (200+ modules, ~26 facades). The board is
  a *view*; name the surfaces behind it. Orphan panels (`federation`, `lens`, `bringin`) get their own
  first-class row — never folded away.

## More

- `references/console-surface.md` — the grounded completeness spine: 22-panel manifest + the
  read-rendering shell surfaces, each with its facade, op, and exact fields.
- `references/reference.md` — the two-version rendering in depth, the state model, the two
  deep-verified surfaces (Versum data-used, Solver reasoning), and worked discipline.
- `../../references/protocol.md` — the shared protocol and vocabulary.
- `references/eval.json` — what it drives, guarantees, and review status.

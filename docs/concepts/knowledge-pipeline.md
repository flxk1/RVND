# The knowledge pipeline — Language → Ingest → Versum → Solver → Patchbay → RVND

RVND does not hold policy knowledge in its own store. Knowledge flows through a
fixed, one-way pipeline whose single persistent knowledge plane is **Versum**:

```
Language        governance / deontic / any registered future grammar
  │             (a policy written in the Loomground language)
  ▼
Ingest          PolicyIngester selects the registered grammar and produces a
  │             neutral, span-grounded Subgraph. It does NOT persist.
  ▼
Versum          the ONLY persistent knowledge plane: span-anchored claims and
  │             concept compositions. versum_writer() is the one write door.
  ▼
Solver          reasons over Versum knowledge, projected onto the flat
  │             Federation-5D edge algebra (structural/causal/intentional/
  │             temporal/relational) via the neutral reasoning.interop boundary.
  ▼
Patchbay        the pinned presentation layer — a wiring view of the reasoned
  │             governance graph. It is not a second reasoning or knowledge engine.
  ▼
RVND            the terminal runtime: authorization, oversight, custody, the
                signed audit chain, and enforcement — the runtime decision that
                Versum deliberately refuses to make.
```

## Why each boundary matters

- **Language → Ingest.** Governance is the authoritative policy grammar; deontic
  classifies normative content; the design is open to any future grammar. Ingest
  chooses the grammar and emits a neutral `Subgraph`
  (`server/src/workspaces/ingest/policy.py`, `PolicyIngester`).
- **Ingest → Versum.** Ingest never persists on its own. `versum_writer()` hands
  the subgraph to the Versum-provided sink; Versum is the single knowledge-store
  door. The published policy-pack path uses the same door
  (`server/src/workspaces/published_policy_pack.py`).
- **Versum → Solver.** Versum is a span-grounded claim/concept graph, not a
  triple store; it holds knowledge but does **not** parse, validate, evaluate, or
  authorize it. RVND reads Versum edges and passes them to the real Solver
  dimensions and `compose_paths()`
  (`server/src/workspaces/adapters/versum/solver_source.py`).
- **Solver → Patchbay → RVND.** The Solver's reasoning becomes the Patchbay
  wiring view; RVND consumes the pinned Patchbay presentation layer (an immutable
  `loomground-patchbay` commit, verified by the patchbay-consumption gate) and
  connects it to its own operations and enforcement.

## Fail-closed properties

- **Versum is the only knowledge plane.** `reason()` and `workspace_query()` read
  Versum and **refuse an unindexed workspace** ("index the folder first") rather
  than falling back to any non-Versum source. The former legacy pair-overlay was
  retired so nothing reasons off a source other than Versum. (A separate local
  fact store — `workspace_remember` / `pairs_search` — persists typed triples on
  the signed log for recall; it is not the knowledge plane and does not feed
  reasoning.)
- **"Any future grammar" is not automatic loading.** A new language must
  implement the Ingest `Ingester` contract, be registered, and be explicitly
  admitted by RVND as an allowed language contract. Unknown grammar packages are
  not loaded — the correct fail-closed reading.

## What lives where

- Versum owns span-grounded claims, concepts, 5D+nD storage, fingerprints, and
  retrieval. RVND adds authorization, custody, the signed audit chain, and
  governance-specific read-time behavior on top.
- The authoritative Versum-side description of the Loomground interchange
  boundary is in the `loomground-versum` repository
  (`docs/architecture/loomground.md`).

---

*Assisted by Claude (Anthropic); not an author or copyright holder.*

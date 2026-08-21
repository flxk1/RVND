# Ingest cascade — one graph, local-first

The shared architecture every skill follows. It exists so a skill never invents a governance
format and never answers before the engine has had its turn.

## One object

Every skill operates the **same dimensioned `Subgraph`** — the object the versum plane stores and the
RVND engine ingests. A skill does **not** hand-build a `.lg` patch or a bespoke JSON shape. It
produces (or refines) the shared Subgraph through the **`loomground_ingest`** plane, so the graph is
RVND-ingestible by construction.

- Facets: `loomground_ingest.ALLOWED_FACETS == {'5D','nD'}`.
- The 5D axes: `FEDERATION_EDGE_DIMENSIONS == {structural, causal, intentional, temporal, relational}`
  — every edge is placed on these.
- Categories include `policy`, `contract`, `assessment`, `register`, `technical`, `appointment`.

## The planes a policy graph consumes (all installed; consume, never regrow)

| plane | module | what it contributes to the graph |
|---|---|---|
| governance | `loomground_governance` v0.8.3 | nodes `actor/human/gate/master`, cords `authority/pipe/egress`, verdicts, declarations; `schema('patch')` etc. |
| deontic + norm | `loomground_norm` v0.1.0 via `ingest.DeonticIngester` | `DeonticFormula`, operators `O/P/F`, Hohfeldian `incident` — the must/may/must-not modality |
| legal | `loomground_legal` v0.2.1 | `Anchor / ApplicableLaw / Citation / Adjudication` — source hierarchy, precedence, defeasibility |
| factual | `loomground_factual` v0.1.0 | `grammar / lower` — the facts a norm ranges over |
| epistemic | `loomground_epistemic` v0.1.0 | `grammar / extract` — confidence / knowledge status |

The lowering entry points (verified): `ing.GovernanceIngester().ingest(text, ing.Ctx(category="policy",
facet="5D")) -> Subgraph` and `ing.DeonticIngester().ingest(...) -> Subgraph`; sink via
`ing.DimensionedSubgraphSink().upsert(...)`.

## The cascade — local-first, always

```
policy / action text
  │  1. TRY THE ENGINE FIRST
  ├─ RVND present & folder governed → its own ingest + versum + solver
  │     → precise, signed Subgraph, verdicts computed server-side, enforced.        (live)
  │
  └─ 2. FALL BACK (engine absent or can't)
        → DELEGATE to the free plane skills (loomground-ingest, deontic, loomground)  (coarse)
        → the cloud LLM ENRICHES the assembled Subgraph IN-GRAMMAR   (fix bearer/condition/edges)
        → hand the same Subgraph to sign-off / to RVND when it returns.             (degraded)
```

## Delegate to the plane skills — don't reimplement them

The fallback does **not** re-author a lowering. It delegates to the plane's own skill from the free
`loomground` marketplace (Apache-2.0), and assembles the facets:

| facet | delegated skill | it owns |
|---|---|---|
| lowering / ingest | `loomground-ingest` | drive the network-free ingest plane → a dimensioned Subgraph |
| deontic | `deontic` (loomground-deontic) | each norm → a verified `DeonticFormula` (`O/P/F`, incidents) |
| governance | `loomground` (loomground-governance) | the governance `.lg` patch + the express/policy/host litmus |

A commercial `rvnd`-tier skill consuming these free plane skills is the layering working as intended —
never the reverse, and never a reimplementation. The plane skills are the standalone floor; the RVND
skill only cascades (engine-first) and assembles.

Rules that make the cascade honest:
- **Engine first, never the reverse.** The assistant does not answer and check later. If the engine
  can decide it, the engine decides it.
- **The LLM only enriches; it never changes the shape.** Enrichment stays inside the plane's grammar
  (e.g. a corrected `DeonticFormula`), so an engine that ingests it later sees the same object.
- **Degraded is labelled.** A standalone (LLM-only) build is coarse — no solver/versum precision, no
  signing, no enforcement — and the skill says so. It is advisory until the engine ratifies.
- **Fail-closed.** If neither the engine nor the ingest plane can produce a well-formed Subgraph, the
  skill stops and reports a residual — it never fabricates a verdict.

## Worked pattern (verified this session)

Governance lowering (standalone) → a Subgraph with `dimension: nD`, `actor`/`master` nodes, validated
against `schema('patch')`. Deontic lowering (standalone) → `O(bearer : action)` norm nodes with edges
on the 5D axes. LLM enrichment of the coarse deontic node → a valid `DeonticFormula`:

```
if [decision.amount > 10000 EUR]
  then O(automated-decision-system : be reviewed by a compliance officer)
  within [before execution]  in accordance with [EU-AI-Act:Art.14]
```

— one line consuming governance + deontic + legal, RVND-ingestible unchanged.

Every skill's `SKILL.md` references this file for the cascade and the plane list; it does not restate
the module surfaces.

# Governance layer — usage

Policy is the map; agents fold onto it — policy is the source of authority, and every verdict
traces to a governing rule (README → *The model*). This layer ingests a policy,
projects it, shows it as cards, enforces it through gates an officer oversees, and lets you ask
it — all on the signed chain. It **declares, never certifies**, and **applies nothing** until a
human confirms. CTAs open/tighten, never execute or loosen.

The layer exposes **six ops** through `workspace_workflow(op=…, params)` — `governance_chat`,
`governance_map`, `governance_kg`, `security_dashboard`, `officer`, `model_capability` — each a
versioned, read-only projection. A bad param *value* returns a structured `{"error": …}`, never a
traceback.

**Kill switch:** set `RVND_GOVERNANCE_LAYER=off` to disable the whole cluster — the six ops refuse
with a clear error and disappear from the `ops` catalog; everything else (including the ingest
quarantine, which is enforcement, and `policy_ingest`) is unaffected. Default **on**.

## One box — `governance_chat`

```
workspace_workflow(op="governance_chat",
  { folder_context, text, policy_text?, instrument?, intent? })
```

One input, routed by inferred intent (the router *proposes*; `intent` overrides):

| input | routed to | result |
|---|---|---|
| a **policy** | ingest | a draft v0.5 governance twin — nothing applied |
| a **self-description** ("we screen CVs with an LLM") | intake | fills a subject card |
| a **question** | ask | answered from the map |

Returns `{ intent, echo, kind, result }`. App: **Governance chat** in the menu.

## The map — `governance_map`  ·  `governance_map/v1`

```
workspace_workflow(op="governance_map",
  { folder_context, policy_text | provisions, instrument?, view?, question? })
```

Projects rules → roles · steps · risks: a typed rule list + roll-ups + a group-by/filter/
deep-link tree. `view = { group_by, sort, filters, focus }`; axes: `room / role / risk / status /
instrument / demand`. A natural-language `question` is parsed to a `view` and echoed (auditable).
Each rule row carries its CTA + overlay; a Policy Card is a `review_card`, and enforcing a card
runs `card_gate` (allow / hold / deny).

App: **Policy map** — paste Article-shaped text → *Map it* → collapsible groups, gaps first; the
*ask* box narrows by question.

## The graph — `governance_kg`  ·  `governance_kg/v1`

```
workspace_workflow(op="governance_kg",
  { folder_context, policy_text | provisions, instrument?,
    level?, focus?, dimensions?, demand_as?,   # a graph, OR…
    from?, to? })                              # …a reasoning path
```

Projects the **same rules the map builds** into a universal, zoomable graph — nodes
(`instrument / role / room / rule / obligation / gate / artifact`) + 5-dimension edges — at
`level` = `overview | cluster | detail`. Give `from`/`to` (node ids) for a **reasoning path**
instead: the ordered edges (provenance) + the composed dimension — the auditable "why" a table
can't give. `demand_as` = `node` (reify the obligation) or `edge` (collapse to a labelled edge).

## Security dashboard — `security_dashboard`  ·  `security/v1`

```
workspace_workflow(op="security_dashboard", { folder_context, group_by? })
```

A read-only projection over the folder's signed chain's security events (quarantine / card-gate /
erase-guard): admitted / held / rejected, live `holds_pending`, `group_by` = `verdict | rule |
source | kind`. Carries a `limits` field: the ingest gate is a **denylist tripwire, not
containment** — a clean board means "no known-bad pattern matched", not "safe".

## Oversight preview — `officer`

```
workspace_workflow(op="officer",
  { oversees, control_form?, escalation_party?, gate_floor?, grade?, act? })
```

Preview a policy-programmed oversight binding: the officer's control form composes with a gate's
floor **strictest-wins, tighten-only** (it can never loosen a regulated gate), and a reserved
`act` shows *where it escalates* — routed to a human, never auto-decided. A typo'd `control_form`
errors (fail-closed). The officer is passed in; production auto-loading of *registered* officers
awaits the officer store.

## Model readiness — `model_capability`  ·  `model_capability/v1`

```
workspace_workflow(op="model_capability", { task? })
```

For each LLM task (or one `task`): is a capable **local** model registered → `run_local`, or the
honest degrade (`deterministic` / `keyword_only` / `escalate_human` / …). Reads the local registry
only; runs nothing. The same check **enforces** at the one live LLM seam: `policy_ingest`'s
`use_llm=True` consults it before the ambient local-model proposer runs and degrades to
deterministic — reported in the twin's `capability` field, never silent. An explicitly injected
proposer is the caller's choice and is not second-guessed.

## Doctrine (holds across all of the above)

- Deterministic core; the local LLM is **opt-in, a proposer not a judge, fenced**.
- Strictest-wins / default-deny.
- RVND **carries** legal content; it does not author it.

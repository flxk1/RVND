# Console surface — the completeness spine

The coverage contract. "All console info" = the **22 panels declared in
`app/src/panels/pack.json`** (the console fail-closes if the pack is malformed and red-banners any
declared panel that never registers) **plus the read-rendering shell surfaces**. Every field below
was read from the console bundles and the server code — do not add fields not grounded here, and if a
new panel enters `pack.json`, add its row or the audit is incomplete.

Backend is reached as `tool("<facade>", {op:"<op>", params:{folder_context, …}})`. The audit uses
only **pure-read** ops (marked). Facade surface (from the `help` panel): `workspace_{workspace,
folder, ingest, policy, session, ask, matrix, lock, erase, lens, contract, workflow, dispatch,
orchestrate, audit, conformity, grounder, memory, capture, model, mirror, legal}` + standalone
`cross_workspace_read`, `server_info`.

## The core read — `governance_live` (backs `govstrip` + `govlive`)

`tool("workspace_workflow", {op:"governance_live", params:{folder_context}})` — VERIFIED by reading
`governance_live.py` and running it (rvnd 0.6.9.9):

```
{ ok,
  summary: {sessions_open, admitted, run_leases_held, escalations, unauthorised_effects},
  sessions: [{sid, admitted, verdict, grade, escalation, folder_context, expires}],
  leases:   [{run_id, folder, workflow, holder, ttl_s}],
  chain:    [{seq, actor, event, extra, hash, prev_hash}],        # chain[i].prev_hash==chain[i+1].hash
  certificates: [{audit_id, certificate}],
  reconciliation: {status, unauthorised_rate, matched,
                   authorised_not_observed, observed_not_authorised} }
```

Verdict alphabet (strictest-wins, fail-closed): `auto / human / reserved / refused / prohibited /
unfired`. Only `auto` releases on its own.

## The 22 declared panels (pack.json)

| # | id · title | facade · read ops (pure-read) | key fields | audit dimension · state map |
|---|---|---|---|---|
| 1 | **ai** · AI & Capture | `workspace_model`(list, status), `workspace_capture`(read), `workspace_dispatch`(list_pinned, recent) | models[], endpoint reachable; `readiness.tasks{capable,model_id,action,reason}`, `tier_c{available,backend,fail_closed}`; capture `captures[model,scope,summary]`; pins; recent dispatches | #1 AI system + model card + capture · host (model) / idle (capture) |
| 2 | **govlive** · Live governance | `workspace_workflow`(governance_live), `workspace_audit`(tail, verify_chain), `workspace_workflow`(lane_capabilities, approval_list) | see core-read above | spine (#2/#8/#9/#13/#14) · live/idle |
| 3 | **data** · Local data | `workspace_memory`(recent), `workspace_mirror`(list) | memory `{count, served_sealed, results[id, problem.summary]}`; mirror `{count, mirrors[source_path, kind, span_count]}` | #4 local knowledge · blind |
| 4 | **grounder** · Sources & gaps | `workspace_grounder`(coverage, bibliography, swarm.frontier, oversight.feed) | coverage `{works, claims, claims_by_status, works_missing_creators/link/date, untraced_works, claims_without_evidence, verified_without_evidence, support_failures, overlong_quotes, disputed_residuals}`; feed `{flagged, count}` | #4 attribution · blind |
| 5 | **coverage** · Coverage | `workspace_workflow`(governance_graph, coverage_matrix; presets `agent_task`/`kind_risk`/`task_role`) | matrix `{title, col_axis, cols[], rows[], cells[][]{verdict, finding, why, count, use_case_id, agent_id}}` | #17 capability matrix (coverage) · blind |
| 6 | **decision** · Decision | `workspace_dispatch`(decision_pending) | pending `[query, decision_id, assignment_basis, raised_by, option_count, priority, overdue]`; surface `{options[id, grounding_band, label, consequences, conclusion, supporting[text, pinpoint]]}` | #6 reasoning/decision · blind |
| 7 | **legal** · Standing facts | `workspace_legal`(card.list) | `cards[]` (subject-card ids/paths) | legal standing facts (own row) · blind |
| 8 | **obligations** · Obligations | `workspace_contract`(obligations) | `buckets{breached_candidate, escalated, due, due_soon, pending}`, `unresolved_deadlines`, `closed_counts{satisfied, waived, superseded}` | #10 obligations · blind |
| 9 | **egress** · Egress board | `workspace_workflow`(egress_board) | tracks `[name, connector_id, channel, floor(permit/hold/deny), destination_class, mode(enforced/attested), credential.status(armed/no_cable/unplugged)]`; `summary{can_act_outside, tracks, unplugged, no_cable}`; `llm_broker{bound_here, reachable}` | #10 egress · idle |
| 10 | **lock** · Privacy Lock | `workspace_lock`(threshold_get, setup_status) *(NOT audit_query — it self-records)* | `{configured, backend_spec, audit_log_path}`, `threshold` | #5 PII / privacy lock · blind |
| 11 | **erasure** · Erasure | `workspace_erase`(status) *(sweep/request mutate — read status only)* | `manifest.executed`; (sweep preview: `drafts_sealed`, `total_hits`) | #5 erasure / GDPR tombstones · blind |
| 12 | **protections** · Policy | `workspace_policy`(snapshot, juris_packs, party_list) | `{lock_is_active, lock_mode, oversight_is_active, oversight_default_level, ai_training_optout}`; jurisdiction stack; parties | #9 oversight dial + posture · idle |
| 13 | **conformity** · Conformity | `workspace_conformity`(evidence_pack, oversight_attestation, trigger_map, drift_report, risk_register, threat_model; `regime` ∈ ""/"eu-ai-act") | evidence `{chain.ok, records, counts_by_kind, basis}`; attestation `{attested, determinations, conditional_releases, bypassed_events, statement}`; drift `{baselines, open_findings}`; risk `{posture, oversight, observed_actions}`; threat `{categories}` | #12 compliance/conformity · blind |
| 14 | **audit** · Audit trail | `workspace_audit`(verify_chain, discipline, shadow_scan, overrides, override_recurrence, calibration), `workspace_model`(attest_status) | verify_chain `{ok, total_events, broken_links, signature_failures, unsigned_events, malformed_lines}`; discipline `{clean, failures, warnings, scanned}`; attest `models[model_id, baselines, latest_run{verdict(PASS/EXPLAINED_DRIFT/UNLOGGED_LEARNING), diverged, unobserved, probe_count, hash_state}]` | #14 proof / signed record · blind |
| 15 | **approvals** · Sign-offs | `workspace_contract`(list_approvals), `workspace_workflow`(approval_list) | contract `{signer_decisions, overall_state, signers, action_summary, contract_id, approval_id, deadline}`; reservation `{needed/quorum, approvers, competences, deadline, on_elapse, state}` | #9 human oversight · blind |
| 16 | **roles** · Roles & competence | `workspace_policy`(party_list) | parties `[party_id, party_kind(human/agent), status(active/suspended/killed), role, competences[], channels[]]` | #16 parties · blind |
| 17 | **workflow** · Run board | `workspace_workflow`(list, active, queue, inspect_stuck, transport_audit) | defs `[name, step_count, description]`; queue `[run_id, workflow_name, state]`; `inspect_stuck.stuck`; transport_audit `{holds, actor_present, total, missing_actor}` | #2/#3 runs & fleet · idle |
| 18 | **contract** · Contract execution | `workspace_contract`(list_reviews, state) | reviews `[contract_id, traffic_light(green/red/amber/grey), decision]`; `contracts`, `obligations`, `decision_queue` | #7 contract · blind |
| 19 | **federation** · Connected tools | `workspace_workflow`(connector_list) *(federated_decision/group_floor mutate — list only)* | connectors `[connector_id, group, floor, role, channel, tags, use_cases]`; decision `{decision, disagreement, sources[connector_id, verdict, tool_verdict, floor, group_floor, input_digest], revoked_sources[]}` | **federation (own row)** · idle |
| 20 | **lens** · Spend & limits | `workspace_lens`(log, precedent_list) | log `{cap, spent, held, over_budget, count}`; precedents `[{id}]` | #11 cost/budget + precedents · host (spend) / idle |
| 21 | **bringin** · Bring-in | *(all ops mutate — declare available inputs only)* `workspace_ingest`(path/url/skill) | inputs available; results `{idempotent_noop, count, state, skill_id}` | **bring-in (own row)** · host (available) / blind |
| 22 | **map** · Policy map | `workspace_workflow`(governance_map) | rules projected onto `room/role/risk/demand/status`, gaps-first (renderer: `renderMapContract`, shared in index.html) | #7 rules map · blind |

Menu note: 21 of 22 are menu-anchored; `govlive` opens from the always-on strip. `govlive`,
`grounder`, `conformity`, `audit`, `obligations`, `coverage`, `egress`, `map`, `legal` are `access:read`;
the rest are `write` panels (the audit uses only their read ops).

## Read-rendering shell surfaces (beyond the 22 panels)

Chrome, not manifest panels, but they render governance **state** the audit must also report:

| shell | facade · read op | renders |
|---|---|---|
| `govstrip` | `workspace_workflow`(governance_live) | the always-on tiles: sessions / admitted / leases / needs-you; HOTL "all green" vs flaring `sid · verdict` |
| `ticker` | `workspace_audit`(tail, limit) | signed-record stream — per-verdict tally + `event, actor, ⚠unsigned, ts, audit_id` |
| `controller` | `workspace_policy`(snapshot, party_list), `workspace_lock`(threshold_get) | lamps: oversight level · grounding floor · active parties N/total; ALL-STOP control |
| `register` | `workspace_workflow`(governance_register; `scope:"all"`) | agents · tasks · verdict · reserved; per-folder rollup |
| `matrix_modal` | `workspace_matrix`(show) | **the OTHER matrix** — grade × oversight traffic-light (`go/ask/block`); inherited vs local override |
| `matrix_view` | `coverage_matrix` (canvas) | `kind_risk` / `task_role` / `task_agent` lenses |
| `session_env` | `workspace_session`(verify_bytes) | signed `.rvnd` bundle: `name, signed-by, origin_role, chains intact`, sealed status |
| `transport` | `workspace_policy`(party_list) | run-state Run/Held; N of M agents active |
| `help` | `<facade>`(help) | the full op catalogue — #3 "tools available / which ran" |

**Two matrices, not one** (an easy conflation): `coverage_matrix` (panel
`coverage` / `matrix_view`) is agent×task / kind×risk / task×role; `workspace_matrix` (shell
`matrix_modal`) is **grade × oversight**. Give each its own row.

## Deep-verified surface #4 — Data & knowledge (Versum)

RVND's knowledge plane; the per-answer receipt is a **span-grounded claim**. VERIFIED from
`adapters/versum/knowledge.py` and the consumed `versum` package.

- **Claim row — `CLAIM_COLUMNS`:** `canonical_urn, library, item_id, source_urn, unit_id, unit_type,
  span_start, span_end, marker, text, polarity, type, predicate, dimension, modality, quantification,
  principle, judicial_canon, inference_rule, confidence, verification`. Grounded-vs-generated = whether
  a statement resolves to a `source_urn` + `span_start/end` (the receipt).
- **Concept row — `CONCEPT_COLUMNS`:** `concept_id, label, domain, definition, catalogue_version,
  created_by, status, superseded_by, aliases`. **Edge:** `src_id, dst_id, predicate, dimension`.
- **Data-used fingerprint:** `VersumKnowledgeStore.snapshot().digest` — sha256 over the `.versum`
  files. **Per-focus:** `subgraph()` → `{schema:"rvnd.versum.subgraph/v1", focus, nodes, edges,
  snapshot}`.
- **Grounder ledger (`GroundedClaim`):** `id, text, work_ids, locator, quote, confidence, status,
  method, agent, verified_by, evidence_at_promotion, first_seen, last_seen`. `grounder coverage`
  gives attribution completeness + un-evidenced claims.

## Deep-verified surface #6 — Reasoning (Solver)

VERIFIED by running `loomground_solver.contract`.

- **`check_case(case, *, oversight_level, oversight_active, stake, personal, held_pinpoints)`** →
  `ContractReport.to_dict()` = `{ok, must_escalate, findings:[{level, code, message, field, pair_id}]}`.
- **`level` ∈ `pass | violation | escalate`.** Codes RC-1..RC-8: evidence sound · steps warranted ·
  resolution type · judgment floor · actions anchored · norms complete · profile consistent.
- **`LEVELS` (oversight ladder):** `autonomous, notify, review, approve, supervised, manual` — each with
  an `INFORMATION_FORMS` entry (record → notice → preview → decision-surface → transcript → schema-only).
- **`PROFILES` (reasoning schemas):** `legal-de` (Gutachtenstil, default), `legal-irac`, `frma`,
  `generic` (defeasible Toulmin). RC-7 reports which one a chain conformed to.
- **`governance_kg`** (`tool("workspace_workflow", {op:"governance_kg", …})`): projection
  `{version, level, focus, dimensions, kinds, nodes, edges}`; reasoning path (needs `from`+`to`)
  `{version, from, to, hops, edges, dimension_chain, overall_dimension}` or `{hops:0, reason:"no path"}`.
  Path-finding is delegated to the solver (`compose_paths`), not RVND's own traversal.

## What fills WITHOUT the engine (host-declarable only)

`ai` (model + card), `help`/`#3` (tools *available*), coarse *sources available* for `data`/`grounder`,
declared token usage for `lens`. **Everything else renders `blind` or `idle`** — no reasoning checked,
no rules applied, no red lights, PII unmonitored, reconciliation uncomputable, no proof. The absence is
the signal.

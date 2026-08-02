# Changelog

Notable changes to RVND. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
dates are ISO.

## [Unreleased]

## [0.6.8.7] - 2026-08-02

### Documentation

- Scoped enforcement and signed-audit claims to calls routed through RVND,
  documented the optional OS-level containment boundary, and replaced the
  stale identity-proxy limitation with the implemented principal, lane and
  policy-fingerprint admission contract.
- Documented all five consumed Loomground packages and clarified that RVND,
  rather than the ingest library, owns host-side confirmation and enforcement.

## [0.6.8.6] - 2026-08-02

### Distribution

- Added native Claude and Codex manifests for the bundled `rvnd-governance`
  plugin while retaining the generic package and MCP descriptor.
- Made RVND itself the Claude marketplace so the bundled plugin resolves from
  its valid repository-local path without duplicating its AGPL source.
- Kept compatibility with external Claude catalogs through a repository-root
  plugin manifest that delegates to the same nested skills and MCP descriptor.
- Added a regression gate that keeps every host manifest and MCP launch
  contract aligned with the canonical plugin package.

## [0.6.8.5] - 2026-08-02

### Security

- Made Privacy Lock mirror generation fail closed when scanning is unavailable
  or produces no redacted output.
- Hardened catalogue HMAC key handling: verification is read-only, invalid keys
  never rotate implicitly, POSIX creation is atomic and durable, and enforce
  mode rejects unsigned, stale, incomplete, or unverifiable catalogues.
- Removed raw cloud API-key persistence while retaining one-cycle, value-free
  compatibility warnings; cloud credentials resolve through per-track
  references at use time.

### Reliability

- Refused workspace migration before destructive movement when its recovery
  marker cannot be persisted.
- Prevented stopped workers from leasing another run.
- Made forgotten-subject and erasure state owner-only, atomic, durable,
  corruption-sensitive, and stable across retries and historical salts.

### Distribution

- Completed REUSE metadata coverage for the release tree.
- Corrected dependency-root validation and publication evidence checks.
- Replaced dated internal readiness narrative with an evergreen, evidence-led
  release checklist and neutral public provenance language.

## [0.6.8.4] - 2026-07-26

### Changed — release integration

- Enforced signed governance sessions and active-party admission on governed runs.
- Wired the five-unit console to the live RVND backend and capability flow.
- Aligned the bundled governance plugin with AGPL-3.0-only, Governance 0.8.2,
  immutable dependency roots, and the installed RVND MCP server.
- Hardened container inputs, identity-proxy boundaries, privacy checks, and
  console dependency security.

## [0.6.8.3] - 2026-07-25

### Added — governed distribution boundaries

- Published policy packs enter through a fail-closed RVND import interface:
  classifier and adapter action kinds must be declared and host-known, the
  active policy fingerprint must match the child's persisted governance lane,
  and RVND-supplied child-safety, developmental, privacy, and jurisdictional
  review attestations remain mandatory.
- Optional host-wide Privacy Lock deployment now has explicit, reversible
  Linux, macOS, and Windows firewall operations. No host firewall is changed
  by default.

## [0.6.8.2] - 2026-07-25

### Changed — licence

- Rvnd is now free and open-source software under AGPL-3.0-only.
  Every SPDX header, both build configs and the licence documents changed
  over, and contributions switch from the retired CLA to DCO sign-off. Copies obtained
  under the earlier Rvnd Research and Community Licence retain the rights
  granted with them.

### Changed — module paths and packaging

- The `workspaces` package is organised in domain subpackages
  (`workspaces.decisions.*`, `.contracts.*`, `.corpus.*`,
  `.attestation.*`, `.capability.*`, `.cli.*`); the compatibility shims at
  the old flat module paths shipped for one release and are deleted.
- One version source: both build configs resolve `[project] version` from
  `workspaces/_version.py`, and the version-drift gate retires.
- The grounder gold corpus ships with the loomground-governance package and is
  consumed through the asset facade; the copies under `server/eval` retire.

### Changed — privacy-lock boundary

- The lock's import boundary is gated (`scripts/lock_boundary_check.py`):
  inbound use goes through the package's declared API, outbound needs are
  injected hooks filled by `workspaces/lock_wiring.py`, and every crossing
  lives in a committed baseline that only shrinks. An unwired lock keeps its
  fail-safe behaviour everywhere (full-protection policy snapshot, skipped
  captures, MCP transport fallback).

### Changed — plugin distribution

- The `rvnd-governance` plugin content lives in this repository
  (`plugin/rvnd-governance`), beside the server surface it versions with;
  distribution moved to the `rvnd-plugins` marketplace repository, which
  references the plugin as a git-subdir source.

### Changed — repository layout

- Documentation now separates durable design from machine-checked release
  evidence and security findings.
- The gate scripts `verify_surface.py` and `verify_completeness.py` moved to `scripts/`;
  a root `Makefile` is the single entry point for every local gate (`make gates`,
  `make completeness`, `make test`).
- The app render gates moved from a flat directory into `app/shell/` (the instrument),
  `app/panels/` (governance content), `app/harness/` (shared bridge), and `app/tests/`
  (cross-cutting walks) — the shell/panels line is the presentation-plane boundary.

### Added — Loomground v0.7: the principal chain

- **RVND reproduces Loomground v0.7 — all 44 conformance vectors.** This is one half of the
  spec's two-implementation interoperability criterion (Loomground SPEC §9); the second
  implementation is tracked separately. The engine implements the v0.7 delegation semantics:
  a `human` may root a principal chain (anchoring answerability while conferring no
  authority), the on-behalf-of relation must name a declared actor or human, carry at most
  one delegator per actor, and be acyclic — each violation ill-formed at apply (fail-closed).
  The binding now projects into the observation as `on_behalf_of` on the delegate's node, and
  a partyless delegate projects the nearest declared party along the chain. The empty-set
  corner of no-amplification is pinned strict: a delegator holding no grant over a kind at a
  gate has the empty risk set there, so a delegate is never granted where its actor-delegator
  is not. The conformance gate now also checks the ordered log trace — one entry per activated
  gate, in evaluation order, carrying the gate's effective verdict (SPEC §7.4) — via the new
  `evaluate_log`. The bundled vocabulary is refreshed to the v0.7 commit, and
  `test_loomground_principal_chain.py` mirrors the new vectors self-contained.

### Added — starter templates (S14, server side)

- **Templates are recipes, not fixtures.** `session_templates.py` ships two declarative starter
  recipes ("Govern a kid's AI", "Enterprise baseline") materialized in-process through the
  governed write paths (party/connector/use-case registration; drafts via the draft store) and
  signed with the local key. A pre-built `.rvnd` fixture could never work here: it would carry
  the packager's key, and a foreign-key session is view-only (decision B) — instantiation, not
  distribution, is the only path to a continuable fresh environment. Materialization refuses a
  destination already in use (before touching anything) and self-verifies the built bundle
  before handing it over. `workspace_session` gains `template_list` / `template_new`
  (materialize + register beside/replace, or `none` for bundle-only); UI wiring pending.

### Added — ONNX GenAI local backend for Tier C

- **`onnx_genai` backend implemented.** The Tier C semantic PII classifier can now run an
  ONNX GenAI model directory (`genai_config.json` + weights) in-process via onnxruntime-genai,
  the static-binary path that packages into a PyInstaller bundle without llama-cpp-python.
  Selected with `AGENT_TOOL_LOCK_LLM_BACKEND=onnx_genai:<model_dir>`. Shares the llama_cpp
  prompt template and response parser; decodes greedily for a deterministic verdict; caches the
  loaded model per directory per process. Fails closed on missing deps, missing model directory,
  or inference error — an unavailable validator flags the text rather than passing it unscanned.
- **Reproducible local-model path.** Rvnd stays model-neutral — it bundles no weights and
  endorses no vendor. As a licence-clean worked example, `scripts/fetch_onnx_model.sh`
  downloads Microsoft's prebuilt Phi-3.5-mini-instruct ONNX build (MIT) as a ready ONNX GenAI
  directory via the Hugging Face CLI (idempotent, no model builder or torch; `MODEL_ID` /
  `SUBDIR` / `OUT` overridable for any other model). `docs/concepts/local-models.md` documents backend
  selection, the worked example, a lighter GGUF alternative, hosted-model notes, and how to
  verify. `models/` now tracks a README (weights stay gitignored). The one model the repo
  documents is MIT, so nothing shipped depends on a non-commercial licence.

### Added — per-track egress (identity & access on the track)

- **Access binding.** An egress connector can carry a `credential_ref` — a reference
  (`env:` / `keydir:` / `oidc:` / `spiffe:`), never the secret; resolved fail-closed at call
  time (`lock/credential_resolver`). The **Egress board** shows every track that can cross the
  wall, with its floor lamp and cable state (armed / no cable / unplugged).
- **LLM-egress broker.** The egress proxy, bound to a workspace folder, brokers the credential
  per track: each proxied call declares its egress track (`X-Lock-Track`), the track is verified
  against the signed chain (exists, egress role, floor, resolvable reference — refuse otherwise),
  client credential headers are stripped and the track's secret is injected for the chosen
  upstream only. A `hold` floor puts a person in front of every forward. Audit and refusals
  carry the reference and track id, never the secret (`lock/track_broker`).
- **Enforcement attestation.** The board says `enforced` only on evidence:
  `lock/broker_probe` asks the running proxy whether it is broker-bound to this folder
  (unreachable / unbound / bound-elsewhere all read as not enforced), and the board carries
  the answer as a board-level `llm_broker` fact for the LLM destination class.
- **Track channel strip.** Selecting a lane or channel in the Inspector shows that track's
  governance, assembled read-only (`track_strip`): status, the L0–L4 oversight ladder with
  law-basis locks (tighten-only), competences, the channel join (dangling bindings shown,
  not hidden), use cases and reservations, the live m-of-n sign-off state ("signed 1 of 2"),
  a per-track verdict meter, and — on egress tracks only — the cable's arm state.

### Added — governance authoring & navigation layer

- **Ingest / interpret.** Policy ingestion routes by document genre: a court decision is
  quarantined — its readings inform interpretation and never compile to a gate (`genre_router`,
  `judgment_reading`) — and instrument text is triaged into duties by role and risk
  (`duty_identification`).
- **Map contract (`governance_map/v1`).** `governance_map` op — a versioned, typed rule list
  (rules → roles · steps · risks) with roll-ups and a group-by/filter/deep-link tree. A Policy Card
  **is** a `review_card`; enforcing a card **is** a gate (`card_gate`, allow/hold/deny). Demand →
  CTA + tighten-only overlay (`demand_cta`); neutral use-case intake folded onto `subject_card`.
- **Oversight.** Policy-programmed `officer`, wired into `action_gate` as a monotone
  tighten-only step.
- **Security.** Ingest quarantine (`ingest_quarantine`, wired into `inbox_watcher`) blocks
  known-bad file shapes and injection patterns and states its detection limits;
  `security_dashboard` reports those decisions with a machine-readable `limits` disclosure;
  per-record `seal.encrypt_record` (at-rest hardening).
- **Navigate.** Single-input chat (`governance_chat` op → ingest / intake / ask);
  NL-question → View (`governance_ask`); universal zoomable knowledge graph with reasoning paths
  (`governance_kg` op, `governance_kg/v1`) over the same rules the map builds. Map / chat / ask
  panels in the app.
- **Model capability.** `model_capability` matches a task to a capable local model or returns a
  bounded fallback; readable as the `model_capability` op and enforced at the LLM seam —
  `policy_ingest use_llm=True` consults it before the local-model proposer runs and falls back to
  the deterministic path (reported in the twin's `capability` field).
- **Ops surface.** Six read-only ops — `governance_chat` / `governance_map` / `governance_kg` /
  `security_dashboard` / `officer` / `model_capability` — behind one validated boundary
  (structured errors on bad param values) and one **feature flag**: `RVND_GOVERNANCE_LAYER=off`
  disables the cluster (refuses + hides from the catalog) without touching enforcement.
- **Contract pin.** The map panel is version- and field-set-pinned to `governance_map/v1`
  (cross-language), so a contract shape change fails a test rather than drifting silently.

### Fixed

- **Test isolation.** A module-level `os.environ` write ran at pytest *collection* and polluted the
  global env, breaking `governance_graph_v05` order-dependently — isolated via per-test
  `monkeypatch`.
- `loomground_assets.vocabulary` — copy-on-read (defensive hardening against shared-mutable reuse).


See [docs/concepts/governance-layer.md](docs/concepts/governance-layer.md) for usage.

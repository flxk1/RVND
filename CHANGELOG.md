# Changelog

Notable changes to RVND. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
dates are ISO.

## [Unreleased]

## [0.6.9.7] - 2026-08-12

### Added

- **The front door hands the agent the governance language (C3).** An agent
  connecting over MCP now receives the governance language at the handshake
  instead of having to discover it: the `FastMCP` server `instructions` present
  it up front (you are IDENTIFIED and GOVERNED; every tool call is planned +
  gated GO/CONDITIONAL/NO-GO; refusal is valid), and it is exposed as the
  `governance://llms.txt` MCP resource. The console also serves it at
  `GET /llms.txt` (`text/markdown`). The language is **consumed from
  loomground-governance** via `artifact_path("llms.txt")` — byte-for-byte, never
  copied, so it cannot drift from the canonical source. `connect-agent-hub`
  points at it. Backed by `tool_call_plan`'s existing fail-closed contract: no
  agent reaches a tool call without first being handed the language.

## [0.6.9.6] - 2026-08-12

### Changed

- **`bootstrap.sh` is now a single branded guided install.** It opens with an
  `RVND` banner, and on a real terminal (works under `curl … | sh` via
  `/dev/tty`) flows straight from install into the guided first-run wizard
  (`workspaces init` — folder, local model, skills, oversight), then prints the
  command overview (`workspaces guide`) and offers to open the local console.
  A non-interactive run (no usable tty) prints the manual steps instead, exactly
  as before. Reuses the already-ported wizard — no new setup logic.

### Fixed

- **`bootstrap.sh` now asks where to install instead of silently defaulting to
  `~/rvnd`.** When neither `$RVND_DIR` nor a path argument is given, it prompts
  for the install directory (default `~/rvnd`) by reading the controlling
  terminal `/dev/tty` — which works even under `curl … | sh`, where stdin is the
  script, not the keyboard. The prompt is fully guarded: a present-but-dead
  `/dev/tty` (some containers/CI) can't abort the install under `set -e` and
  falls through to the default with no stray output; a truly non-interactive run
  never prompts. `$RVND_DIR` and the positional argument still bypass the prompt.

### Fixed

- **The skill-pin picker now reads host-INSTALLED plugins instead of a static
  catalogue that never shipped.** `workspaces pin --interactive` and the `init`
  wizard's §6 read a companion catalogue (`plugin/references/skill-companions.json`)
  that is generated from nothing, so the picker was dark for everyone. Skills are
  authored per plugin repo and installed via the marketplaces (`claude`/`codex
  plugin install`); the picker now enumerates what the host has actually
  installed — reading `~/.claude/plugins/installed_plugins.json` (+ Codex) and
  scanning each install's `skills/` dir — and offers canonical `<plugin>:<skill>`
  ids. `WORKSPACE_HOST_PLUGIN_DIRS` overrides the search roots. Any static
  catalogue that does ship is still unioned in (back-compat). `load_companion_catalogue`
  and companion-suggestion are untouched.

## [0.6.9.4] - 2026-08-11

### Fixed

- **`workspaces init` is now the full first-run wizard it was meant to be —
  local-model choice and skills selection are wired in, not just described.**
  Two components that already shipped in the tree were never invoked from the
  wizard: the guided model wizard (`workspaces.lock.run_wizard` — the
  bundled/download/pick-existing/skip flow) and the companion/skills multi-select
  (`workspaces pin --interactive`). `init` §5 (Local model) now offers to launch
  the real model wizard, and a new §6 (Skills) reuses the same picker to pin
  starter skills to the default workspace — rather than printing commands and
  omitting skills entirely. `--yes` / `--dry-run` keep the non-interactive
  guidance (they cannot prompt). No new code paths for either capability: the
  wizard now *consumes* the built ones, consistent with the no-parallel-structures
  gate. Sections renumbered (oversight → §7, connect → §8).
- **`init` §7 oversight copy corrected.** It claimed the console's first-run
  wizard "picks" the oversight level; that step only tightens the autonomy
  matrix. §7 now names the real setter (`workspaces oversight <level>`) and
  describes the console wizard accurately.

### Removed

- Retired two genuinely dead modules surfaced by a build-vs-wired audit
  (imported by no live code, no tests): `workspaces/navigate_folder.py` (a
  superseded substrate op) and `lock/backends/ollama_http.py` (the ollama
  backend the factory has rejected since 0.6.5). Unrelated unwired modules that
  are legitimate dev/eval tools (e.g. `grounder_eval`) or front-ends of a
  partly-live subsystem (`issue_token` → the live `case_index` CBR memory) were
  deliberately KEPT, not deleted.

## [0.6.9.3] - 2026-08-11

### Fixed

- **Hardened the installer so a stale pip wheel cache can no longer produce a
  wrong install.** `server/install.sh` now (1) drops all `loomground_*` plane
  wheels from pip's cache before installing, forcing every git pin to rebuild
  from its exact commit — pip caches wheels by version, not commit, so a stale
  wheel of a shared version could otherwise shadow a pin; and (2) verifies each
  consumed plane's real surface (`loomground_solver.ESCALATE` / `RelationAlgebra`
  / `Dimension`, ingest/deontic/versum/legal symbols) and **fails loudly with
  the one-line fix** (`pip cache purge && ./server/install.sh`) instead of a
  cryptic downstream `ImportError` — or an engine that imports but is hollow.
  Defence-in-depth behind the unique-version pins from 0.6.9.1/0.6.9.2.

## [0.6.9.2] - 2026-08-11

### Dependencies

- Retired the last branch-pin: `loomground-deontic` re-pinned from an immutable
  commit to released tag `deontic-v0.1.4` (same content, unique version, kept
  in the 0.1.x line so `loomground-solver`'s `loomground-deontic>=0.1,<0.2`
  constraint holds). **All nine plane dependencies are now released version
  tags — zero branch-pins** — closing the pip wheel-cache version-collision
  class for every plane (see 0.6.9.1).

## [0.6.9.1] - 2026-08-11

### Fixed

- **Clean installs of 0.6.9.0 could fail with `ImportError: cannot import name
  'ESCALATE' from 'loomground_solver'`.** solver and ingest were pinned to
  immutable commits whose package version (solver `0.2.0`, ingest `0.1.1`) was
  shared with earlier commits. pip caches wheels by *version*, not by commit, so
  a machine holding a stale wheel of that version installed the wrong build —
  one predating the symbols RVND imports. Re-pinned both to uniquely-versioned
  release tags so the pin resolves unambiguously.

### Dependencies

- Re-pinned `loomground-solver` to released tag `solver-v0.4.0` and
  `loomground-ingest` to released tag `ingest-v0.2.0` (both were branch-pins to
  immutable commits). Only `loomground-deontic` remains an immutable-commit pin.

## [0.6.9.0] - 2026-08-10

### Added

- **Live Governance ("govlive") — a read-only operational dashboard over the
  signed governance log.** The honest-subset v2 `governance_live` board op
  (sessions derived by replay, per-agent lane verdicts, run-lease
  serialization, the one signed chain; fields with no honest source are omitted,
  never faked), plus its surfaces: the always-on integral governance strip with
  the HOTL alarm (I1), the live read-only step-stream (I2), the egress-governed
  board API `GET /govlive/board` (I3), the step inspector drill-down (I4), and
  the governed interaction route `POST /govlive/act` (I5) — acting on a reserved
  step routes through the same approval facade the CLI uses, never a bypass.
- **Governance-enforcement proofs.** Reserved-to-agent ownership enforced at
  four independent layers (T-own); session-scoped Versum plus solver
  consistency, fail-closed (T-cons).

### Changed

- `POST /govlive/act` reports the honest outcome (`counted` / `state`) rather
  than a bare `ok`, so a monitor never reads success when a vote did not count.
- Runtime-claim and guarantee wording scoped to what each mechanism actually
  enforces ("signed builds").

### Dependencies

- Re-pinned `loomground-versum` to the released `loomground-versum-v0.13.0`
  tag (previously a branch-pin). solver, ingest, and deontic remain pinned to
  immutable commits pending their own releases.

## [0.6.8.9] - 2026-08-02

### Documentation

- Made the public quick start runnable from a clean directory by cloning the
  correctly cased `RVND` folder first and using the tested installer.
- Removed a display-only shell comment that interactive zsh could interpret as
  a command when the full example was pasted.

## [0.6.8.8] - 2026-08-02

### Dependencies

- Refreshed all five immutable Loomground dependency pins to their approved
  public repository heads, including Solver runtime 0.2.0 and Deontic 0.1.3.

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

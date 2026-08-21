# rvnd

The RVND governance layer as skills: how an AI host drives the **RVND governance MCP server**
safely. RVND (reeve + nD) is a local-first governance system that sits between an agent and what it
acts on and decides what may happen. These skills do not decide anything — **the server decides and
the host renders** — they teach the host the discipline that keeps a request from ever being shown
as a grant.

## The cycle

Every consequential interaction runs one loop: **discover → query → propose → validate → preview →
confirm → apply → display**. Reads grant nothing; writes are fail-closed. The one rule the whole
plugin exists to enforce: never present requested state as granted.

`humanConfirmation` in `package.json` is declarative metadata enforced by the host. The plugin
does not treat that flag as server-side authorization; missing or unknown capabilities must be
refused by the host.

The shared contract lives in `references/protocol.md` (the eight steps, identity resolution,
tightening vs loosening, Sign routing) and `references/catalogue.md` (the ten canonical verbs
mapped onto the server's real operations, and how to discover the live tool surface instead of
hardcoding it).

## Grounding

A skill is grounded in Loomground only when it produces or consumes typed governance objects — not
when it merely prompts a model to "apply governance". The spine of that is the typed **proposal
envelope** (`schemas/proposal.schema.json`): the skill assembles intent, scope, and runtime
bindings and asks RVND to generate and validate the Loomground constructs; it never writes `.lg`.
Every governance meaning is either a real construct or a visible **residual** — nothing in between
is invented. `references/grounding.md` sets out the six grounding links (source, object, language,
runtime, evidence, version) and the residual ledger; `references/vocabulary.md` pins the real
`loomground-governance` **0.8.2** constructs the skills are allowed to handle; `references/corpus.md`
is the acceptance corpus, headed by the equivalence test (chat / CLI / sheet / `.lg` → one canonical
observation). Each skill carries a `manifest.yaml` declaring the constructs it may read and propose.

## The eight skills

Named for the user's goal, not the pipeline step. Each operates the one shared dimensioned
Subgraph and cascades local-first (engine → LLM fallback); see `references/ingest-cascade.md`.
The full catalog — these 8 plus the 17 free Loomground plane skills — and the install paths are in
[`SKILLS.md`](SKILLS.md).

- **govern-an-action** — "is this allowed, and put it on the record": dry-run the verdict, or run the
  full cycle query → validate → confirm → apply → display. (folds in the read-only reason step)
- **onboard-a-policy** — "bring this regulation/contract into governance": lower it into the shared
  5D+nD graph via the ingest plane (governance + deontic + legal), hand it to sign-off. (folds in
  extract + compile)
- **sign-off** — "what's waiting on me": the human decision, approve / hold / deny, with the named
  approver and the rationale a loosening needs.
- **resolve-a-conflict** — "two rules clash": validate a resolving delta (consuming legal precedence)
  or surface a residual for a person; never picks a host-side winner.
- **audit-the-ai** — "what may this AI do, and where's the proof": the pure-read board, everything the
  console reports, in two versions (without-RVND transparency vs with-RVND governance), reconciliation first.
- **verify-a-receipt** — "prove this one decision happened, unaltered": checked against the Ed25519
  chain; announces its audit-of-audit append before running; attributed, not asserted.
- **revoke-or-erase** — "pull that authority / erase this subject": tightening is immediate, restoring
  authority needs approval; erasure is a signed tombstone.
- **build-a-surface** — "assemble a governed app screen": compose and lint the five cards so a built
  surface cannot show a request as a grant. The one skill that drives no server.

## Structure

- `mcp/` — how to connect the RVND governance server (`rvnd.mcp.json`). RVND runs locally; this
  plugin names it, it does not ship it.
- `apps/` — the five MCP App card specs: context, proposal, patch, decision, receipt.
- `schemas/` — the surface-card and composition schemas the linter enforces.
- `references/` — the shared protocol and catalogue, and the `offline-floor.md` guide.
- `skills/` — eight composable skills, each with a lean `SKILL.md`, interface metadata, an operation
  manifest, and focused references.
- `bin/` — the zero-install **offline floor**: stdlib-only tools (`rvnd-probe`, `rvnd-lint`,
  `rvnd-preview`, `rvnd-verify`) that work before the engine is installed and route to the governed
  surface once it is. Advisory only — never grant, sign, or enforce. See `references/offline-floor.md`.
- `scripts/` — shared helper (`rvnd_floor_lib.py`) for the `bin/` tools.

## Relationship to the kernel

This is **not** a set of kernel wrappers. All eight skills drive the RVND server, which is built on the
Loomground language and the `loomground-solver` kernel. RVND supplies the policy, corpus, custody
and audit adapters; the Solver has no RVND dependency. (The previous 0.1.x version of this plugin
wrapped the kernel directly — a mis-layering — and is superseded by this MCP-driver design.)

## Licence

This plugin and RVND are free and open-source software under
the GNU Affero General Public License v3.0 only. Vendored Loomground Governance
schemas retain their upstream Apache-2.0 provenance and identifiers.

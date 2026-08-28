<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Loomground & RVND — skill catalog and install

Two tiers, two marketplaces, and **they are not equally reachable.** Read the
access column before picking an install path.

| tier | marketplace | access | licence | what it is | count |
|---|---|---|---|---|---|
| **RVND server** | `rvnd` (`flxk1/RVND`) | **public** — anyone can `/plugin marketplace add flxk1/RVND` | AGPL-3.0 (commercial terms for the engine) | user-action skills that operate the shared graph on a signed engine | 1 plugin · **8 skills** |
| **Loomground planes** | `loomground` (`flxk1/loomground-plugins`) | **author-internal** — this marketplace repo is private; a stranger cannot add it | Apache-2.0 | standalone, single-facet authoring skills | 5 plugins · **17 skills** |

**The public path is the pip engine plus the `rvnd@rvnd` plugin.** Anyone can
`pip install` RVND (see [docs/getting-started.md](../../docs/getting-started.md))
and install `rvnd@rvnd` from the public `flxk1/RVND` marketplace — that alone
runs every RVND skill, falling back to *transparency* mode for the pieces
that would otherwise delegate to a Loomground plane skill (see "Skills vs the
engine" below). The `loomground` marketplace's Apache-2.0 licence describes
what you may do with those skills *if* you have access to them; it does not
describe who can currently reach the repo that ships them. If you don't have
access, use RVND standalone — you lose nothing on the enforcement or audit
path, only the plane skills' own standalone authoring UI.

---

## Install — what needs what

**The dependency rule:**

- **Install RVND alone (public, works for anyone).** `rvnd@rvnd` from the public `flxk1/RVND`
  marketplace is everything most people need — it runs standalone, in transparency mode, with no
  dependency on the `loomground` marketplace at all.
- **Install RVND *with* the plane skills it would otherwise delegate to → needs `loomground` access.**
  RVND's skills *delegate* to three Loomground plane skills when present: `loomground-governance`,
  `loomground-deontic`, `loomground-ingest`. Adding those upgrades RVND from transparency to governed
  mode for the pieces that use them — but the `loomground` marketplace is author-internal, so this path
  is only open to someone with access to it. `loomground-solver` and `loomground-versum` are independent
  tools RVND does **not** require either way.
- **Install a single Loomground skill → standalone, but needs `loomground` access.** It needs neither
  RVND nor any other Loomground skill — just access to the marketplace that ships it.

**Skills vs the engine — transparency vs governed.** The plugins above install the *skills*; the RVND
*engine* (`server/install.sh`) is separate. The RVND skills follow a local-first cascade, so they run
either way — you do **not** install a different skill for each:

- **Skills only (no engine):** each RVND skill falls back to what the host can declare alone.
  `audit-the-ai`, for instance, renders its board in *transparency* mode (what the AI is and could
  reach), every engine-backed row left empty — no signed board, no enforcement.
- **Skills + engine (`server/install.sh` → `scripts/connect-agent-hub.sh`):** the same skills fill
  from the live signed engine — *governed* mode (what policy permitted, where an effect overstepped,
  and proof). The engine adds the MCP tools; the hook adds enforcement.

Same skills, two runtime outcomes — the cascade picks the mode.

### A · RVND only — public, the path everyone can take

```
/plugin marketplace add flxk1/RVND
/plugin install rvnd@rvnd
```

Runs standalone: transparency mode for the pieces that would otherwise
delegate to a `loomground` plane skill, governed mode for everything the pip
engine itself covers once you also run `server/install.sh` (see above). No
access to the `loomground` marketplace is required for this path.

### B · RVND + the plane skills it delegates to — needs `loomground` access

```
/plugin marketplace add flxk1/loomground-plugins
/plugin marketplace add flxk1/RVND
/plugin install rvnd@rvnd
/plugin install loomground-governance@loomground
/plugin install loomground-deontic@loomground
/plugin install loomground-ingest@loomground
```

Upgrades the three delegating pieces from transparency to governed mode.
(Add `loomground-solver@loomground` and/or `loomground-versum@loomground`
only if you also want those standalone tools — RVND doesn't need them.) This
path is only reachable if you have access to the `loomground` marketplace;
if `flxk1/loomground-plugins` won't add, stop here and use path A.

### C · A single Loomground skill (standalone — no RVND, no siblings; needs `loomground` access)

```
/plugin marketplace add flxk1/loomground-plugins
/plugin install loomground-deontic@loomground
```

Swap `loomground-deontic` for any of the five plugins in the (author-internal) `loomground` marketplace.

### D · The shared installer (one `settings.json` — the RVND stack, needs `loomground` access)

The RVND stack in one paste: registers both marketplaces and enables `rvnd` **plus the three plane
skills it depends on** — no per-user `/plugin` commands. This is the installer for someone who already
has `loomground` marketplace access and wants the full governed stack in one paste; without that
access, use path A instead (drop the `loomground` marketplace entry and the three plane-skill lines
below). A skill-only user with `loomground` access who doesn't want RVND uses path C — one line, one
plugin.

```json
{
  "extraKnownMarketplaces": {
    "loomground": { "source": { "source": "github", "repo": "flxk1/loomground-plugins" } },
    "rvnd":       { "source": { "source": "github", "repo": "flxk1/RVND" } }
  },
  "enabledPlugins": {
    "rvnd@rvnd":                        true,
    "loomground-governance@loomground": true,
    "loomground-deontic@loomground":    true,
    "loomground-ingest@loomground":     true
  }
}
```

Optional extras (independent tools RVND does not require): add
`"loomground-solver@loomground": true` and/or `"loomground-versum@loomground": true`.

`extraKnownMarketplaces` registers a marketplace from a GitHub repo; `enabledPlugins` turns plugins
on at startup (the `@marketplace` suffix is required). Where to put it:

- **Per project (committed, shared via git):** the project's `.claude/settings.json` — applies when a
  teammate opens *that* project and trusts it.
- **Per machine (one user):** `~/.claude/settings.json`.
- **Admin push to every machine:** `managed-settings.json` —
  macOS `/Library/Application Support/ClaudeCode/managed-settings.json`,
  Linux/WSL `/etc/claude-code/managed-settings.json`,
  Windows `C:\Program Files\ClaudeCode\managed-settings.json` (or the Claude admin console).

Trim `enabledPlugins` to only the skills you want — e.g. for a Loomground-only, RVND-free setup, drop
`rvnd@rvnd` and the `rvnd` marketplace entirely (a single plane skill needs neither RVND nor its
siblings).

---

## Catalog — Public · `rvnd@rvnd` (8 skills)

Server skills, installable by anyone from the public `flxk1/RVND` marketplace. They cascade
**local-first** — the RVND engine decides, signs, and enforces when present; the (author-internal)
plane skills below are the fallback the graph-building skills delegate to when reachable.

| skill | the job | reads / writes |
|---|---|---|
| **govern-an-action** | "Is this allowed, and put it on the record" — dry-run the verdict, or run the full cycle (propose → validate → confirm → apply → display, signed) | read / write |
| **onboard-a-policy** | "Bring this regulation/contract into governance" — lower it into the shared 5D+nD graph (delegating to the plane skills), hand to sign-off | write (graph) |
| **sign-off** | "What's waiting on me?" — the human decision, approve / hold / deny, with named approver + rationale, recorded to the chain | write |
| **resolve-a-conflict** | "Two rules clash" — rank by legal precedence, validate a resolving delta or surface a residual; never picks a host-side winner | write (via sign-off) |
| **audit-the-ai** | "What may this AI do, and where's the proof?" — the pure-read whole-console board, two versions (without-RVND vs with-RVND) | read-only |
| **verify-a-receipt** | "Prove this one decision happened, unaltered" — checked against the Ed25519 chain; announces its audit-of-audit append | write (append) |
| **revoke-or-erase** | "Pull that authority / erase this subject" — tighten immediately, restoring needs approval; erasure is a signed tombstone | write |
| **build-a-surface** | "Assemble a governed app screen" — compose + lint the five cards so a surface can't show a request as a grant; drives no server | read |

---

## Catalog — Author-internal · `loomground` (17 skills across 5 plugins)

Plane authoring skills, Apache-2.0 licensed but shipped from a marketplace repo
(`flxk1/loomground-plugins`) that is currently private — not installable by a stranger. Each is
standalone once you have access.

### `loomground-governance@loomground` — 1
- **loomground** — Express an AI-governance requirement as a **verified `.lg` policy-graph patch**
  (oversight, reservation, prohibition, separation-of-duty/quorum, redress, delegation, disclosure);
  validate or fix a patch; judge whether a requirement is expressible.

### `loomground-deontic@loomground` — 1
- **deontic** — Transcribe a natural-language norm into a **verified deontic formula**
  `O/P/F(bearer : action)`; classify the Hohfeldian incident (claim, duty, privilege, no-right,
  power, liability, immunity, disability).

### `loomground-ingest@loomground` — 1
- **loomground-ingest** — Drive the **ingest plane**: turn an acquired artifact into a **dimensioned
  subgraph** for the Versum mental model. Dry-run by default (nodes, edges, dimension, provenance,
  quarantine); writes only through a governed Versum sink.

### `loomground-solver@loomground` — 7
- **analyse-risks** — Score and rank risks by **impact × likelihood** and prioritise mitigations.
- **estimate-liability** — Estimate the conditional probability of liability as a **calibrated range**
  (Bayesian). Organisational estimate, not legal advice.
- **litigation-risk-assessor** — Quantify exposure, weigh merits, recommend **settle vs fight**.
- **opponent-modeler** — Model an adversary — options, payoffs, likely move, exploitable tendencies.
- **probability-tracker** — Maintain and **update a calibrated probability** as evidence arrives.
- **strategic-analysis** — Analyse a competitive/adversarial position — moves, threats, opportunities,
  plan — via decision methods + possible-worlds.
- **advise-solver-addons** — Assess whether a Solver problem needs the **world-model add-on**, or
  whether runs are ready for metacognitive analysis.

### `loomground-versum@loomground` — 7
- **loomground-kg** — The **cockpit** over the Versum knowledge graph: state, what grounds a claim,
  what to run next, routing.
- **loomground-kg-chat** — Conversational, **read-only Q&A** over the KG, grounded on every read,
  local-model-first.
- **loomground-knowledge-write** — The **single write path** into a KG; delegates every executable
  write to the capture-to-kg writer. Never fetches.
- **loomground-curate** — Coordinate-identity curation to **mint the concept / mental-model layer**
  (whole KG or one domain); build domain canon.
- **loomground-enrich** — **Grow the graph from research findings** — extract, validate, propose with
  confidence; writes route through `loomground-knowledge-write`.
- **loomground-organise** — Organise documents into a Versum by **shared mental models**, a person
  confirming every placement.
- **loomground-mental-model** — The **mental-model engine**: scan content into a grounded ConceptGraph
  and project it to the format that answers the question.

---

## How they relate

The plane skills **author** each facet of the shared graph (governance, deontic, ingest, reasoning,
knowledge). The RVND skills **operate** that graph against a real signed engine — cascade
engine-first, delegate to the plane skills as fallback when reachable, sign and enforce. RVND runs
on its own from the public marketplace; the plane skills only add authoring reach for someone who
also has `loomground` marketplace access.

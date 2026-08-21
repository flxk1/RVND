<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Loomground & RVND — skill catalog and install

Two tiers, two marketplaces. **25 skills** total.

| tier | marketplace | licence | what it is | count |
|---|---|---|---|---|
| **Loomground planes** (free) | `loomground` (`flxk1/loomground-plugins`) | Apache-2.0 | standalone, single-facet authoring skills | 5 plugins · **17 skills** |
| **RVND server** (commercial) | `rvnd` (`flxk1/RVND`) | AGPL-3.0 | user-action skills that operate the shared graph on a signed engine | 1 plugin · **8 skills** |

---

## Install — what needs what

**The dependency rule:**

- **Install RVND → you need both.** RVND's skills *delegate* to the free Loomground plane skills they
  depend on: `loomground-governance`, `loomground-deontic`, `loomground-ingest`. So you install RVND
  **and** those three (both marketplaces). `loomground-solver` and `loomground-versum` are independent
  tools RVND does **not** require — add them only if you want them.
- **Install a single Loomground skill → standalone.** It needs **neither RVND nor any other Loomground
  skill.** Install just the one; nothing else.

### A · RVND (needs both) — the minimal working stack

```
/plugin marketplace add flxk1/loomground-plugins
/plugin marketplace add flxk1/RVND
/plugin install rvnd@rvnd
/plugin install loomground-governance@loomground
/plugin install loomground-deontic@loomground
/plugin install loomground-ingest@loomground
```

That's everything RVND requires. (Add `loomground-solver@loomground` and/or
`loomground-versum@loomground` only if you also want those standalone tools — RVND doesn't need them.)
Or, after the two `marketplace add` lines, run `/plugin` and click-install from the browser.

### B · A single Loomground skill (standalone — no RVND, no siblings)

```
/plugin marketplace add flxk1/loomground-plugins
/plugin install loomground-deontic@loomground
```

Swap `loomground-deontic` for any of the five free plugins.

### C · The shared installer (one `settings.json` — the RVND stack)

The RVND stack in one paste: registers both marketplaces and enables `rvnd` **plus the three plane
skills it depends on** — no per-user `/plugin` commands. This is the installer for the RVND audience.
(A skill-only user doesn't want this; they use path B — one line, one plugin.)

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

## Catalog — Commercial · `rvnd@rvnd` (8 skills)

Server skills. They cascade **local-first** — the RVND engine decides, signs, and enforces when
present; the free plane skills are the fallback the graph-building skills delegate to.

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

## Catalog — Free · `loomground` (17 skills across 5 plugins)

Plane authoring skills. Each is standalone.

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

The free plane skills **author** each facet of the shared graph (governance, deontic, ingest,
reasoning, knowledge). The commercial RVND skills **operate** that graph against a real signed engine
— cascade engine-first, delegate to the plane skills as fallback, sign and enforce. That is why RVND
needs both tiers, while a single plane skill stands alone.

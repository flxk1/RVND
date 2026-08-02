<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# `.rvnd` session bundle — v1 schema (S1)

Concrete field layout for the environment-level session bundle implemented by
the gated session import/export surface. The compatibility name remains
`.rvnd`; `.lg` is reserved for a future schema-major change and is not claimed
by this release.

## The load-bearing insight: configs ARE the chain

In RVND every *governed* config is already an event on the signed chain — `register_party`,
`register_connector`, policy `set_lock_mode`/`disable` (with accepted_by+reason), reservations,
autonomy, obligations. So **embedding a workspace's chain captures all its configs automatically**;
the bundle must NOT re-serialize configs (that would be a redundant, drift-prone cache). Only
**off-chain** state needs separate capture:

| State | On/off chain | Capture | Load effect |
|---|---|---|---|
| patches, parties, connectors, reservations, autonomy, obligations, audit | **on-chain** | embed the signed log | replay/apply = **governed, recorded** |
| **policy (lock mode / oversight / opt-out / access control)** | **FILE-backed + chain audit** | `config` (the policy file) | file written back on restore |
| layout (positions, routers, view, solo-as-view) | off-chain (presentation) | `presentation` | applied **no-write** |
| drafts (pasted policy/map/cards/officers/chat) | off-chain (the persistence gap) | `drafts` (read from `draft_store`, server-sourced; S8) | rehydrated into `draft_store`, **no chain write** |
| rail order / focus / global view | off-chain (presentation) | `rail` | applied no-write |

> **S2 finding (corrected):** "configs are chain projections" is only *mostly* true. Policy is a
> **dual-write** — a chain event for audit AND `save_policy` to a state FILE (`load_policy` reads
> the file, not the chain). Embedding the chain captures the audit but **not the current state**,
> so the policy file travels in `config` or it's lost. Chain-projected config (parties, connectors,
> use_cases, reservations) is captured by the chain embed; file-backed config is captured explicitly.
> The `config` hash is in the manifest, so a tampered policy file → `altered_content`, fail-closed.

This refines **S2**: config-capture coverage is a *replay* test — "replaying the embedded chain
reconstructs every config subsystem" — proving nothing governed lives off-chain and gets lost.

## Bundle skeleton

```jsonc
{
  "format": "rvnd-session",            // magic; reject if absent
  "schema_version": "1.0",             // major.minor; unknown MAJOR -> refuse (fail-closed)
  "patch_format": "lg",                // "lg" | "loom" (deprecated alias accepted on load, S11)
  "meta": {
    "created": "<rfc3339>",
    "modified": "<rfc3339>",
    "parent_version": "<bundle-hash|null>",   // lineage for branch-on-continue (S9)
    "origin_role": "<role>",                  // NO named identity (no-id wall, I4)
    "app_version": "<rvnd>", "engine_version": "<loomground_lang>",
    "loomground_version": "<vocab>",          // for conformance/compat
    "workspace_count": <n>
  },
  "workspaces": [
    {
      "id": "<stable-id>", "name": "<label>",
      "chain_mode": "full",              // v1 default; "from_checkpoint" reserved for S18
      "chain": {
        "log": [ /* signed event objects, in order, exactly as on disk */ ],
        "tip_hash": "sha256:<hash of last event>",
        "checkpoint": null              // {state_at, tip_hash, sig} when chain_mode=from_checkpoint
      },
      "presentation": { "positions": {…}, "routers": {…}, "view": "patch|arrange|desk|matrix", "solo_view": [ … ] },
      "drafts": { "policy_paste": …, "map": …, "cards": …, "officers": …, "chat": … }  // surface payloads from draft_store, inline
    }
  ],
  "rail": {
    "order": [ "<ws-id>", … ],
    "focused": "<ws-id>",
    "global_view": {…}
    // NOTE: governed routing (group-bus floors, federation) is ON-CHAIN per workspace;
    // only presentation ordering/focus lives here.
  },
  "manifest": {                          // what the signature covers
    "workspaces": { "<ws-id>": { "chain_tip_hash": "…", "presentation_hash": "…", "drafts_hash": "…" }, … },
    "rail_hash": "…", "meta_hash": "…"
  },
  "signature": { "alg": "ed25519", "pubkey": "<hex>", "sig": "<hex>", "covers": "manifest" }
}
```

## Integrity model (S6)

Three independent checks, all must pass or load is refused (fail-closed):
1. **Each workspace chain verifies internally** — hash-chain links (`prev_hash`) + per-event
   Ed25519 signatures (the existing `verify_chain`), and `chain.tip_hash` matches the last event.
2. **Manifest matches content** — recompute each `chain_tip_hash` / `presentation_hash` /
   `drafts_hash` / `rail_hash` / `meta_hash` from the embedded bytes; any mismatch = tamper.
3. **Bundle signature over the manifest verifies** — one signature binds the whole environment;
   a copied session verifies on any machine with the pubkey.

A checkpoint bundle (S18) adds: the checkpoint is a signed event whose `prev_hash` anchors it to a
real prior tip, so `[checkpoint → HEAD]` stays verifiable without genesis.

**Verify contract (the fail-closed rules):**
- **Ordered + short-circuit** — checks run 1→2→3; the first failure refuses the load and later
  checks are reported "not reached" (no partial open).
- **Not overridable** — a failed verify has **no "open anyway"**. Integrity is enforcement, not an
  advisory badge; a tampered session simply does not load.
- **Located, honest errors** — name *what* and *where* (e.g. "billing ✗ broken at event #63 —
  hash mismatch"), never a generic "invalid file".
- **Refusal taxonomy** — altered content · broken chain · invalid/foreign signature ·
  unknown schema version.
- Prototypes: `mockups/session-verify-pass.html`, `mockups/session-verify-fail.html`.

## Load semantics (S5)

- **`chain`** → replay into the target. Into a *new/own* workspace = clean apply, **recorded** as a
  `SessionLoaded` audit event (which bundle, its hash, when, acting role). Overwriting a *different
  existing* workspace = the guarded path (replace-all confirm / fork-to-new / cancel; default safe).
- **`presentation`** + **`rail`** → applied with **no chain write** (I3).
- **`drafts`** → rehydrated into `draft_store`, no chain write.

## Versioning / compat (I6, S11)

- `schema_version` major bump = breaking; unknown major → refuse with a clear message. Minor =
  additive; older readers ignore unknown fields.
- `patch_format`: writer emits `lg`; reader accepts `loom` as a deprecated alias for one release.
- `parent_version` gives branch lineage (S9); null for a root save.

## Sub-decisions
1. **`drafts` capture — RESOLVED: inline (self-contained by default).** Every Save writes a fully
   portable bundle — inline drafts + full chains, no external refs — so a session moves as one file
   (I1) and there is no "collect" step. `drafts` is read from `draft_store` at capture time
   (server-sourced — never taken from the caller) and serialized inline into the bundle.

2. **`id` stability — RESOLVED.** Each workspace/channel carries a **stable uuid** separate from
   the display `name`, so renames don't break `parent_version` diffs or referential integrity (S4).
3. **canonicalization — RESOLVED, with a hard exemption.** Off-chain parts
   (presentation/drafts/rail/meta) are hashed and round-trip-compared in **canonical form** (sorted
   keys, stable array order) so cosmetic reorder never churns the manifest. The **signed chain log
   is byte-preserved verbatim and NEVER canonicalized** — re-serializing signed events would break
   their signatures. Chain integrity = `verify_chain` on the original bytes.

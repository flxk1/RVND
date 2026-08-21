---
name: verify-a-receipt
description: Prove one governance decision really happened, unaltered. Check a receipt against RVND's per-folder Ed25519-signed hash chain, attribute the decision to its rule, key, and approver, and report plainly what the chain can and cannot prove. Verification is a WRITE — it appends an audit-of-audit event to the signed chain, announced before it runs and surfaced by its new event hash. Attributed, not asserted; fail-closed on a broken chain; honest about the chain's limits (key-directory adversary, tombstone erasure). Triggers - "verify this receipt", "is this decision genuine", "check the signed log", "who approved this and under what rule", "prove this decision happened unaltered".
---

# verify-a-receipt

The one thing a user reaches for when they need to say *"prove this one decision really happened,
and hasn't been changed since."* Take a receipt (or a span of a folder's history) and check it
against RVND's per-folder Ed25519-signed hash chain, so the decision on record carries its rule,
its key, and its approver — attributed, not asserted.

This skill grants nothing and applies nothing. Its job is to tell the truth about what the chain
proves — and to be honest that the act of verifying is itself recorded.

## Verification is a write — announce it first

Verifying **appends an audit-of-audit event to the signed chain**. It is not a passive read. Before
running, say so in plain words:

> Verifying appends an audit-of-audit event to the signed chain.

Then run, and **surface the new event's hash** in the result, so the append is visible and itself
auditable. Never verify silently as though it left no trace.

## What it does

1. **Announce the append**, then **read** the per-folder Ed25519-signed hash chain and the receipt.
2. **Verify** the receipt's position and signature against the chain.
3. **Attribute** each decision to the rule that produced it, the signing key, and — for a
   loosening — the named approver.
4. **Report** the verification as a discrete state (verified / unverified), the new audit-of-audit
   event's hash, and the limits of what the chain proves, stated plainly.

## Cascade & the shared graph

This skill operates one dimensioned Subgraph and cascades **local-first** — see
`../../references/ingest-cascade.md` for the cascade, the plane list, and the honesty rules; this
skill does not restate them. Here local-first has a hard edge: **verifying the chain needs the
engine.** The Ed25519 signing, the hash-chain walk, and the audit-of-audit append are server-side
operations — there is no cloud-LLM fallback that can prove a signature or extend a signed chain.

- **Engine first.** When RVND is present and the folder is governed, it verifies and appends. Done.
- **No engine → it cannot verify.** The skill **fails closed**: it says the chain cannot be
  verified here and stops. It never simulates a pass, never asserts "probably genuine", never
  fabricates an event hash. Absence of the engine is reported as absence of proof, not as a green light.

## What the chain does and does not prove

- It proves that a recorded decision was appended under a known key and has not been altered since.
- Tamper-evidence against an adversary who can also write the key directory holds **only** with
  the opt-in key protections (encrypted keys at rest, genesis key pinning) and the log shipped
  off-host. Say so; do not overclaim.
- Erasure is a **signed tombstone**: it purges this folder's record and blocks re-ingestion, but
  it cannot recall copies that already left the boundary.
- The chain proves integrity of what was recorded, not that the policy behind the decision was correct.

## The rule

Attributed, not asserted. A receipt without its rule, key, and approver is incomplete and reads as
**unverified**. If chain verification fails — or the engine is absent — treat downstream reliance as
fail-closed.

## More

- For the whole governance board rather than one receipt, use `audit-the-ai`.
- `references/reference.md` - chain structure, verification, and the exact limits to report.
- `../../references/ingest-cascade.md` - the cascade, the plane list, and the honesty rules.
- `../../references/protocol.md` - the shared protocol and Sign routing.
- `references/eval.json` - what it drives, guarantees, and review status.

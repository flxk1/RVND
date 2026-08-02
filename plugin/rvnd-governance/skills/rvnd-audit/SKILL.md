---
name: rvnd-audit
description: Verify RVND's tamper-evident audit trail - check a receipt against the per-folder Ed25519-signed hash chain, attribute each decision to its rule and key, and report what the chain can and cannot prove. Verification records an audit-of-audit event; drives RVND audit; attributed-not-asserted; fail-closed on a broken chain. Triggers - "verify the audit chain", "is this receipt genuine", "check the signed log", "who approved this and under what rule", "prove this decision happened".
---

# rvnd-audit

The verify step. Take a receipt or a folder's history and check it against RVND's signed chain, so
every decision on record carries its rule, its key, and its approver — attributed, not asserted.

This skill grants nothing, but verification records an audit-of-audit event in the signed chain.
Its job is to tell the truth about what the chain proves.

## What it does

1. **Read** the per-folder Ed25519-signed hash chain and the receipt in question.
2. **Verify** the receipt's position and signature against the chain.
3. **Attribute** each decision to the rule that produced it, the signing key, and — for a
   loosening — the named approver.
4. **Report** the verification as a discrete state (verified / unverified), with the limits of
   what it proves stated plainly.

## What the chain does and does not prove

- It proves that a recorded decision was appended under a known key and has not been altered since.
- Tamper-evidence against an adversary who can also write the key directory holds **only** with
  the opt-in key protections (encrypted keys at rest, genesis key pinning) and the log shipped
  off-host. Say so; do not overclaim.
- Erasure is a **signed tombstone**: it purges this folder's record and blocks re-ingestion, but
  it cannot recall copies that already left the boundary.

## The rule

Attributed, not asserted. A receipt without its rule, key, and approver is incomplete and reads as
**unverified**. If chain verification fails, treat downstream reliance as fail-closed.

## More

- `references/reference.md` - chain structure, verification, and the exact limits to report.
- `../../references/protocol.md` - the shared protocol and Sign routing.
- `references/eval.json` - what it drives, guarantees, and review status.

# verify-a-receipt - reference

## What it drives

The RVND audit chain and the security dashboard. This skill reads the per-folder Ed25519-signed
hash chain, verifies a receipt or a span of history against it, and reports the result with its
attribution and its limits. Verification appends an audit-of-audit event; the server signs it and
the skill holds no signing capability of its own.

## Cascade — local-first, with a hard edge

This skill follows the shared cascade in `../../references/ingest-cascade.md`: engine first, cloud
fallback only when the engine is absent. But verification has a hard edge — proving a signature and
appending to the signed chain are **server-side operations**, so there is no LLM fallback that can
verify. When the engine is present it verifies and appends; when it is absent the skill **fails
closed**, reports that the chain cannot be verified here, and stops. It never simulates a pass and
never fabricates an event hash.

## Announce the append before you verify

Verification is a **write**, not a read: it appends an audit-of-audit event to the signed chain.
State that before running — "Verifying appends an audit-of-audit event to the signed chain" — then
surface the **new event's hash** in the result so the append is itself visible and auditable.

## The chain

Every decision RVND applies is appended to a per-folder, Ed25519-signed hash chain. Each entry
links to the previous one, so an alteration breaks the chain from that point forward. Verification
checks that a receipt sits at its claimed position and that the signature holds.

Read the security dashboard (`security/v1`) alongside the chain for the server's own statement of
security decisions and known limitations — it is part of an honest report.

## Attribution — the whole point

A verified entry is not just "present". Report, for each decision:

- the **rule** that produced the verdict;
- the **signing key** the entry was signed under;
- the **approver** and rationale, for anything that loosened authority.

An entry missing any of these is incomplete. Render it as **unverified**, not as a pass.

## The limits — state them plainly

Do not let a green result overclaim:

- Tamper-evidence against an adversary who can also write the key directory holds **only** with
  the opt-in protections: encrypted keys at rest, genesis key pinning, and the log shipped
  off-host. Without those, a sufficiently privileged local attacker is outside what the chain
  proves.
- Erasure is a **signed tombstone**. It purges this folder's record and blocks re-ingestion. It
  cannot recall copies that already left the boundary; do not imply otherwise.
- The chain proves integrity of what was recorded. It does not prove that the policy behind a
  decision was correct — only that the decision was taken and signed as shown.

## Guardrails

- Verification is a write: announce the audit-of-audit append **before** running, and surface the
  new event's hash. The skill never signs or erases directly.
- Attributed, not asserted — no verdict without its rule, key, and approver.
- Cascade local-first: the engine verifies and appends; with no engine the skill fails closed and
  cannot verify — never a simulated pass, never a fabricated event hash.
- Verification status is a discrete lamp (verified / unverified), never a score.
- Fail-closed: a broken or unverifiable chain means downstream reliance stops, not "probably fine".

## Pairing

Verifies the receipts `govern-an-action` and `sign-off` produce. When an audit turns up something
wrong — a broken chain, an unauthorised action — `revoke-or-erase` owns the response. For the whole
governance board rather than one receipt, use `audit-the-ai`.

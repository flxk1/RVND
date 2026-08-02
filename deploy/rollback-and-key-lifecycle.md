<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Rollback and key lifecycle

Operational answers to two questions a signed, hash-chained, sealed-store system
makes non-trivial: **"can I roll back to a previous wheel?"** and **"how do I
rotate a key?"** Both are constrained by the same design fact — the audit chain
is append-only and self-attesting, and its identity key is pinned for the life
of the chain.

## Wheel rollback

The unit of rollback is the wheel (`python -m build --wheel`, produced by CI so a
rollback artifact always exists). The **data** — the chain, keys, sealed stores —
is separate. A code rollback is supported only together with a checkpoint made
by that same release. Running an older wheel against data written by a newer
release is an unsupported downgrade and must be refused operationally.

**Chain format compatibility.** The chain has no explicit version stamp; the
reader infers version by field presence:

| Field present | Introduced | An older reader lacking it… |
|---|---|---|
| `prev_hash` | 0.6.5 | counts the event as `legacy` (accepted) |
| `signature` | 0.6.6 | — |
| `host_id` | 0.6.8 | **silently ignores it** → loses A2 host-divergence detection |
| `key_rotation` / `air_gap_refused` / `validator_rejected` event kinds | 0.6.8 | tolerates the event; does not act on it |

- **Forward migration (new wheel, old chain): safe and tested.** Missing fields
  degrade gracefully; `server/tests/migration/test_chain_format_migration.py`
  covers 0.6.5 → 0.6.8 boundaries end to end.
- **Downgrade (old wheel, new chain): unsupported.** Historical wheels predate
  newer event semantics and cannot be retrofitted to recognise them. Never point
  an older wheel at a store that a newer release has opened or written.

**Recommended rollback procedure:**

1. Before upgrading, stop writers, run `verify_chain`, and make an atomic backup
   of the log root, sealed stores, and `WORKSPACE_KEY_DIR`.
2. Record the wheel version and a digest of that checkpoint.
3. To roll back, stop the newer service and restore the matching checkpoint
   before installing the earlier wheel. Do not reuse the newer data directory.
4. Run `verify_chain` under the restored wheel before accepting traffic.

There is deliberately no claim that an old executable can detect a format that
did not exist when it was built. The deployment boundary supplies that guarantee:
rollback means restoring a version-matched code-and-data checkpoint; in-place
downgrade is denied by policy.

## Key lifecycle — there is no in-place rotation

**By design, the identity key is immutable for the life of a chain.** This is not
an omission; it is what makes the chain verifiable:

- `verify_chain` loads exactly one verifying key (the current on-disk identity)
  and checks every event against it (`mutation_log.py`).
- The genesis key pin commits the chain to the fingerprint of its first
  `key_registration` event and hard-fails on mismatch — and pin enforcement
  **cannot be downgraded** by unsetting an env var.

Consequently, replacing `identity.priv` with a fresh key makes the *entire*
existing chain fail verification (`ed25519_signature_invalid` + `key_pin_mismatch`).
The `key_rotation` event kind is a **host-move annotation only** — it suppresses
the host-divergence warning when the same key moves hosts; it does not re-key.

### To retire or replace a key (the real procedure)

There is no zero-downtime rotation. To move to a new identity key:

1. **Seal or archive the current chain** (`workspaces … seal`, or copy the log
   dir) — it stays verifiable under its original key as a closed record.
2. **Start a fresh chain** under a new `WORKSPACE_KEY_DIR` (new host subdir →
   new identity key, new genesis pin).
3. Record the cutover (who, when, why) — the old chain's last event and the new
   chain's genesis together are the audit trail of the rotation.
4. The **controller key** (two-key purge co-signer) is likewise per-workspace and
   not rotatable in place; replacing it invalidates prior two-key tombstones'
   `controller_sig`, so treat it the same way — new chain, fresh controller key.

### If a key is compromised

Assume every event signed by the compromised key is now attacker-forgeable
(RR-1 in the threat model). The chain is no longer trustworthy from the
compromise point. Response: preserve the old chain as evidence, start a new
chain under a new key on a clean host, and — for the interval you cannot vouch
for — rely on any off-host copies you shipped, since a single self-attesting host
cannot prove its own integrity against its own key-holder.

---

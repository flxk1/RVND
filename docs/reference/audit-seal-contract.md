<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Sealing through RVND — the consumer contract

This is the stable, public contract a consumer binds to when it wants an
auditable seal from RVND. It exists so a consumer — a person, a vertical like
the legal navigator, or an agent that cannot `import rvnd` to inspect the
package — can bind against ground truth instead of guessing symbol names.

RVND is consumed as a **global Python package**, not a source checkout and not a
connected folder. A consumer does `import rvnd`; if it imports, it seals through
the API below; if it does not, it falls back to its own signed record and names
which backend sealed it. No program root, no folder bridge.

The seal **is** RVND's per-folder, Ed25519-signed, hash-chained mutation log.
It is not an ad-hoc hash placed beside that log. Binding to anything other than
the mutation log is outside this contract.

## The one-call entry point: `rvnd.seal_audit(...)`

The signature (illustrative — a stub, not a runnable block):

<!-- doctest: skip -->
```python
def seal(folder, *, pair_id, payload, event="system", actor="",
          extra=None, log_root=None) -> dict
```

`rvnd.seal_audit` is this function, imported under that name at the package
root. It lives in `rvnd.mutation_log` as `seal`; the package root re-exports
it as `seal_audit` because `rvnd.seal` is already a submodule (at-rest
encryption of a folder's memory — `seal_folder` / `unseal_folder` /
`is_sealed`, the same verb the `workspaces seal` / `unseal` CLI commands use).
Binding this function to the bare name `rvnd.seal` at the package root would
shadow that submodule for every internal consumer that reaches it via
`from . import seal` — a real, reproduced regression, not a hypothetical one.
Bind to `rvnd.seal_audit`, or `rvnd.mutation_log.seal` directly.

Registers `folder`, fingerprints `payload` and binds that fingerprint into the
appended event's `source_hash`, appends the event to the folder's real signed
chain, and reports the chain's integrity. The full `payload` is bound but NOT
stored on disk — only the `extra` descriptor is written.

```python
import rvnd

def seal_review(review: dict, *, audit_dir, contract_id: str) -> dict:
    return rvnd.seal_audit(
        audit_dir,
        pair_id=contract_id,
        payload=review,
        actor="legal-navigator",
        extra={"kind": "legal-navigator/review", "contract_id": contract_id},
    )
```

The return shape is the receipt:

```python
{
    "backend": "rvnd",
    "audit_id": "280b6e5e-...",   # the appended event's UUID
    "head_hash": "…",             # the chain head after the append
    "verified": True,             # verify_chain().ok immediately after
}
```

## What `seal_audit` does internally

`seal_audit` is a thin composition of the lower-level public API — bind to
these exact names directly when a consumer needs finer control than the
one-call entry point (verified against the installed package):

- `add_known_workspace(folder_path, *, label="", log_root=None) -> dict`
  Registers a folder so its log is writable. The mutation log is allowlist-gated:
  an unregistered folder is refused unless `WORKSPACES_ALLOW_UNREGISTERED=1` is
  set. Register the consumer's audit folder once; the call is idempotent.
- `MutationLog(folder_path, *, log_root=None)` — the signed chain for one folder:
  - `append(event) -> str` — appends a `LogEvent`, returns its `audit_id`.
  - `head_hash() -> str` — the current chain head.
  - `verify_chain() -> ChainVerificationResult` — `.ok` is the integrity verdict.
  - `replay() -> Iterable[LogEvent]` — the sealed records, in order.
  - `count() -> int`.
- `LogEvent(event, folder_path, pair_id, *, source_hash="", actor="system", channel="system", extra={}, ...)`
  `event` is drawn from a **fixed vocabulary**, not free text:
  `admit, live, system, hold, ingest, classify, extract, delete, purge, reject,
  stale, supersede, air_gap_refused, validator_rejected, key_registration,
  key_rotation`. A consumer review that has no lifecycle meaning to RVND is
  recorded as `event="system"`; the domain payload goes in `extra`, and its
  content is bound through `source_hash`.
- `signature(text) -> str` — a deterministic content fingerprint (normalise +
  SHA-256). `seal_audit` uses it to bind the caller's payload into
  `source_hash`; it is a content fingerprint, not the chain's Ed25519 signing
  layer — each appended event separately carries its OWN Ed25519 `signature`
  field, set by `MutationLog.append`, which is what `verify_chain()` checks.
- `list_known_workspaces(log_root=None)`, `remove_known_workspace(folder_path, *, log_root=None)`.

There is no `seal_record`, `append_record`, `audit.seal`, `mutation_log.append`,
or `mint_canonical` in RVND. An adapter that probes for those names finds
nothing and stays unbound. Bind to the names above, or call
`rvnd.seal_audit(...)`.

The one-call form wraps this manual sequence:

```python
import json, rvnd

def seal_review(review: dict, *, audit_dir, contract_id: str) -> dict:
    # 1. register the consumer's audit folder (idempotent).
    rvnd.add_known_workspace(audit_dir, label="legal-navigator")
    # 2. bind the review's content to a fingerprint.
    content_hash = rvnd.signature(json.dumps(review, sort_keys=True, default=str))
    # 3. append to the signed chain.
    log = rvnd.MutationLog(audit_dir)
    audit_id = log.append(rvnd.LogEvent(
        event="system",
        folder_path=str(audit_dir),
        pair_id=contract_id,
        source_hash=content_hash,
        actor="legal-navigator",
        extra={"kind": "legal-navigator/review", "contract_id": contract_id},
    ))
    # 4. the receipt.
    verdict = log.verify_chain()
    return {
        "backend": "rvnd",
        "audit_id": audit_id,          # a UUID, e.g. 280b6e5e-...
        "head_hash": log.head_hash(),  # the chain head
        "verified": verdict.ok,        # True on an intact chain
    }
```

Each appended record carries an Ed25519 `signature` and a `prev_hash`
(`GENESIS` for the first), so `verify_chain()` and `replay()` prove the chain
was not altered after the fact.

## The audit folder — one per consumer, and clearly configurable

Each consuming vertical seals into **its own** audit folder, not a shared one.

RVND's default log root is `LOG_ROOT_DEFAULT` (`~/.workspace/log`). The audit
location is controlled at the source, one precedence rule everywhere it
applies — an explicit `log_root=` argument (or the CLI's `--log-root` flag)
wins, then the `RVND_LOG_ROOT` environment variable, then the default:

- **`RVND_LOG_ROOT`** — set this environment variable to redirect every
  RVND-governed folder's audit log to a different root, without a code change.
- **`--log-root PATH`** — the `workspaces` CLI's global flag; wins over
  `RVND_LOG_ROOT` for that invocation.
- **`log_root=` / `rvnd.resolve_log_root(explicit=None)`** — the same
  precedence, callable directly. Pass `log_root=` through to `rvnd.seal_audit`,
  `add_known_workspace`, or `MutationLog` to pin a consumer's audit folder
  independently of the process-wide environment variable.
- `workspaces doctor` prints the resolved location so an operator can confirm
  where audits are actually landing.

A consumer that wants its own separately-documented setting (for example
`LEGAL_NAVIGATOR_AUDIT_DIR`) can still layer one on top — resolve it at
startup and pass the result through as `log_root=` — but for the common case
of "point RVND's audit trail somewhere else," `RVND_LOG_ROOT` / `--log-root`
is now the direct, documented control.

## Absence and fallback

If `import rvnd` fails, seal with the consumer's own signed record (for example a
content hash plus a `loomground-solver` replay signature) and name the backend in
the seal block: `"backend": "plane-native"` rather than `"rvnd"`. Never silently
skip the seal — an audit trail must be honest about which backend produced it.

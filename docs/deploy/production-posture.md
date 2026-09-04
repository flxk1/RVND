<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Production deployment posture

The engine's defaults favour a keyless legacy workspace: the mutation-log chain
carries hash-chain protection, but tamper-evidence is not fail-closed and actor
attribution on a shared host is self-asserted. A production deployment tightens
those defaults with environment configuration and, for the strongest claims, an
OS-level control. This runbook states the recommended settings by trust model.

Each setting is opt-in. None changes engine behaviour until you set it, so a
deployment adopts exactly the controls its trust model requires.

## Trust model, then controls

Pick the row that matches the deployment and apply its controls. Later rows
include the controls of the rows above them.

| Trust model | Controls |
|---|---|
| Single trusted operator, single host | D1, D3, and the production assertion below |
| Co-located agents that distrust each other | add D2 |
| Preventive-egress claim (no cloud data leaves the host) | add D4 |

## The production assertion (`WORKSPACE_REQUIRE_STRICT_PINNING`)

By default, when a signing key is present but strict key pinning is off, chain
construction only logs a one-time warning that tamper-evidence is not
fail-closed. In production this posture should be a hard failure, not a log line
an operator can miss.

Set `WORKSPACE_REQUIRE_STRICT_PINNING=1` to opt into that. With the flag set,
constructing a mutation log raises `StrictPinningRequiredError` whenever a
signing key is present while `WORKSPACE_STRICT_KEY_PINNING` is off — the process
refuses to run under the degraded posture instead of warning about it. The flag
is a floor: it is re-checked on every chain construction, not once per process.

- The flag is inert on a keyless workspace (nothing to fail closed) and when
  strict pinning is already on.
- Pair it with `WORKSPACE_STRICT_KEY_PINNING=1` (D1). The pairing is the point:
  D1 supplies the fail-closed floor, and the assertion refuses to start if a
  configuration change silently drops it.

<!-- doctest: skip -->
```sh
export WORKSPACE_STRICT_KEY_PINNING=1
export WORKSPACE_REQUIRE_STRICT_PINNING=1
```

## D1 — Fail-closed tamper-evidence against a filesystem adversary

An adversary with write access to the log tree can rewrite the chain and drop
signatures. Strict key pinning makes an unregistered or unsigned chain fail
verification instead of passing as `ok`.

- `WORKSPACE_KEY_PINNING=1` — a fresh chain records its signing identity in a
  genesis `key_registration` event and writes a trust-on-first-use pin.
- `WORKSPACE_STRICT_KEY_PINNING=1` — an unregistered chain fails verification,
  enforcing the pin as a floor from the first event rather than adopting it
  lazily.
- `WORKSPACE_KEY_PIN_DIR=<path>` — relocate the pin file off the log tree onto a
  write-protected mount. The pin's guarantee is only as strong as this
  location's write protection; an adversary who can rewrite both the log and the
  pin defeats it.

<!-- doctest: skip -->
```sh
export WORKSPACE_KEY_PINNING=1
export WORKSPACE_STRICT_KEY_PINNING=1
export WORKSPACE_KEY_PIN_DIR=/srv/rvnd-pins   # a read-only mount for the agent
```

The pin directory must be writable once, at registration, then mounted
read-only for the agent identity. Verify that the agent cannot write to it.

## D2 — Multi-tenant / co-located distrusting agents

On a single host under one uid, RVND treats every local caller as one trust
domain: actor attribution in local mode is self-asserted and forgeable by a
same-uid peer. When co-located agents must not be able to act as one another,
front the engine with a proxy that authenticates the caller and passes a trusted
principal header. See `docs/concepts/bring-your-idp.md` for the full setup.

- `WORKSPACE_PROXY_SHARED_SECRET=<secret>` — the served path accepts a principal
  header only from a proxy that presents this secret; without it the header is
  ignored, so a peer cannot inject one.
- `WORKSPACE_PRINCIPAL_HEADER` / `WORKSPACE_PRINCIPAL_GROUPS_HEADER` — the
  headers the proxy sets from its authenticated session (for example
  `X-Auth-Request-Email`).
- `WORKSPACE_IDENTITY_MAP=<path>` — maps proxy principals to workspace actors.

Run the engine on the served path behind the proxy, not in local mode, for this
trust model. The served principal path is fail-closed: an unauthenticated caller
gets no attributed identity.

## D3 — Single-host hardening

For a deployment where any host shift or key exposure is an incident:

- `WORKSPACE_STRICT_HOST_DIVERGENCE=1` — a `host_id` that changes mid-chain
  without a `key_rotation` marker becomes a hard verification failure rather than
  an advisory warning. Emit a `key_rotation` event on any deliberate host move,
  or verification will treat it as key theft.
- `WORKSPACE_KEY_PASSPHRASE=<passphrase>` — encrypts the identity private key at
  rest, so a copy of the key file alone does not yield a usable signing key.

<!-- doctest: skip -->
```sh
export WORKSPACE_STRICT_HOST_DIVERGENCE=1
export WORKSPACE_KEY_PASSPHRASE=<passphrase from your secret store>
```

Supply the passphrase from a secret store, not a shell profile or the process
environment recorded in an image.

## D4 — OS egress lock (preventive-egress claim only)

The engine-side cloud-LLM guarantee — the import guard and the fail-closed
egress proxy — holds without this control. Apply the OS firewall lock only for a
deployment that claims preventive egress enforcement: that no agent data can
reach a provider except through the auditing proxy, enforced by the host rather
than by the application.

The installers and their trust model are documented in
`deploy/firewall/README.md`. Applying or removing rules requires host
administrator authority and a recovery-capable maintenance window.

1. Dry-run the template for the platform, then apply it as administrator.
2. Confirm the lock took: `scripts/verify_egress_lock.py` exits 0 in live mode
   (it requires root and never mutates the firewall). Exit 1 means the lock is
   not in force; exit 2 means it could not be verified.
3. Static rule verification is not behavioural proof. Also confirm that direct
   TCP and UDP egress fails as the agent identity while the proxy identity can
   reach its configured upstream.

<!-- doctest: skip -->
```sh
sudo deploy/firewall/apply-egress-lock.sh --dry-run --proxy-user rvnd-egress
sudo deploy/firewall/apply-egress-lock.sh --apply   --proxy-user rvnd-egress
sudo scripts/verify_egress_lock.py ; echo "exit=$?"   # expect exit=0
```

## Verifying the posture

After configuring the environment, confirm the settings are in force:

- Construct a mutation log in the production environment and confirm it starts
  without the not-fail-closed warning (D1 plus the production assertion).
- Run `workspaces status` and confirm the key fingerprint matches the intended
  identity across hosts (D3).
- For D2, confirm an unauthenticated local caller receives no attributed actor.
- For D4, confirm `scripts/verify_egress_lock.py` exits 0 and that direct egress
  fails as the agent identity.

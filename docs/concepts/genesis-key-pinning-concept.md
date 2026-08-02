<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Genesis key pinning

Status: shipped as an opt-in trust-on-first-use control.

RVND can bind a signed chain to its genesis identity instead of trusting
whichever public key happens to be present beside the log at verification
time. Set `WORKSPACE_KEY_PINNING=1` when creating a chain. The first
`key_registration` event records the identity fingerprint and public key, and
verification thereafter refuses a different key.

`WORKSPACE_KEY_PIN_DIR` relocates the trust-on-first-use pin outside the log
tree. This matters: a pin inside the same writable tree protects against
accidental re-keying, while a read-only or separately controlled pin directory
also detects an attacker replacing both the log and its local keys.

`WORKSPACE_STRICT_KEY_PINNING=1` refuses legacy chains without a registration.
Without the strict setting, unregistered legacy chains remain readable and
report that no key pin is present. A registered chain is always checked;
clearing `WORKSPACE_KEY_PINNING` cannot downgrade it.

The release scope is deliberately narrow:

- one signing identity per registered chain;
- no identity-key rotation for a registered chain;
- no controller co-signature or external transparency service.

Deployments requiring key rotation or multiple custodians must create a new
registered chain and retain the old chain as signed evidence. RVND does not
claim seamless key succession in this version.

The executable contract is covered by
`server/tests/test_genesis_key_pinning.py`.

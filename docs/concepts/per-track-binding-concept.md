<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Per-track identity and access binding

This document records the implemented boundary contract.

An egress connector stores only a typed credential reference. Accepted schemes
are `env:`, `keydir:`, `oidc:`, and `spiffe:`; secret material is never written
to the connector record, response payload, or audit chain. Resolution occurs at
the boundary immediately before use.

The connector's declared destination class determines enforcement:

- LLM egress is preventive only when the workspace is bound to the RVND egress
  broker and the deployment network lock prevents direct provider access.
- Tool, message, and file egress are attested unless the host routes the actual
  operation through RVND. RVND does not claim control over calls made outside
  its process or deployment boundary.

The egress board reports this distinction as `brokered` or `attested`. Missing,
malformed, unresolved, or disarmed credential references fail closed. The track
strip is a read-only projection of the same server state; it does not infer or
upgrade enforcement in the browser.

Relevant implementation modules are `connectors.py`,
`lock/credential_resolver.py`, `lock/track_broker.py`, `lock/egress_proxy.py`,
and `track_strip.py`.

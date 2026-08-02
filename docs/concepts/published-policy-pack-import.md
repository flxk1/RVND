<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->
# Published policy-pack import

`workspaces.published_policy_pack.import_published_policy_pack` is RVND's
validation boundary for a policy pack published outside the active child
workspace. Import does not grant authority or widen a governance lane.

The classifier or adapter supplies its declared action kinds. The host supplies
its known action-kind registry. A pack is denied when it omits action kinds,
names a kind outside either set, or requests a kind outside the child's approved
RVND governance lane.

The pack fingerprint must equal the active policy fingerprint, and the child's
current governance lane must bind that same fingerprint. Changing the compiled
policy therefore requires a new lane version through the normal approval path.

The importing RVND host—not the published payload—supplies non-empty review
attestations for:

- child safety;
- developmental suitability;
- privacy; and
- jurisdictional applicability.

These dimensions remain mandatory even when a publisher marks the pack
pre-reviewed. Publisher-supplied review claims cannot satisfy the interface.
The values identify RVND's review results; they are not a publisher-controlled
waiver and do not imply a human reviewer.

The interface resolves the child's lane from RVND's persisted governance
state. A caller cannot inject a replacement lane object.

At acceptance, RVND consumes the installed Loomground Governance and Deontic
language contracts and persists their exact package, version, status, and role
with the pack. Governance remains the authoritative policy grammar and
vocabulary; Deontic remains the normative-classification language. A published
compiled pack is not reinterpreted as free-form Deontic prose. If either
language contract is missing or incomplete, import is denied before an Ingest
writer is created.

Only after these checks does the public persistence interface construct an
Ingest `Subgraph` and call `loomground_ingest.versum_writer()`. The Ingest
writer then delegates to RVND's Versum sink; Versum remains the sole persistent
store door.

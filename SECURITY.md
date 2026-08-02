<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Security policy

RVND is a governance and audit system: its value is that its records can be
trusted. Security reports are taken seriously. This policy is maintainer-set and
may be revised; if you are coordinating a disclosure, the terms in effect are
those in this file at the time of your report.

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability. Use GitHub's
[private vulnerability reporting](https://github.com/flxk1/RVND/security/advisories/new)
for this repository (Security → Report a vulnerability). Include:

- a description of the issue and the affected component/version (`__version__`
  in `server/src/workspaces/_version.py`);
- reproduction steps or a proof of concept;
- the impact you believe it has.

Please allow the maintainer time to investigate before public disclosure.

**Expectations (best-effort for a small project, not a contractual SLA):**
acknowledgement within 5 business days; an initial assessment within 15. We aim
for coordinated disclosure within 90 days of a fix being available, credited to
you unless you prefer otherwise.

## Scope

In scope: the RVND server, the egress proxy, the console bridge, the firewall
templates, and the audit-chain integrity guarantees. The
[threat model](docs/reviews/threat-model.md) states the trust boundaries and the
**accepted residual risks** (RR-1…RR-4) — findings that restate a documented
residual limit (e.g. a root-capable host adversary, or a key-holder rewriting a
single-host chain) are already known; novel ways to *reach* those states are
in scope.

Out of scope: the customers' governed agents; the operator's own OS hardening;
issues requiring a pre-compromised host (root/admin) or physical access.

## What "fixed" means here

Every fixed security issue lands with a regression test and, where it fits the
attack taxonomy, an entry in
[`docs/reviews/red-team-findings.md`](docs/reviews/red-team-findings.md) (the
durable register — two meta-tests fail if an entry is deleted). A fix without a
test is not considered complete.

## Supply chain

Releases carry a freshly generated CycloneDX SBOM, a no-copyleft dependency
gate, digest-pinned container inputs, and SHA-pinned first-party dependencies.
Report suspected dependency or supply-chain issues the same way.

---

<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright 2026 flxk1 -->

# Release readiness

Use this checklist for every release candidate. A green dashboard alone is not
a release decision; retain links to the exact commit, CI run, artifacts, and
accepted residual risks.

## Required evidence

- Functional: server, plugin, browser-smoke, render, and quick-start checks pass.
- Compatibility: dependency installation succeeds on Linux, macOS, and Windows.
- Security: static analysis, dependency audit, secret scan, and egress gates pass.
- Deployment: any release claiming preventive egress control includes a live
  proxy plus the verified OS/container network lock. Without that proof the
  product may describe egress as attested, never as prevented.
- Privacy: redaction, erasure, retention, and subject-right tests pass.
- Reliability: concurrency, timeout, replay, migration, and rollback tests pass.
- Packaging: wheel builds from the candidate and installs in a clean environment.
- Publication: the candidate tree matches the intended release tree and artifacts.
- Documentation: version, changelog, licensing, security policy, and runbooks agree.

## Release decision

Record the following outside the source distribution for each release:

- candidate commit and immutable tag;
- CI and artifact links;
- publisher identity and publication proof;
- accepted residual risks and expiry/review date;
- rollback owner and recovery checkpoint.

Do not claim publication, platform support, or a security property without the
corresponding evidence from the exact candidate commit.

## Stable references

- [Threat model](../docs/reviews/threat-model.md)
- [Rollback and key lifecycle](rollback-and-key-lifecycle.md)
- [Retention and subject rights](../docs/concepts/data-retention-and-subject-rights.md)
- [AI Act self-classification](../docs/concepts/ai-act-self-classification.md)
- [Security policy](../.github/SECURITY.md)

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Quarantine — dead-on-arrival copies of retired norm-runtime twins.

These are the ORIGINAL RVND implementations of the eight norm-runtime modules
(rule_extractor, rule_extractor_llm, subsumption_path, subsumption_validator,
obligation_runtime, hohfeld, rule_registry, obligation_scheduler) as they stood
before RVND became a consumer of ``loomground-norm``. Their live counterparts
in ``workspaces/`` are now thin re-export shims over
:mod:`workspaces.adapters.norm`.

Nothing here is imported by live code — that invariant is fenced by
``tests/test_consumed_modules.py``. These files are kept only so the retirement
can be verified against the originals before deletion; they are not part of the
package's runtime and must never be re-wired in.
"""

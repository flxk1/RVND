# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Migration-regression test family.

Goal: any chain written by a prior version of Workspace MUST still verify on the
current runtime, or every upgrade silently destroys the user's audit trail.

Scope:

- One fixture per prior version under ``fixtures/`` (synthetic JSONL captured
  against the migration fixtures).
- ``test_chain_format_migration.py`` walks each fixture through the current
  runtime: load → verify → append → re-verify.
- ``test_key_dir_migration.py`` covers the per-host-key namespacing migration
  introduced in 0.6.8 (B4).

Conventions:

- Tests use ``WORKSPACE_KEY_DIR`` to isolate keypair state per test.
- Tests skip (do not fail) when a fixture is missing — the fixture set grows
  as new versions ship.
- Tests use ``pytest.mark.xfail(strict=True)`` only for assertions that
  document KNOWN behaviour we plan to change in the next release; these xfails
  flip to passing once 0.6.8 lands.
"""

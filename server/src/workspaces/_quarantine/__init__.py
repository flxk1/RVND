# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Quarantine — dead-on-arrival copies of retired consumed-plane twins.

These are the ORIGINAL RVND implementations of modules RVND has retired in favour
of upstream Loomground packages. Their live counterparts in ``workspaces/`` are
now thin re-export shims (or SPLITs that keep only their folder-runtime) over the
``workspaces.adapters`` seam.

  * **norm-runtime** (consumed from ``loomground-norm`` via
    :mod:`workspaces.adapters.norm`): rule_extractor, rule_extractor_llm,
    subsumption_path, subsumption_validator, obligation_runtime, hohfeld,
    rule_registry, obligation_scheduler — kept here whole.

  * **legal-world stack** (consumed from ``loomground-legal`` via
    :mod:`workspaces.adapters.legal`): legal_world, validate (corpus),
    world_corpus_loader, world_relations — kept whole; and the MIGRATED PORTIONS
    of the two SPLIT modules — regulatory_population (the CODE/DOMAIN/TRANCHES
    catalogue + load_instruments loader) and instance (the PartyRef +
    ContractInstance model) — whose folder-runtime (populate_*, default_csv,
    ContractRegistry) stayed live.

Nothing here is imported by live code — that invariant is fenced by
``tests/test_consumed_modules.py``. These files are kept only so the retirement
can be verified against the originals before deletion; they are not part of the
package's runtime and must never be re-wired in.
"""

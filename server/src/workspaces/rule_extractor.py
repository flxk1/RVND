# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's rule extractor — RVND owns neither.

RVND's parallel Phase-1 rule extractor is RETIRED. The five-slot EU-wide
extraction now lives in ``loomground-norm`` (``loomground_norm.rule_extractor``);
this module is a thin compatibility seam re-exporting that surface behind the
historical import names (``RuleFacet``, ``extract_rules``, plus the module-level
helpers callers touch: ``supported_languages``, ``_detect_language``,
``_is_agentless_passive``, ``_segment``).

RVND's normative-fingerprint gate (``nd_routing.score_normative``) is injected
into the plane through the ``adapters/norm`` seam — the workspaces boundary rule
confines every upstream import there. This file carries no extraction logic of
its own; callers are unchanged and migrate to the plane directly, then this
shim is deleted.
"""
from __future__ import annotations

from .adapters.norm import (
    RuleFacet,
    extract_rules,
    supported_languages,
    _detect_language,
    _is_agentless_passive,
    _segment,
)

__all__ = ["RuleFacet", "extract_rules", "supported_languages"]

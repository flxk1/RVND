# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Supply activated RVND norms through Solver's neutral corpus port; internal by design."""

from __future__ import annotations


class RvndNormSource:
    def __init__(self, registry):
        self._registry = registry

    def norm_spans_for(self, instrument_codes: set) -> list[dict]:
        return [
            row for row in self._registry.workspace_items()
            if row.get("kind") == "norm" and
            any(anchor.get("entity") in instrument_codes
                for anchor in row.get("anchors", []))
        ]

    def held_pinpoints(self) -> set:
        return {
            row.get("pinpoint") for row in self._registry.workspace_items()
            if row.get("kind") == "norm" and row.get("pinpoint")
        }

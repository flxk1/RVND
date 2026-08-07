# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Sanctioned re-export seam for the consumed ``deontic`` language package.

RVND consumes the deontic **grammar** (the language) directly from the
independently-versioned ``deontic`` package — exactly as it consumes the
governance grammar through :mod:`.policy_languages` and the 5D / solver model
through :mod:`.solver`. This module is the *only* place RVND reaches into the
``deontic`` package; every workspaces-internal consumer imports the grammar
from here, never ``import deontic`` directly.

Scope — GRAMMAR ONLY. This seam carries the language: the formula shape, the
operator vocabulary (``O`` / ``P`` / ``F`` — there is no ``R``; a *right* is
constructed via the Hohfeld incident + correlative, e.g. :func:`claim_right`),
incident classification, the operator→dimension affinity, conflict detection,
and the language version.

It does NOT carry per-norm **facet data** (a specific norm's operator / bearer
/ action / incident). That data lives in versum and reaches RVND only through
the ``versum → solver → patchbay → rvnd`` chain; RVND must never re-derive or
re-dispatch it, and must never read it directly from versum through here.
"""
from __future__ import annotations

import deontic as _deontic

# -- core grammar -----------------------------------------------------------
DeonticFormula = _deontic.DeonticFormula
VALID_OPERATORS = _deontic.VALID_OPERATORS          # ('O', 'P', 'F')
OP_OBLIGATION = _deontic.OP_OBLIGATION              # 'O'
OP_PERMISSION = _deontic.OP_PERMISSION              # 'P'
OP_PROHIBITION = _deontic.OP_PROHIBITION            # 'F'
MODAL_TO_OP = _deontic.MODAL_TO_OP                  # {'right': 'P', ...} — no 'R'

# -- construction / classification -----------------------------------------
formula_from_fields = _deontic.formula_from_fields
classify_incident = _deontic.classify_incident
correlative = _deontic.correlative
claim_right = _deontic.claim_right                  # right = O(obligor : action) + claim incident
is_grounded = _deontic.is_grounded

# -- reasoning helpers ------------------------------------------------------
dimension_affinity = _deontic.dimension_affinity
detect_conflicts = _deontic.detect_conflicts

# -- Hohfeld vocabulary + version ------------------------------------------
INCIDENTS = _deontic.INCIDENTS
language_version = _deontic.language_version


__all__ = [
    "DeonticFormula",
    "VALID_OPERATORS",
    "OP_OBLIGATION",
    "OP_PERMISSION",
    "OP_PROHIBITION",
    "MODAL_TO_OP",
    "formula_from_fields",
    "classify_incident",
    "correlative",
    "claim_right",
    "is_grounded",
    "dimension_affinity",
    "detect_conflicts",
    "INCIDENTS",
    "language_version",
]

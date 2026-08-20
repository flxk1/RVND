# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Detection completeness — the open-world guard.

The situational solver acts only on what it DETECTS; whether it detected
everything is, in general, unknowable (open-world; the frame/qualification
problem). This module does not pretend to close the open world. It MEASURES
the residual and DECLARES it, and turns the signal into TARGETED oversight —
a human is pulled in where completeness is low, not on every run.

Three signals:
  * **negative space** — a type model lists the issue types a document of a
    class usually carries. Expected-but-absent types are *known* unknowns:
    the single biggest lever, because it converts "what might I have missed"
    into a checklist gap. (Grounds in Workspaces' existing mandatory-content
    checklists.)
  * **dark fraction** — the share of the surface no detector could classify.
    A proxy for unknown-unknowns: it does not say WHAT was missed, only that
    something likely was.
  * **open-world floor** — completeness is never certified 'high' without a
    type model to check against. No model is not evidence of nothing missing.

The band (high / medium / low) gates oversight: low escalates to a human;
high may proceed at grade. Deterministic; no model in the loop.
"""
from __future__ import annotations

from typing import Any

#: Type models — what issue types a document of each class usually carries.
#: A partial, maintainable "what should be here". Extend per domain; a missing
#: model means the open-world floor applies (never certified complete).
TYPE_MODELS: dict[str, set] = {
    "services-contract-de": {
        "liability_cap", "data_processing", "ip_assignment", "warranty",
        "termination", "confidentiality", "governing_law",
    },
    "dpa-de": {
        "data_processing", "confidentiality", "liability_cap",
        "termination", "governing_law",
    },
}

#: dark-fraction thresholds for the band.
_DARK_HIGH = 0.15        # below this, surface is well-accounted-for
_DARK_LOW = 0.40         # above this, too much is unclassified to trust


def completeness_report(
    doc_type: str,
    detected_types: list[str],
    *,
    covered_chars: int = 0,
    total_chars: int = 0,
) -> dict[str, Any]:
    """Estimate and DECLARE detection completeness for one document.

    Returns the negative-space gap (expected-absent), the dark fraction, a
    completeness band, and whether to escalate to a human. Never certifies
    'high' without a type model (open-world floor)."""
    detected = {t for t in detected_types if t}
    model = TYPE_MODELS.get(doc_type)
    has_model = model is not None
    expected_absent = sorted((model or set()) - detected)
    unexpected = sorted(detected - (model or set())) if has_model else []

    dark = 0.0
    if total_chars > 0:
        dark = round(1.0 - min(covered_chars, total_chars) / total_chars, 4)

    # band: the open-world floor caps an unmodelled document at 'medium'
    # however clean it looks; a modelled one is graded on gap + dark.
    if not has_model:
        band = "medium"
    elif not expected_absent and dark <= _DARK_HIGH:
        band = "high"
    elif len(expected_absent) > len(model) // 2 or dark >= _DARK_LOW:
        band = "low"
    else:
        band = "medium"

    return {
        "doc_type": doc_type,
        "has_type_model": has_model,
        "detected": sorted(detected),
        "expected_absent": expected_absent,     # known-unknowns (negative space)
        "unexpected": unexpected,               # model is partial — surface these
        "dark_fraction": dark,                  # unknown-unknowns proxy
        "band": band,
        "escalate": band != "high",             # targeted oversight
        "declared": "detection is not certified complete (open-world)",
    }

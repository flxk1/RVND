# SPDX-License-Identifier: AGPL-3.0-only
"""A decision must name the finding it decided.

`OversightDecision.finding_id` is documented as "one user decision on one
finding". Every producer filled it with a fresh uuid4, so it named nothing —
the audit could not answer which finding the operator accepted, which is the
only question that record exists to answer. Nothing read the field either, so
no test failed and nothing looked wrong.
"""
from __future__ import annotations

import inspect

from rvnd.lock import interactive
from rvnd.lock.core import Finding


def _f(**kw):
    base = dict(tier="B", type="pii_in_argument", severity="high",
                field="email", detail="regex matched: EMAIL")
    base.update(kw)
    return Finding(**base)


def test_identity_is_stable_and_discriminating():
    assert _f().finding_id == _f().finding_id, "same finding must keep one id"
    assert _f().finding_id != _f(field="phone").finding_id
    assert _f().finding_id != _f(severity="low").finding_id
    assert _f().finding_id != _f(detail="something else").finding_id


def test_no_decision_producer_invents_an_id():
    """The regression guard. A uuid here is indistinguishable from a real id at
    the point of reading, so it has to be caught at the point of writing."""
    src = inspect.getsource(interactive)
    assert "uuid" not in src, (
        "a decision id must be derived from its finding, not allocated — "
        "an allocated id points at nothing and cannot be traced back")
    assert src.count("finding_id=f.finding_id") == 3, (
        "all three decision producers must name their finding; found "
        f"{src.count('finding_id=f.finding_id')}")

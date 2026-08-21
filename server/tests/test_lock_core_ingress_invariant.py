# SPDX-License-Identifier: AGPL-3.0-only
"""The ingress redaction depends on an invariant that was never written down.

`_decide_ingress` redacts by collecting `f.field` from high-severity findings. A
high-severity finding with `field=None` therefore contributes nothing to redact
and falls through to `action="allow"`. That is safe only because every producer
on the ingress path sets a field. It used to compute a `has_high` flag that
would have caught the other case, and never used it; removing dead code that
happens to be a spare tyre means pinning the road.
"""
from __future__ import annotations

from rvnd.lock.core import tier_a_check_response, tier_b_scan_dict


def test_ingress_findings_always_carry_a_field():
    payload = {"email": "a@b.com", "note": "call 555-867-5309",
               "nested": {"ssn": "123-45-6789"},
               "items": ["x@y.com", {"phone": "555-0100"}]}
    findings = list(tier_a_check_response(payload, task_scope={"note"}))
    findings += list(tier_b_scan_dict(payload))
    assert findings, "fixture must actually produce findings, or this proves nothing"
    fieldless = [f for f in findings if f.field is None]
    assert not fieldless, (
        "a finding with no field contributes nothing to fields_to_redact and so "
        f"falls through to allow: {[(f.tier, f.type, f.severity) for f in fieldless]}")

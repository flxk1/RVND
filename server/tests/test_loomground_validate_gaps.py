# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Apply-stage well-formedness the fuzz campaign found accept-and-deferred.

These parse fine but were silently accepted (to fail later, or silently degrade — e.g. a
degenerate `0 of {}` quorum becoming "any active human"). validate() now rejects them,
fail-closed. The valid forms must still pass (no false positives).
"""
from __future__ import annotations

import pytest

from rvnd import loomground_lang as L


def _validate(loom: str):
    return L.validate(L.parse(loom))


REJECT = {
    "undeclared grantee":
        "gate g risk low grant ghost\ncord g -> master\n",
    "empty quorum set":
        "actor a\ngate g risk low grant a\nreserve k by 0 of { }\ncord a -> g\ncord g -> master\n",
    "zero quorum":
        "actor a\ngate g risk low grant a\nreserve k by 0 of { legal }\ncord a -> g\ncord g -> master\n",
    "over-quorum (unsatisfiable)":
        "actor a\ngate g risk low grant a\nreserve k by 3 of { legal, finance }\ncord a -> g\ncord g -> master\n",
    "bad duration unit":
        "actor a\ngate g risk low grant a\nreserve k by legal duration 5x : halt\ncord a -> g\ncord g -> master\n",
    "bad on_elapse":
        "actor a\ngate g risk low grant a\nreserve k by legal duration 5d : maybe\ncord a -> g\ncord g -> master\n",
    # v0.8 (SYNTAX §3): an obligation must attach to a DECLARED gate. Was
    # accept-and-deferred — evaluate() withheld at the boundary, but the ill-formed
    # graph validated ok (the reject-obligation-undeclared-gate vector caught it).
    "obligation on undeclared gate":
        "actor a\ngate g risk low grant a\nobligation disclosure on ghost\ncord a -> g\ncord g -> master\n",
    "obligation on an actor (not a gate)":
        "actor a\ngate g risk low grant a\nobligation disclosure on a\ncord a -> g\ncord g -> master\n",
}

ACCEPT = {
    "valid m-of-n + duration":
        "actor a\ngate g risk low grant a\nreserve k by 2 of { legal, finance } duration 30d : halt\ncord a -> g\ncord g -> master\n",
    "valid proceed":
        "actor a\ngate g risk low grant a\nreserve k by legal duration 3d : proceed\ncord a -> g\ncord g -> master\n",
    "valid multi-grantee (single grant keyword)":
        "actor a\nactor b\ngate g risk low grant a b\ncord a -> g\ncord b -> g\ncord g -> master\n",
    "valid single-role reserve":
        "actor a\ngate g risk low grant a\nreserve k by legal\ncord a -> g\ncord g -> master\n",
    "valid obligation on a declared gate":
        "actor a\ngate g risk low grant a\nobligation disclosure on g\ncord a -> g\ncord g -> master\n",
}


@pytest.mark.parametrize("name,loom", list(REJECT.items()), ids=list(REJECT))
def test_rejected_at_apply(name, loom):
    v = _validate(loom)
    assert not v["ok"], f"{name}: expected rejection, validate said ok"


@pytest.mark.parametrize("name,loom", list(ACCEPT.items()), ids=list(ACCEPT))
def test_accepted(name, loom):
    v = _validate(loom)
    assert v["ok"], f"{name}: unexpectedly rejected — {v['errors']}"

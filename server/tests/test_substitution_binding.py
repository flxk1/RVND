# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Does the attested posture still stand for measured mediation?

RVND attests which controls are in force and, separately, MEASURES the mediation
gap by reconciling two ledgers. Reading the first as evidence about the second is
a substitution, and these pin that it gets checked rather than assumed — above
all that a hardening posture over a worsening mediation gap is reported `gamed`.

Grounded in the real chain: a genuine `run_workflow` produces the authorised
window and a step effect logged with no gate-verdict behind it produces the
unauthorised one, exactly as the reconciliation tests do.

The one-second gap is load-bearing, not padding. `reconciliation_binding._iso`
renders window bounds at WHOLE-SECOND resolution, so two bounds inside the same
second collapse to one ISO string and the window reads UNRECONCILED. A fixture
that split the chain microseconds apart passed or failed depending on where the
run happened to fall relative to a second boundary. The chain is built once and
shared so the wait is paid a single time.
"""
from __future__ import annotations

import time

import pytest

from workspaces.mutation_log import MutationLog
from workspaces.substitution_binding import substitution_projection


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """Clean run, then — a full second later — an unauthorised effect."""
    tmp = tmp_path_factory.mktemp("subst")
    import os
    os.environ["WORKSPACES_ALLOW_UNREGISTERED"] = "1"
    os.environ["WORKSPACE_KEY_DIR"] = str(tmp / "keys")

    fc = tmp / "ws"; fc.mkdir(); log = tmp / "log"
    from workspaces.workflows import (
        Workflow, WorkflowStep, _log_workflow_event, define_workflow, run_workflow,
    )
    define_workflow(str(fc), Workflow(name="intake", description="t",
                    steps=[WorkflowStep(skill_id="p:a")]), log_root=log)
    assert run_workflow(str(fc), "intake",
                        dispatcher=lambda **kw: {"ok": True}, log_root=log)["ok"]

    boundary = max(e.ts for e in MutationLog(str(fc), log_root=log).replay())
    while time.time() <= boundary + 1.0:        # clear the whole-second floor
        time.sleep(0.01)
    _log_workflow_event(str(fc), run_id="ghost", workflow="wf", step_index=0,
                        state="done", skill_id="p:x", log_root=log)

    events = list(MutationLog(str(fc), log_root=log).replay())
    split = max(e.ts for e in events)
    assert split - boundary >= 1.0, "windows must be resolvable at second resolution"
    return {"folder": str(fc), "log": log, "events": events,
            "lo": min(e.ts for e in events), "split": split}


def _project(chain, posture_change=None):
    return substitution_projection(
        chain["events"],
        prior_since_ts=chain["lo"], prior_until_ts=chain["split"],
        since_ts=chain["split"], until_ts=chain["split"] + 1.0,
        posture_change=posture_change)


def test_the_windows_carry_the_readings_the_kinds_depend_on(chain):
    """Guard the fixture itself: if the prior window stopped being clean or the
    current one stopped being unauthorised, every kind below would still pass
    for the wrong reason."""
    out = _project(chain, "hardened")
    r = out["readings"]
    assert r["unauthorised_rate_before"] == 0.0
    assert r["unauthorised_rate_after"] > 0.0


@pytest.mark.parametrize("posture_change, expected", [
    ("hardened", "gamed"),          # controls went on, unauthorised effects went up
    ("weakened", "tracking"),       # both moved the same way
    ("unchanged", "tracking"),
    (None, "unchecked"),            # nobody compared the postures
    ("incomparable", "unchecked"),  # no ordering exists, so no direction to report
])
def test_the_substitution_is_named(chain, posture_change, expected):
    assert _project(chain, posture_change)["kinds"] == [expected]


def test_a_hardening_posture_over_a_worsening_gap_says_why(chain):
    sub = _project(chain, "hardened")["substitutions"][0]
    assert sub["metric"] == "enforcement_posture"
    assert sub["stands_for"] == "complete_mediation"
    assert "got worse" in sub["why"]


def test_the_projection_mutates_nothing(chain):
    """Same doctrine as the sibling bindings: a read never changes the chain."""
    before = MutationLog(chain["folder"], log_root=chain["log"]).count()
    _project(chain, "hardened")
    _project(chain, None)
    assert MutationLog(chain["folder"], log_root=chain["log"]).count() == before

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Universal governance chat routes one input to ingest, intake, or ask.

The tests cover inferred routing and explicit intent override.
"""
from __future__ import annotations

import os

from workspaces import governance_chat as GC
from workspaces import governance_map as GM

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def test_policy_routes_to_ingest():
    r = GC.chat("Automated decisions must be reviewed by a compliance officer.")
    assert r["intent"] == "policy" and r["kind"] == "twin"
    assert r["result"]["ok"] and r["result"]["patch"] is not None
    assert r["echo"] == "inferred: policy"


def test_self_description_routes_to_intake():
    r = GC.chat("I'm the deployer and our system screens job applicants")
    assert r["intent"] == "intake" and r["kind"] == "card"
    assert "screens job applicants" in r["result"]["description"]
    assert r["result"]["domain"] == "neutral" and "completeness" in r["result"]
    assert "unknown_facets" in r["result"]         # what to narrow next


def test_question_routes_to_ask():
    r = GC.chat("which rules need a human?",
                policy_text="Providers of high-risk AI systems shall establish a risk management system.")
    assert r["intent"] == "ask" and r["kind"] == "map"
    assert r["result"]["version"] == GM.SCHEMA_VERSION
    assert r["result"]["question"] == "which rules need a human?"


def test_explicit_intent_overrides_router():
    # a question-shaped input the user insists is a policy
    r = GC.chat("shall this be reviewed?", intent="policy")
    assert r["intent"] == "policy" and r["kind"] == "twin"
    assert r["echo"] == "you chose: policy"

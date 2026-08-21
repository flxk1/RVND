# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""One input routes to ask, intake, or policy.

The tests cover deterministic routing, ambiguous input defaults, optional
model proposals, and the echoed classification payload.
"""
from __future__ import annotations

import os

from rvnd import intent_router as IR

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def test_question_is_ask():
    for q in ("which rules need a human?", "show provider rules", "what applies to me?"):
        r = IR.route(q)
        assert r["intent"] == "ask" and r["dispatch"] == "governance_ask"


def test_self_description_is_intake():
    r = IR.route("I'm the deployer and our system screens job applicants")
    assert r["intent"] == "intake" and r["dispatch"] == "subject_card"
    assert IR.route("We deploy an AI tool for hiring")["intent"] == "intake"


def test_policy_document_is_ingest():
    assert IR.route("Providers of high-risk AI systems shall establish a risk management system.")["intent"] == "policy"
    long_doc = ("Article 9\nA risk management system shall be established. "
                "Deployers must ensure human oversight. Providers shall draw up documentation.")
    assert IR.route(long_doc)["intent"] == "policy"


def test_ambiguous_defaults_to_ask():
    r = IR.route("biometrics")
    assert r["intent"] == "ask"                        # read-only default — never writes on a guess
    # an LLM proposer can resolve the middle, but only into a known intent
    r2 = IR.route("hmm", llm=lambda t, ctx: {"intent": "intake"})
    assert r2["intent"] == "intake" and r2["why"].startswith("llm-proposed")
    assert IR.route("hmm", llm=lambda t, ctx: {"intent": "nonsense"})["intent"] == "ask"


def test_classifies_and_echoes_only():
    r = IR.route("which cards are empty?")
    assert set(r.keys()) == {"intent", "dispatch", "text", "echo", "why"}
    assert r["echo"] == "inferred: ask" and r["text"] == "which cards are empty?"

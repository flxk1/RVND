# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The big orchestration test — local-first cascade, governed escalation, and
the agent-token economics.

Three modes, all real code, no mocked Python LLM functions:

  HERMETIC (always, incl. CI/sandbox): spins up real in-process HTTP servers
    that speak the OpenAI /v1/chat/completions wire protocol, so the genuine
    `local_llm.complete_via` transport, the cascade orchestration, the Shield
    gate, the verifier-deferral logic, and the economics ledger ALL execute
    against real sockets. Determinism comes from scripted server replies, not
    from stubbing the client.

  LOCAL-ENDPOINT (opt-in): set WORKSPACE_TEST_LOCAL_URL (+ _MODEL) to your LM
    Studio / llama.cpp server — the same tests run against the real model.

  CLOUD-ENDPOINT (opt-in): set WORKSPACE_TEST_CLOUD_URL + _MODEL + _KEY to exercise
    a real local→cloud escalation end to end.

Run: pytest tests/test_cascade_orchestration.py -v
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from workspaces import local_llm
from workspaces.cascade import (Tier, run_cascade, nonempty_verifier)


# ── a real in-process OpenAI-compatible server ────────────────────────────────

class _FakeOpenAI:
    """A real HTTP server speaking /v1/chat/completions. Scripted: each call
    pops the next reply from ``replies`` (text or the sentinel '__500__' to
    force an HTTP 500, '__garbage__' for a malformed body). Records every
    request it receives so the test can prove what was actually sent."""

    def __init__(self, replies, *, model="fake-model", usage_tokens=40):
        self.replies = list(replies)
        self.model = model
        self.usage_tokens = usage_tokens
        self.requests: list[dict] = []
        self._srv = HTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self._srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    def _handler(self):
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):       # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({
                    "auth": self.headers.get("Authorization", ""),
                    "capability": self.headers.get("X-Rvnd-Capability", ""),
                    "track": self.headers.get("X-Lock-Track", ""),
                    "upstream": self.headers.get("X-Lock-Upstream", ""),
                    "model": body.get("model"),
                    "prompt": body["messages"][-1]["content"],
                })
                reply = server.replies.pop(0) if server.replies else ""
                if reply == "__500__":
                    self.send_response(500); self.end_headers()
                    self.wfile.write(b'{"error":"boom"}'); return
                if reply == "__garbage__":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(b'{"not":"an openai shape"}'); return
                payload = {
                    "model": server.model,
                    "choices": [{"message": {"role": "assistant", "content": reply}}],
                    "usage": {"prompt_tokens": server.usage_tokens // 2,
                              "completion_tokens": server.usage_tokens // 2,
                              "total_tokens": server.usage_tokens},
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        return H

    def stop(self):
        self._srv.shutdown()


@pytest.fixture()
def local_server():
    s = _FakeOpenAI(["LOCAL ANSWER"], model="phi-3.5-mini")
    yield s
    s.stop()


@pytest.fixture()
def cloud_server():
    s = _FakeOpenAI(["CLOUD ANSWER"], model="big-cloud", usage_tokens=400)
    yield s
    s.stop()


# A shield that lets everything through, so cloud-path tests don't depend on
# workspaces.lock being installed; the Shield-specific tests exercise the real gate.
def _allow_shield(prompt):
    return "allow", prompt


def _cloud_tier(server, *, price=3.0):
    return Tier(
        "cloud",
        server.url,
        "big-cloud",
        is_cloud=True,
        price_per_1k=price,
        proxy_url=server.url,
        capability_token="RVSC1.test.signature",
        track_id="cloud-primary",
    )


# ── 1. the real transport works against a real socket ─────────────────────────

def test_transport_speaks_real_openai_protocol(local_server):
    out = local_llm.complete_via(local_server.url, "phi-3.5-mini", "ping",
                                 api_key="", max_tokens=32)
    assert out["ok"] and out["response"] == "LOCAL ANSWER"
    assert out["usage"]["total_tokens"] == 40
    # the server actually received our request, our model id, our prompt:
    assert local_server.requests[-1]["model"] == "phi-3.5-mini"
    assert local_server.requests[-1]["prompt"] == "ping"


# ── 2. standard task handled locally — cloud never touched (the savings claim) ─

def test_local_accept_never_calls_cloud(local_server, cloud_server):
    tiers = [
        Tier("local", local_server.url, "phi-3.5-mini", price_per_1k=0.0),
        _cloud_tier(cloud_server),
    ]
    r = run_cascade("classify this", tiers, verifier=nonempty_verifier,
                    shield=_allow_shield)
    assert r.ok and r.served_by == "local" and not r.served_is_cloud
    # The cloud server received ZERO requests — the whole point.
    assert cloud_server.requests == []
    led = r.ledger()
    assert led["accepted_locally"] is True
    assert led["escalated_to_cloud"] is False
    assert led["agent_tokens_offloaded_to_local"] == 40


# ── 3. local defers (verifier fails) → governed escalation to cloud ───────────

def test_local_defer_escalates_to_cloud(cloud_server):
    local = _FakeOpenAI([""], model="phi-3.5-mini")          # empty → verifier defers
    try:
        tiers = [
            Tier("local", local.url, "phi-3.5-mini", price_per_1k=0.0),
            _cloud_tier(cloud_server),
        ]
        r = run_cascade("hard task", tiers, verifier=nonempty_verifier,
                        shield=_allow_shield)
        assert r.ok and r.served_by == "cloud" and r.served_is_cloud
        assert cloud_server.requests[-1]["prompt"] == "hard task"
        assert cloud_server.requests[-1]["auth"] == ""
        assert cloud_server.requests[-1]["capability"] == "RVSC1.test.signature"
        assert cloud_server.requests[-1]["track"] == "cloud-primary"
        assert cloud_server.requests[-1]["upstream"] == "127.0.0.1"
        led = r.ledger()
        assert led["local_deferrals"] == 1
        assert led["escalated_to_cloud"] is True
        assert led["accepted_locally"] is False
    finally:
        local.stop()


# ── 4. ordered local fallback: n1 defers, n2 accepts, cloud untouched ─────────

def test_ordered_local_fallback_before_cloud(cloud_server):
    n1 = _FakeOpenAI([""], model="tiny")                     # defers
    n2 = _FakeOpenAI(["N2 ANSWER"], model="phi-3.5-mini")    # accepts
    try:
        tiers = [
            Tier("local-n1", n1.url, "tiny", price_per_1k=0.0),
            Tier("local-n2", n2.url, "phi-3.5-mini", price_per_1k=0.0),
            _cloud_tier(cloud_server),
        ]
        r = run_cascade("task", tiers, verifier=nonempty_verifier,
                        shield=_allow_shield)
        assert r.served_by == "local-n2" and not r.served_is_cloud
        assert cloud_server.requests == []                  # never reached cloud
        assert [a.tier for a in r.attempts if a.ran] == ["local-n1", "local-n2"]
    finally:
        n1.stop(); n2.stop()


# ── 5. Shield governs the cloud hop ──────────────────────────────────────────

def test_shield_refusal_blocks_cloud(cloud_server):
    local = _FakeOpenAI([""], model="phi-3.5-mini")          # defers
    def refusing_shield(prompt):
        return "refuse", ""
    try:
        tiers = [
            Tier("local", local.url, "phi-3.5-mini", price_per_1k=0.0),
            _cloud_tier(cloud_server),
        ]
        r = run_cascade("contains a secret", tiers, verifier=nonempty_verifier,
                        shield=refusing_shield)
        assert cloud_server.requests == []                  # blocked before egress
        assert r.escalation_withheld is True
        blocked = [a for a in r.attempts if a.tier == "cloud"]
        assert blocked and not blocked[0].ran and "shield refused" in blocked[0].reason
    finally:
        local.stop()


def test_shield_minimise_sends_redacted_text(cloud_server):
    local = _FakeOpenAI([""], model="phi-3.5-mini")
    def minimising_shield(prompt):
        return "minimise", "REDACTED prompt"
    try:
        tiers = [
            Tier("local", local.url, "phi-3.5-mini", price_per_1k=0.0),
            _cloud_tier(cloud_server),
        ]
        r = run_cascade("my IBAN is DE.. and name is X", tiers,
                        shield=minimising_shield)
        assert r.served_is_cloud
        assert cloud_server.requests[-1]["prompt"] == "REDACTED prompt"  # not the raw PII
    finally:
        local.stop()


def test_shield_unavailable_fails_closed(cloud_server):
    local = _FakeOpenAI([""], model="phi-3.5-mini")
    def broken_shield(prompt):
        return "unavailable", ""
    try:
        tiers = [Tier("local", local.url, "phi-3.5-mini"),
                 _cloud_tier(cloud_server, price=0.0)]
        r = run_cascade("task", tiers, shield=broken_shield)
        assert cloud_server.requests == []                  # fail-closed: no hop
        assert r.escalation_withheld is True
        # explicit override re-enables it
        r2 = run_cascade("task", tiers, shield=broken_shield,
                         allow_unscreened_cloud=True)
        assert r2.served_is_cloud and cloud_server.requests
    finally:
        local.stop()


# ── 6. no cloud key → escalation withheld, best local returned, never faked ───

def test_no_cloud_key_withholds_escalation():
    local = _FakeOpenAI([""], model="phi-3.5-mini")          # defers (empty)
    try:
        tiers = [
            Tier("local", local.url, "phi-3.5-mini", price_per_1k=0.0),
            Tier("cloud", "http://unused", "big-cloud", api_key="",  # no key
                 is_cloud=True, price_per_1k=3.0),
        ]
        r = run_cascade("task", tiers, shield=_allow_shield)
        assert r.escalation_withheld is True
        assert r.served_by == "local"        # best local attempt surfaced
        assert "escalate manually" in r.error
        cloud = [a for a in r.attempts if a.tier == "cloud"]
        assert cloud and not cloud[0].ran and cloud[0].reason == "not configured"
    finally:
        local.stop()


# ── 7. transport failure on a tier → cascade continues, records it ────────────

def test_tier_http_error_is_recorded_and_skipped(cloud_server):
    local = _FakeOpenAI(["__500__"], model="phi-3.5-mini")   # 500s
    try:
        tiers = [
            Tier("local", local.url, "phi-3.5-mini", price_per_1k=0.0),
            _cloud_tier(cloud_server),
        ]
        r = run_cascade("task", tiers, shield=_allow_shield)
        assert r.served_by == "cloud"        # local errored → escalated
        loc = [a for a in r.attempts if a.tier == "local"][0]
        assert loc.ran and not loc.accepted and "HTTP 500" in loc.reason
    finally:
        local.stop()


# ── 8. the economics ledger: savings reported WITH the quality signal ─────────

def test_ledger_quantifies_offload_and_keeps_quality_signal(local_server, cloud_server):
    tiers = [
        Tier("local", local_server.url, "phi-3.5-mini", price_per_1k=0.0),
        _cloud_tier(cloud_server),
    ]
    r = run_cascade("standard task", tiers, shield=_allow_shield)
    led = r.ledger()
    # served locally at zero cost; counterfactual = same answer at cloud rate.
    assert led["actual_cost"] == 0.0
    assert led["cloud_only_cost_estimate"] > 0.0
    assert led["estimated_saving"] == led["cloud_only_cost_estimate"]
    # the saving never appears without the quality counters beside it:
    for k in ("tiers_run", "local_deferrals", "escalated_to_cloud",
              "accepted_locally"):
        assert k in led


def test_deferral_frontier_over_a_labelled_set():
    """The university-grade artifact: run a mixed batch, measure the deferral
    rate and the verifier's false-accept rate on the same set — savings is
    meaningless without this frontier."""
    # 10 tasks: 7 the local model gets right, 3 it gets wrong (empty → defers).
    # The verifier here = "answer must equal the gold label" (a real checkable
    # invariant), so a wrong-but-nonempty local answer is a FALSE ACCEPT.
    gold = [f"ANS{i}" for i in range(10)]
    local_replies = [f"ANS{i}" for i in range(7)] + ["WRONG", "", ""]  # 7 correct, 1 wrong, 2 defer
    served_local = served_cloud = false_accept = 0
    for i in range(10):
        loc = _FakeOpenAI([local_replies[i]], model="phi-3.5-mini")
        cloud = _FakeOpenAI([gold[i]], model="big-cloud", usage_tokens=400)
        try:
            def gold_verifier(prompt, response, _g=gold[i]):
                ok = response.strip() == _g
                return ok, ("matches gold" if ok else "≠ gold"), (1.0 if ok else 0.0)
            tiers = [Tier("local", loc.url, "phi-3.5-mini", price_per_1k=0.0),
                     _cloud_tier(cloud)]
            r = run_cascade(f"task {i}", tiers, verifier=gold_verifier,
                            shield=_allow_shield)
            if r.served_is_cloud:
                served_cloud += 1
            else:
                served_local += 1
                if r.response.strip() != gold[i]:
                    false_accept += 1
        finally:
            loc.stop(); cloud.stop()
    # 7 correct local accepts; the 1 "WRONG" is caught by the gold verifier and
    # escalated (so it does NOT count as served-local), 2 empties escalate too →
    # 7 served local (all correct), 3 served cloud, false-accept = 0.
    assert served_local == 7 and served_cloud == 3
    assert false_accept == 0          # verifier-gating prevented quality leak
    # offload rate = local share of served tasks:
    assert served_local / 10 == 0.7


# ── real-endpoint modes (opt-in; auto-skip without env) ───────────────────────

@pytest.mark.skipif(not os.getenv("WORKSPACE_TEST_LOCAL_URL"),
                    reason="set WORKSPACE_TEST_LOCAL_URL + _MODEL for a real local model")
def test_real_local_endpoint():
    url = os.environ["WORKSPACE_TEST_LOCAL_URL"]
    model = os.environ.get("WORKSPACE_TEST_LOCAL_MODEL", "")
    out = local_llm.complete_via(url, model, "Reply with the single word OK.",
                                 max_tokens=8, timeout=60)
    assert out["ok"], out.get("error")
    assert out["response"].strip()


@pytest.mark.skipif(not (os.getenv("WORKSPACE_TEST_LOCAL_URL") and os.getenv("WORKSPACE_TEST_CLOUD_URL")),
                    reason="set WORKSPACE_TEST_LOCAL_URL and WORKSPACE_TEST_CLOUD_URL + _KEY for a real cascade")
def test_real_local_to_cloud_cascade():
    tiers = [
        Tier("local", os.environ["WORKSPACE_TEST_LOCAL_URL"],
             os.environ.get("WORKSPACE_TEST_LOCAL_MODEL", ""), price_per_1k=0.0),
        Tier("cloud", os.environ["WORKSPACE_TEST_CLOUD_URL"],
             os.environ.get("WORKSPACE_TEST_CLOUD_MODEL", ""),
             api_key=os.environ.get("WORKSPACE_TEST_CLOUD_KEY", ""),
             is_cloud=True, price_per_1k=3.0),
    ]
    # a verifier the small model can plausibly satisfy → should serve locally
    r = run_cascade("Reply with exactly: OK", tiers,
                    verifier=nonempty_verifier, shield=_allow_shield, max_tokens=16)
    assert r.ok
    print("served_by:", r.served_by, "| ledger:", r.ledger())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier-C semantic check must fail closed when a real backend is
configured but unavailable/erroring, instead of silently degrading to
regex-only/allow. Mock (the effective default) stays permissive. Lock/Shield +
local-LLM panels."""
from __future__ import annotations

import pytest

from workspaces.lock import tier_c as TC
from workspaces.lock.backends import BackendError
from workspaces.lock.core import lock_text, Mode


ENV = "AGENT_TOOL_LOCK_LLM_BACKEND"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Each test starts from the documented default (mock) with a clear cache."""
    monkeypatch.delenv(ENV, raising=False)
    TC.reset_backend_cache()
    yield
    TC.reset_backend_cache()


class _Unavail:
    def is_available(self): return False
    def classify(self, text, context=""): return {"contains_pii": False}
    def describe(self): return "unavailable test backend"


class _Boom:
    def is_available(self): return True
    def classify(self, text, context=""): raise RuntimeError("model crashed")
    def describe(self): return "exploding test backend"


# ── effective-mock = permissive (no regression in the onboarding default) ─────

def test_mock_is_the_effective_default_and_permissive():
    assert TC.tier_c_requires_real_backend() is False
    findings = TC.tier_c_check_semantic("some arbitrary text")
    assert all(f.type != "tier_c_unavailable" for f in findings)


def test_mock_effective_classify_crash_stays_permissive(monkeypatch):
    # mock is the explicit opt-in to "no semantic check": even a crash in the
    # (mock) backend must NOT fail closed — that is the documented default.
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Boom())
    assert TC.tier_c_requires_real_backend() is False     # env unset → mock
    assert TC.tier_c_check_semantic("hello") == []


# ── real backend configured but cannot run → FAIL CLOSED ─────────────────────

def test_real_backend_construct_failure_fails_closed(monkeypatch):
    monkeypatch.setenv(ENV, "llama_cpp:/no/such/model.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm",
                        lambda spec: (_ for _ in ()).throw(BackendError("cannot load")))
    assert TC.tier_c_requires_real_backend() is True
    findings = TC.tier_c_check_semantic("hello")
    assert len(findings) == 1
    assert findings[0].type == "tier_c_unavailable"
    assert findings[0].severity == "high"


def test_real_backend_unavailable_fails_closed(monkeypatch):
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Unavail())
    findings = TC.tier_c_check_semantic("hello")
    assert len(findings) == 1
    assert findings[0].type == "tier_c_unavailable"
    assert findings[0].severity == "high"


def test_real_backend_classify_raises_fails_closed(monkeypatch):
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Boom())
    findings = TC.tier_c_check_semantic("hello")
    assert len(findings) == 1
    assert findings[0].type == "tier_c_unavailable"
    assert findings[0].severity == "high"


# ── end-to-end through lock_text: unavailable real Tier-C ⇒ refuse ───────────

def test_lock_text_refuses_when_real_tier_c_unavailable(monkeypatch):
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Unavail())
    # Benign text with no Tier-B regex hits: previously this would ALLOW (Tier C
    # returned []). Now the unavailable real backend forces a refuse.
    dec = lock_text("just some perfectly benign words", mode=Mode.STANDARD)
    assert dec.action == "refuse"


def test_lock_text_allows_benign_under_mock_default():
    # No regression: with the mock default, benign text is still allowed.
    dec = lock_text("just some perfectly benign words", mode=Mode.STANDARD)
    assert dec.action == "allow"


def test_lock_text_failclosed_on_tier_c_import_crash(monkeypatch):
    # If the Tier-C layer itself crashes (not just the backend) AND a real
    # backend is configured, lock_text must still fail closed.
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    def _crash(*a, **k):
        raise ImportError("tier_c layer broken")
    monkeypatch.setattr(TC, "tier_c_check_semantic", _crash)
    dec = lock_text("benign words", mode=Mode.STANDARD)
    assert dec.action == "refuse"


# ── malformed real-backend result fails closed (not silent allow) ────────────

class _MalformedDict:
    def is_available(self): return True
    def classify(self, text, context=""): return {}           # no contains_pii verdict
    def describe(self): return "malformed-dict backend"


class _NonDict:
    def is_available(self): return True
    def classify(self, text, context=""): return "not a dict"
    def describe(self): return "non-dict backend"


@pytest.mark.parametrize("backend_cls", [_MalformedDict, _NonDict])
def test_real_backend_malformed_result_fails_closed(monkeypatch, backend_cls):
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: backend_cls())
    findings = TC.tier_c_check_semantic("hello")
    assert len(findings) == 1
    assert findings[0].type == "tier_c_unavailable"


def test_real_backend_explicit_clean_verdict_allows(monkeypatch):
    # An EXPLICIT contains_pii=False is a real "clean" answer → no finding.
    class _Clean:
        def is_available(self): return True
        def classify(self, text, context=""): return {"contains_pii": False}
        def describe(self): return "clean backend"
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Clean())
    assert TC.tier_c_check_semantic("hello") == []


# ── detail must NOT leak the exception message (only the class name) ──────────

def test_unavailable_finding_detail_carries_no_exception_message(monkeypatch):
    secret = "/secret/path/proprietary-model.gguf and JohnSmith\x40example.com"
    class _Leaky:
        def is_available(self): return True
        def classify(self, text, context=""): raise RuntimeError(secret)
        def describe(self): return "leaky backend"
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Leaky())
    findings = TC.tier_c_check_semantic("hello")
    assert len(findings) == 1
    assert secret not in findings[0].detail
    assert "RuntimeError" in findings[0].detail        # class name is fine


# ── PERMISSIVE / AUDIT_ONLY never block, even on a fail-closed finding ────────

@pytest.mark.parametrize("mode", [Mode.PERMISSIVE, Mode.AUDIT_ONLY])
def test_nonblocking_modes_still_allow_when_real_tier_c_unavailable(monkeypatch, mode):
    # By design PERMISSIVE/AUDIT_ONLY record findings but never block. A
    # tier_c_unavailable finding must NOT turn them into a refuse — this pins
    # that contract so a future _decide_text change can't silently regress it.
    monkeypatch.setenv(ENV, "llama_cpp:/x.gguf")
    TC.reset_backend_cache()
    monkeypatch.setattr(TC, "make_local_llm", lambda spec: _Unavail())
    dec = lock_text("benign words", mode=mode)
    assert dec.action == "allow"

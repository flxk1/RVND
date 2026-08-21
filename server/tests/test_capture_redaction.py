# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""D1 — secrets/PII are redacted BEFORE they reach the capture ledger or the
signed audit chain (six-panel audit, API/OAuth + Lock/Shield).

Claims:
  R1  redact_for_capture strips URL creds, Bearer tokens, API-key prefixes, and
      the PII the egress minimiser already covers.
  R2  a captured exchange with an embedded API key never persists the key — not
      in the always-on summary (METADATA floor), the body, or the prompt_context
      facet (FULL).
  R3  tool-call trace string values are redacted too.
  R4  the cascade audit pair_id does not embed the raw prompt prefix.
"""
from __future__ import annotations

import os

import pytest

from rvnd.lock.core import redact_for_capture
from rvnd.llm_capture import (
    LLMExchange,
    _project_pair,
    VerbosityLevel,
)

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

SECRET = "sk-" "proj-ABCDEFGHIJKLMNOP1234567890"
BEARER = "Bearer abcDEF123456ghiJKL789mno"
URLCRED = "https://user:hunter2\x40db.internal/x"


# ── R1: the redactor covers credentials + PII ───────────────────────────────
@pytest.mark.parametrize("payload,leak", [
    (f"use {SECRET} now", "sk-" "proj-ABCDEFGHIJKLMNOP"),
    (f"auth: {BEARER}", "abcDEF123456ghiJKL789mno"),
    (f"dsn {URLCRED}", "hunter2"),
    ("mail me at alice\x40example.com", "alice\x40example.com"),
])
def test_redact_for_capture_strips(payload, leak):
    out = redact_for_capture(payload)
    assert leak not in out
    assert "REDACTED" in out


def test_redact_for_capture_keeps_benign():
    assert redact_for_capture("what is 2+2?") == "what is 2+2?"


@pytest.mark.parametrize("label", ["PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY"])
def test_complete_pem_private_key_is_redacted(label):
    payload = f"before -----BEGIN {label}-----\nsecret-body\n-----END {label}----- after"
    out = redact_for_capture(payload)
    assert "secret-body" not in out
    assert out == "before [REDACTED-PRIVATE-KEY] after"


def test_many_unclosed_pem_markers_complete_without_consuming_text():
    payload = ("-----BEGIN PRIVATE KEY-----" * 20_000) + "tail"
    assert redact_for_capture(payload).endswith("tail")


# ── R2 + R3: nothing leaks into the projected pair at any verbosity ─────────
def _pair(verbosity):
    ex = LLMExchange(
        model="m",
        prompt_context=f"system key {SECRET}\n{URLCRED}",
        response=f"ok, {BEARER}",
        tool_call_trace=[{"name": "fetch", "args": {"token": SECRET}}],
    )
    return _project_pair(ex, verbosity, folder_context="/tmp/x")


@pytest.mark.parametrize("v", list(VerbosityLevel))
def test_no_secret_in_projected_pair(v):
    import json
    blob = json.dumps(_pair(v))
    assert SECRET not in blob
    assert "hunter2" not in blob
    assert "abcDEF123456ghiJKL789mno" not in blob


def test_summary_redacted_even_at_metadata():
    p = _pair(VerbosityLevel.METADATA)
    assert "sk-proj" not in p["problem"]["summary"]


def test_trace_redacted_at_full_plus_trace():
    import json
    p = _pair(VerbosityLevel.FULL_PLUS_TRACE)
    assert SECRET not in json.dumps(p["solution"].get("tool_call_trace", []))


# ── cited_sources (credentialed URL) is redacted ────────────────────────────
def test_cited_sources_redacted():
    import json
    ex = LLMExchange(model="m", prompt_context="q", response="a",
                     cited_sources=["https://user:hunter2@h/x", f"https://api/x?api_key={SECRET}"])
    p = _project_pair(ex, VerbosityLevel.FULL, folder_context="/tmp/x")
    blob = json.dumps(p)
    assert "hunter2" not in blob and SECRET not in blob


# ── no-collapse: two exchanges differing only in a secret keep DISTINCT ids ──
def test_redacted_hashing_does_not_collapse_distinct_exchanges():
    a = _project_pair(LLMExchange(model="m", prompt_context=f"key sk-AAA{('1'*20)}", response="r"),
                      VerbosityLevel.FULL, folder_context="/tmp/x")
    b = _project_pair(LLMExchange(model="m", prompt_context=f"key sk-BBB{('2'*20)}", response="r"),
                      VerbosityLevel.FULL, folder_context="/tmp/x")
    assert a["id"] != b["id"]
    assert a["problem"]["id"] != b["problem"]["id"]


# ── web-search capture redacts query + results ──────────────────────────────
@pytest.mark.parametrize("v", list(VerbosityLevel))
def test_web_capture_redacts(v):
    import json
    from rvnd.web_capture import WebSearchExchange, WebSearchResult, _project_pair as web_pair
    ex = WebSearchExchange(
        query=f"use {SECRET} with stripe",
        engine="ddg",
        results=[WebSearchResult(url=URLCRED, title=f"tok {BEARER}",
                                 snippet=f"snippet {SECRET}", full_text=f"body {SECRET}", rank=1)],
    )
    blob = json.dumps(web_pair(ex, v, folder_context="/tmp/x"))
    assert SECRET not in blob and "hunter2" not in blob and "abcDEF123456ghiJKL789mno" not in blob


# ── extra credential classes ────────────────────────────────────────────────
@pytest.mark.parametrize("payload,leak", [
    ("token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc123", "eyJzdWIiOiIxIn0"),
    ("pay 4111111111111111 now", "4111111111111111"),
    ("password = supersecret123", "supersecret123"),
    ("AUTH: bearer lowercaseTokenABCDEF1234", "lowercaseTokenABCDEF1234"),
])
def test_extra_credential_classes(payload, leak):
    assert leak not in redact_for_capture(payload)


# ── WorkspaceMemory.web_capture (sibling path) redacts before the chain ──────────
def test_workspacememory_web_capture_redacts(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd.memory import WorkspaceMemory
    ws = tmp_path / "org"; ws.mkdir()
    mem = WorkspaceMemory(str(ws), log_root=str(tmp_path / "logs"), actor="t")
    mem.web_capture(f"find {SECRET}", [{"url": URLCRED, "title": f"t {BEARER}",
                                        "snippet": f"snip {SECRET}", "full_text": f"body {SECRET}"}])
    import json
    blob = json.dumps([p for p in mem.all_pairs()])
    assert SECRET not in blob and "hunter2" not in blob and "abcDEF123456ghiJKL789mno" not in blob


def test_workspacememory_llm_capture_redacts(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd.memory import WorkspaceMemory
    ws = tmp_path / "org"; ws.mkdir()
    mem = WorkspaceMemory(str(ws), log_root=str(tmp_path / "logs"), actor="t")
    mem.llm_capture(f"key {SECRET}", f"ok {BEARER}", model="m", cited_sources=[URLCRED])
    import json
    blob = json.dumps([p for p in mem.all_pairs()])
    assert SECRET not in blob and "hunter2" not in blob and "abcDEF123456ghiJKL789mno" not in blob


# ── quoted secret values (JSON/YAML/.env) are redacted ──────────────────────
@pytest.mark.parametrize("payload,leak", [
    ('password = "supersecret123"', "supersecret123"),
    ("{\"token\": \"sk-aaaaaaaaaaaa\"}", "sk-aaaaaaaaaaaa"),
    ("api_key: 'longsecretvalue42'", "longsecretvalue42"),
])
def test_quoted_secret_assignment_redacted(payload, leak):
    assert leak not in redact_for_capture(payload)


# ── R4: cascade audit pair_id is a hash, not the raw prompt ─────────────────
def test_cascade_pair_id_does_not_embed_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd.workspace_cascade import cascade_for_workspace
    ws = tmp_path / "org"; ws.mkdir()
    secret_prompt = f"please use {SECRET} to authenticate"
    # no cloud/local tier configured → served locally/refused, but the audit
    # event is still written; we only assert the pair_id shape.
    cascade_for_workspace(str(ws), secret_prompt, log_root=str(tmp_path / "logs"))
    from rvnd.mutation_log import MutationLog
    log = MutationLog(str(ws), log_root=str(tmp_path / "logs"))
    for ev in log.replay():
        pid = ev.pair_id if hasattr(ev, "pair_id") else (ev.get("pair_id", "") if isinstance(ev, dict) else "")
        if pid.startswith("cascade:"):
            assert SECRET not in pid and "sk-proj" not in pid
            assert len(pid) == len("cascade:") + 16  # sha256[:16]

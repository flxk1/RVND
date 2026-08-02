# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""D2 — complete_via must never send an API key over a plaintext wire."""
from __future__ import annotations

import pytest

from workspaces.local_llm import _is_secure_or_loopback, complete_via


@pytest.mark.parametrize("url,ok", [
    ("https://api.anthropic.com", True),
    ("https://api.openai.com/v1", True),
    ("http://api.example.com", False),           # public http → not secure
    ("http://evil.example.com:8080", False),
    ("http://127.0.0.1:8080", True),             # loopback → never leaves machine
    ("http://localhost:1234", True),
    ("http://[::1]:1234", True),
    ("ftp://x", False),
])
def test_is_secure_or_loopback(url, ok):
    assert _is_secure_or_loopback(url) is ok


def test_complete_via_refuses_key_over_plain_http():
    # Public http endpoint + a key → refuse BEFORE any network call (no urlopen).
    r = complete_via("http://api.example.com", "gpt-x", "hi", api_key="sk-secret-123")
    assert r["ok"] is False
    assert "https" in r["error"].lower()
    # the key must not appear in the error payload
    assert "sk-secret-123" not in r["error"]


def test_complete_via_keyless_http_is_not_blocked_by_the_guard():
    # No key → the TLS guard does not apply (it only protects credentials).
    # (Will fail to connect, but NOT with the https-refusal error.)
    r = complete_via("http://127.0.0.1:59999", "m", "hi")
    assert r["ok"] is False
    assert "https" not in r["error"].lower()

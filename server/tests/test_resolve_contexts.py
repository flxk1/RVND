# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the axis-B context-resolution seam (``rvnd.hook.resolve_contexts``).

Today resolution is a no-op: every call, whatever tool it names and however
malformed its ``tool_input``, resolves to the singleton acting folder. These
tests pin that down so a future target-workspace resolver cannot silently
widen the singleton without a test noticing, and so the "never raises"
contract stays enforced.
"""
from __future__ import annotations

import pytest

from rvnd import hook as H


@pytest.mark.parametrize("tool_name,tool_input", [
    ("Bash", {"command": "ls -la"}),
    ("Read", {"file_path": "/some/file.py"}),
    ("Write", {"file_path": "/some/file.py", "content": "x"}),
    ("mcp__github__create_issue", {"title": "x"}),
])
def test_resolve_contexts_returns_singleton_cwd(tool_name, tool_input):
    cwd = "/workspace/project"
    assert H.resolve_contexts(cwd, tool_name, tool_input) == (cwd,)


@pytest.mark.parametrize("malformed_tool_input", [
    None,
    "not a dict",
    ["also", "not", "a", "dict"],
    123,
    {"nested": {"deeply": {"malformed": object()}}},
])
def test_resolve_contexts_never_raises_on_malformed_tool_input(malformed_tool_input):
    cwd = "/workspace/project"
    # Must not raise for ANY shape of tool_input, and must still answer (cwd,).
    assert H.resolve_contexts(cwd, "Bash", malformed_tool_input) == (cwd,)


def test_resolve_contexts_never_raises_on_malformed_tool_name():
    cwd = "/workspace/project"
    for bad_name in (None, 123, object()):
        assert H.resolve_contexts(cwd, bad_name, {}) == (cwd,)


def test_resolve_contexts_returns_a_tuple():
    result = H.resolve_contexts("/x", "Bash", {"command": "ls"})
    assert isinstance(result, tuple)
    assert len(result) == 1

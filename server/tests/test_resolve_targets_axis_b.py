# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for ``rvnd.context_resolve.resolve_targets`` — the axis-B target
reader for structured file-write tools.

Covers exactly ``Write``/``Edit``/``MultiEdit`` (``file_path``) and
``NotebookEdit`` (``notebook_path``); every other tool, and any malformed
input, must answer the empty tuple without raising.
"""
from __future__ import annotations

import os

import pytest

from rvnd import context_resolve as CR


# ── the four structured file-write tools ────────────────────────────────────
@pytest.mark.parametrize("tool_name,field", [
    ("Write", "file_path"),
    ("Edit", "file_path"),
    ("MultiEdit", "file_path"),
    ("NotebookEdit", "notebook_path"),
])
def test_resolve_targets_reads_the_right_field_for_each_write_tool(tool_name, field):
    cwd = "/workspace/project"
    target = "/workspace/project/sub/file.txt"
    result = CR.resolve_targets(cwd, tool_name, {field: target})
    assert result == (target,)


def test_resolve_targets_resolves_relative_path_against_cwd():
    cwd = "/workspace/project"
    result = CR.resolve_targets(cwd, "Write", {"file_path": "sub/file.txt"})
    assert result == ("/workspace/project/sub/file.txt",)


def test_resolve_targets_applies_expanduser():
    home = os.path.expanduser("~")
    result = CR.resolve_targets("/workspace/project", "Write",
                                {"file_path": "~/notes.txt"})
    assert result == (f"{home}/notes.txt",)


def test_resolve_targets_normalises_dot_segments():
    cwd = "/workspace/project"
    result = CR.resolve_targets(cwd, "Edit",
                                {"file_path": "/workspace/project/a/../b.txt"})
    assert result == ("/workspace/project/b.txt",)


def test_resolve_targets_multiedit_reads_file_path_not_edits_list():
    cwd = "/workspace/project"
    result = CR.resolve_targets(cwd, "MultiEdit", {
        "file_path": "/workspace/project/f.py",
        "edits": [{"old_string": "a", "new_string": "b"},
                  {"old_string": "c", "new_string": "d"}],
    })
    assert result == ("/workspace/project/f.py",)


# ── tools out of scope for this resolver ────────────────────────────────────
@pytest.mark.parametrize("tool_name,tool_input", [
    ("Bash", {"command": "cat /etc/passwd"}),
    ("Read", {"file_path": "/some/file.py"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Grep", {"pattern": "TODO"}),
    ("WebFetch", {"url": "https://example.com"}),
    ("WebSearch", {"query": "governance"}),
    ("mcp__github__create_issue", {"title": "x"}),
    ("mcp__some_server__some_tool", {"file_path": "/should/not/match"}),
    ("SomeUnknownTool", {"file_path": "/also/should/not/match"}),
])
def test_resolve_targets_returns_empty_for_non_write_tools(tool_name, tool_input):
    assert CR.resolve_targets("/workspace/project", tool_name, tool_input) == ()


# ── malformed input never raises ────────────────────────────────────────────
@pytest.mark.parametrize("malformed_tool_input", [
    None,
    "not a dict",
    ["also", "not", "a", "dict"],
    123,
    {},                                    # missing file_path
    {"file_path": None},
    {"file_path": 12345},
    {"file_path": ""},
    {"nested": {"deeply": {"malformed": object()}}},
])
def test_resolve_targets_never_raises_on_malformed_tool_input(malformed_tool_input):
    assert CR.resolve_targets("/workspace/project", "Write", malformed_tool_input) == ()


def test_resolve_targets_never_raises_on_malformed_tool_name():
    for bad_name in (None, 123, object(), ""):
        assert CR.resolve_targets("/workspace/project", bad_name,
                                  {"file_path": "/x"}) == ()


def test_resolve_targets_never_raises_on_malformed_cwd():
    for bad_cwd in (None, 123, object()):
        # A relative file_path forces the resolver to fall back on cwd; a
        # malformed cwd must not raise even then.
        result = CR.resolve_targets(bad_cwd, "Write", {"file_path": "rel.txt"})
        assert isinstance(result, tuple)


def test_resolve_targets_returns_a_tuple():
    result = CR.resolve_targets("/x", "Write", {"file_path": "/x/y.py"})
    assert isinstance(result, tuple)
    assert len(result) == 1

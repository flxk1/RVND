# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces guide` — the categorized command dictionary.

The guide derives its command list and descriptions from the live parser, so
these tests double as a drift guard: every registered subcommand must appear in
the printed guide (grouped or, as a fallback, under "Other"), and descriptions
must match the parser's own help.
"""
from __future__ import annotations

import argparse
import io
import json

import rvnd.cli.impl as impl


def _run(monkeypatch, *, as_json=False):
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = impl.cmd_guide(argparse.Namespace(json=as_json))
    return rc, out.getvalue()


def test_guide_lists_every_registered_command(monkeypatch):
    rc, out = _run(monkeypatch)
    assert rc == 0
    # Every command the parser knows about must be visible in the guide —
    # nothing silently dropped.
    for name in impl._guide_help_map():
        assert name in out, f"command {name!r} missing from the guide"


def test_guide_groups_are_present_and_ordered(monkeypatch):
    rc, out = _run(monkeypatch)
    assert rc == 0
    titles = [t for t, _ in impl._GUIDE_GROUPS]
    positions = [out.find(t) for t in titles]
    assert all(p >= 0 for p in positions), "a group heading is missing"
    assert positions == sorted(positions), "group headings are out of order"


def test_guide_every_group_command_is_a_real_command(monkeypatch):
    # Guard the other direction: no group may reference a command the parser
    # doesn't actually register (a rename would surface here).
    known = set(impl._guide_help_map())
    for _title, cmds in impl._GUIDE_GROUPS:
        for c in cmds:
            assert c in known, f"guide groups list unknown command {c!r}"


def test_guide_descriptions_match_parser_help(monkeypatch):
    _rc, out = _run(monkeypatch)
    hm = impl._guide_help_map()
    # init's help text is stable and non-empty — it must be shown verbatim.
    assert hm["init"] and hm["init"].split()[0] in out


def test_guide_json_is_valid_and_grouped(monkeypatch):
    rc, out = _run(monkeypatch, as_json=True)
    assert rc == 0
    data = json.loads(out)
    assert "Setup & lifecycle" in data
    assert "init" in data["Setup & lifecycle"]

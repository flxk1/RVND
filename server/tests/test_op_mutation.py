# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-op mutation flag for the console CLI: pure reads are cleared, everything
else fails closed to a write, an inline declaration overrides, and stamping a
help registry flags every op.

  python -m pytest server/tests/test_op_mutation.py -q
"""
from __future__ import annotations

from rvnd.op_mutation import is_read, mutates, stamp


def test_pure_reads_are_reads():
    for op in ("snapshot", "party_list", "audit_query", "console_snapshot",
               "governance_graph", "egress_board", "whoami", "verify",
               "budget_cap_get", "draft_load", "card.list", "track_strip"):
        assert is_read(op), op
        assert mutates(op) is False, op


def test_writes_fail_closed():
    # name-shaped-as-read writes, and genuinely unrecognised ops, both mutate
    for op in ("save", "restore", "draft_save", "card.save", "import", "adopt",
               "party_status", "set_oversight_level", "erase", "party_register",
               "tighten", "a_brand_new_unlisted_op"):
        assert mutates(op) is True, op


def test_inline_declaration_wins():
    # a facade may correct the fallback either way
    assert mutates("save", declared=False) is False
    assert mutates("snapshot", declared=True) is True


def test_stamp_flags_every_op():
    ops = [{"op": "snapshot"}, {"op": "party_status"}, {"op": "save"},
           {"op": "console_snapshot"}, {"op": "help"}]
    stamp(ops)
    by = {o["op"]: o["mutates"] for o in ops}
    assert by == {"snapshot": False, "party_status": True, "save": True,
                  "console_snapshot": False, "help": False}
    # an already-declared flag is respected, not overwritten
    pre = [{"op": "snapshot", "mutates": True}]
    stamp(pre)
    assert pre[0]["mutates"] is True

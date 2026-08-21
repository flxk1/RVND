# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-op mutation status for the console CLI.

Internal by design: consumed by the app bridge to stamp the help registry with
``mutates``; it has no operator surface of its own.

The chat is a CLI over every governance op. Before firing an op it must know
whether the op mutates state — a mutating op raises a confirm-card and is gated
and recorded; a pure read runs and renders. The help registry does not carry
this, and the client must never guess it from the op name (both directions
misclassify: `save`/`restore` read-shaped but write, `accountability`/`active`
write-shaped but read).

So the server declares it, fail-closed: an op mutates unless it is a known pure
projection. A read wrongly flagged only costs a confirm; a write wrongly cleared
would fire ungated — the asymmetry decides the default. A facade may override by
carrying ``mutates`` on its own help entry; that wins over this fallback, so the
allowlist is the initial curation and each op's true status is recorded here or
inline as it is confirmed.
"""
from __future__ import annotations

from typing import Any, Optional

# Leaf verbs that denote a pure projection (no chain event, no file, no state
# change). Matched against the op's last dotted/underscored segment and against
# the whole op name. Deliberately excludes save/restore/import/adopt/draft_save/
# card.save and every set_/add_/register_ verb — those write.
_READ_LEAVES = frozenset({
    "snapshot", "list", "show", "tail", "get", "form", "load", "query", "help",
    "catalogue", "ops", "whoami", "board", "describe", "verify", "coverage",
    "map", "kg", "graph", "search", "stats", "summary", "history", "peek",
    "inspect", "preview", "export", "read", "dossier", "tally",
    "explain", "trace", "count", "diff", "info",
})

# Explicit pure-projection ops whose name does not end in a read leaf.
_READ_OPS = frozenset({
    "console_snapshot", "egress_board", "governance_graph", "governance_map",
    "governance_kg", "audit_query", "approval_list", "connector_list",
    "party_list", "use_case_list", "get_event", "facts.form", "card.form",
    "card.list", "card.load", "draft_load", "threshold_get", "budget_cap_get",
    "status_get", "verify_bytes", "template_list", "model_capability",
    "security_dashboard", "track_strip", "matrix_coverage", "governance_live",
})

# Explicit writes that would otherwise match a read leaf (belt-and-braces).
_WRITE_OPS = frozenset({
    "save", "restore", "restore_bytes", "draft_save", "draft_discard",
    "card.save", "template_new", "adopt", "import", "write_file",
})


def _leaf(op: str) -> str:
    tail = (op or "").split(".")[-1]
    return tail.split("_")[-1].lower()


def is_read(op: str) -> bool:
    """True iff the op is a known pure projection (safe to run without a
    confirm-card). Fail-closed: anything unrecognised is not a read."""
    n = (op or "").lower()
    if n in _WRITE_OPS:
        return False
    if n in _READ_OPS or _leaf(op) in _READ_LEAVES or n in _READ_LEAVES:
        return True
    if n.startswith(("list_", "show_", "get_")) or n.endswith(("_list", "_show", "_get")):
        return True
    return False


def mutates(op: str, declared: Optional[bool] = None) -> bool:
    """Whether an op changes state — the confirm-card gate. A facade's own
    ``mutates`` on its help entry (``declared``) wins; otherwise fail-closed to
    True unless the op is a known pure projection."""
    if declared is not None:
        return bool(declared)
    return not is_read(op)


def stamp(ops: list[Any]) -> list[Any]:
    """Add ``mutates`` to each op entry of a help registry, respecting an
    already-declared value. ``help``/``catalogue``/``ops`` self-entries and
    string entries are stamped too, so the CLI never meets an unflagged op."""
    for o in ops:
        if isinstance(o, dict) and o.get("op"):
            o["mutates"] = mutates(o["op"], o.get("mutates"))
    return ops

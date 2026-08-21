# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace router — deterministic concept routing across workspaces ("cells-lite").

Answers "which workspace(s) should handle this?" by building a cheap concept
*signature* for each workspace from what it already holds (its label + its pairs'
summaries / facets / bodies) and scoring a query's tokens against it. No
embeddings, no model, no network — pure stdlib term overlap. The keyword/label
signature IS the fallback floor; an LLM/embedding ranker can layer on later
without changing this contract.

Wall-respecting: a workspace's pairs are read through :func:`rvnd.seal_binding.read_pairs`,
so a sealed+unlocked workspace is scored on its served (in-memory) content while the
disk stays ciphertext, and a sealed+locked workspace degrades to a **label-only**
signature (its name/path tokens) rather than leaking or erroring. The result
flags which workspaces were label-only so the caller knows the ranking was partial.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import seal_binding

# A small, fixed stopword set — deterministic, no NLTK/sklearn dependency.
_STOP = frozenset((
    "the a an and or of to in on for with by from at as is are be this that "
    "it its into over under about across per via your you our we they them "
    "what which when where who how why not no yes can may will shall do does"
).split())


def _tokens(text: Any) -> list[str]:
    """Lowercase alphanumeric tokens, length >= 3, stopwords removed."""
    if not text:
        return []
    return [t for t in re.split(r"[^a-z0-9]+", str(text).lower())
            if len(t) >= 3 and t not in _STOP]


def _pair_tokens(pair: dict) -> Iterable[str]:
    prob = pair.get("problem") or {}
    sol = pair.get("solution") or {}
    yield from _tokens(prob.get("summary"))
    yield from _tokens(prob.get("type"))
    facets = prob.get("facets") or {}
    if isinstance(facets, dict):
        for v in facets.values():
            yield from _tokens(v)
    for tag in (prob.get("tags") or []):
        yield from _tokens(tag)
    body = sol.get("body")
    if isinstance(body, str):
        yield from _tokens(body[:2000])  # cap: cheap + bounded


def signature_for_workspace(
    folder: str | Path, *, log_root: str | Path | None = None, label: str = "",
) -> tuple[Counter, bool]:
    """Return ``(token_counter, label_only)`` for a workspace.

    ``label_only`` is True when the workspace's content could not be read (sealed +
    locked, or error) and only its name/label tokens contribute.
    """
    sig: Counter = Counter()
    sig.update(_tokens(label or Path(folder).name))
    label_only = False
    try:
        pairs = seal_binding.read_pairs(folder, log_root=log_root)
        for p in pairs.values():
            sig.update(_pair_tokens(p))
    except Exception:
        label_only = True  # sealed+locked or unreadable → name-only signature
    return sig, label_only


def route(
    query: str,
    folders: Iterable[str | Path],
    *,
    log_root: str | Path | None = None,
    labels: dict | None = None,
    limit: int = 5,
) -> list[dict]:
    """Rank ``folders`` by concept overlap with ``query``.

    Score = sum of query-token frequencies present in the workspace's signature;
    tie-broken by the count of distinct query tokens matched. Workspaces with zero
    overlap are dropped. Deterministic.
    """
    q = _tokens(query)
    q_set = set(q)
    labels = labels or {}
    scored: list[dict] = []
    for f in folders:
        f = str(f)
        sig, label_only = signature_for_workspace(f, log_root=log_root, label=labels.get(f, ""))
        score = sum(sig.get(t, 0) for t in q_set)
        matched = sorted(t for t in q_set if t in sig)
        if score > 0:
            scored.append({
                "folder": f,
                "label": labels.get(f, Path(f).name),
                "score": score,
                "matched": matched,
                "label_only": label_only,
            })
    scored.sort(key=lambda r: (r["score"], len(r["matched"])), reverse=True)
    return scored[:max(1, int(limit))]

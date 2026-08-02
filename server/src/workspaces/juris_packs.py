# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Jurisdiction packs — governance posture as versioned overlay DATA (§ 4.5).

Generalises the `discipline.json` manifest pattern: a pack maps footprint
tags (the action-gate's risk vocabulary, plus any domain tags a vertical
adds) to NAMED control forms from the § 1.5 algebra. Packs are data the
operator opts into; they describe a governance posture, never legal
compliance (locked decision: no compliance claims until 0.7).

Composition is the algebra and nothing else: per tag, `compose_all` over
the stack — strictest wins, an overlay can only ADD guarantees, order
never matters. The composed stack feeds
`policy_matrix.effective_control_form(required_forms=...)`, so pack data
can tighten a painted cell and can never loosen one.

Closed vocabulary at load: a control-form name the algebra does not know
is a load ERROR (the NT-14 rule applied to pack data). `effective_from`
is carried and validated (YYYY-MM-DD) but not yet enforced — dating
enforcement rides the surface that resolves a folder's pack stack.
Signing rides the registry/seal primitive; these reference files ship
unsigned in-repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .controlforms import Guarantees, compose_all, guarantees

_REQUIRED = ("pack_id", "version", "jurisdiction", "controls")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PACK_DIR = Path(__file__).parent / "data" / "packs" / "jurisdiction"


def load_pack(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load + validate one pack from a path or an already-parsed dict."""
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = dict(source)
    for field in _REQUIRED:
        if field not in raw:
            raise ValueError(f"pack missing required field {field!r}")
    if not isinstance(raw["controls"], dict):
        raise ValueError("pack 'controls' must be a mapping tag -> form name")
    eff = raw.get("effective_from", "")
    if eff and not _DATE.match(eff):
        raise ValueError(
            f"effective_from must be YYYY-MM-DD, got {eff!r}")
    for tag, form in raw["controls"].items():
        if not tag or not isinstance(tag, str):
            raise ValueError(f"invalid footprint tag {tag!r}")
        guarantees(form)        # closed vocabulary — raises on invention
    raw.setdefault("extends", "")
    raw.setdefault("effective_from", "")
    return raw


def load_reference_pack(name: str) -> dict[str, Any]:
    """One of the shipped reference packs (`eu-base`, `de-overlay`)."""
    path = _PACK_DIR / f"{name}.json"
    if not path.exists():
        known = sorted(p.stem for p in _PACK_DIR.glob("*.json"))
        raise ValueError(f"unknown reference pack {name!r}; known: {known}")
    return load_pack(path)


def compose_packs(packs: Iterable[dict[str, Any]]) -> dict[str, Guarantees]:
    """Strictest-wins per tag over the whole stack (pure `compose_all`)."""
    stack = list(packs)
    out: dict[str, Guarantees] = {}
    for pack in stack:
        for tag in pack.get("controls", {}):
            if tag not in out:
                out[tag] = compose_all(
                    p["controls"][tag] for p in stack
                    if tag in p.get("controls", {}))
    return out


def required_forms(packs: Iterable[dict[str, Any]],
                   footprint: Iterable[str]) -> list[Guarantees]:
    """The forms an action's footprint picks up from the stack — feed this
    straight into ``effective_control_form(required_forms=...)``. Tags the
    stack does not govern contribute nothing (auto stays auto)."""
    composed = compose_packs(packs)
    return [composed[t] for t in footprint if t in composed]


# ── folder binding: declaration in folder policy, TDM-style cascade ──────────
# A folder declares its OWN stack in `FolderPolicy.juris_packs`. Resolution
# walks the ancestors (bounded, like resolve_ai_training_optout): ancestor
# packs come first and CANNOT be removed below — a descendant only ever adds.
# That is the § 1.5 monotonicity rule again: more packs, never fewer.

def _load_entry(entry: str, folder_path: str | Path) -> dict[str, Any]:
    """Resolve one declared entry: reference-pack name, else a file path
    (absolute, or relative to the declaring folder)."""
    ref = _PACK_DIR / f"{entry}.json"
    if ref.exists():
        return load_pack(ref)
    p = Path(entry)
    if not p.is_absolute():
        p = Path(folder_path) / p
    if p.exists():
        return load_pack(p)
    known = sorted(q.stem for q in _PACK_DIR.glob("*.json"))
    raise ValueError(
        f"pack {entry!r} is neither a reference pack ({known}) nor a file")


def set_folder_packs(
    folder_path: str | Path,
    packs: list[str],
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> dict[str, Any]:
    """Declare this folder's own pack stack (audited policy change).

    Every entry must load and validate BEFORE anything persists — a stack
    with one invented pack changes nothing.
    """
    from .mutation_log import LogEvent, MutationLog
    from .policy import load_policy, save_policy
    resolved = [_load_entry(e, folder_path) for e in packs]   # validate first
    policy = load_policy(folder_path)
    policy.juris_packs = list(packs)
    save_policy(folder_path, policy)
    log = MutationLog(folder_path, log_root=log_root)
    audit_id = log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy:juris_packs",
        channel="system",
        actor=actor,
        extra={"policy_change": "juris_packs",
               "packs": list(packs),
               "pack_ids": [r["pack_id"] for r in resolved]},
    ))
    return {"ok": True, "juris_packs": list(packs), "audit_id": audit_id}


def resolve_folder_packs(folder_path: str | Path) -> list[dict[str, Any]]:
    """The folder's EFFECTIVE stack: ancestors' packs first (root-most
    outermost), then its own; deduplicated by pack_id (first declaration
    wins — composition is idempotent anyway). A descendant cannot remove
    what an ancestor declared."""
    from .policy import load_policy
    p = Path(folder_path).expanduser().resolve()
    chain: list[Path] = []
    for _ in range(64):   # bounded walk, like resolve_ai_training_optout
        chain.append(p)
        if p.parent == p:
            break
        p = p.parent
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in reversed(chain):                    # root-most first
        for entry in load_policy(folder).juris_packs:
            pack = _load_entry(entry, folder)
            if pack["pack_id"] not in seen:
                seen.add(pack["pack_id"])
                out.append(pack)
    return out


def active_packs(packs: Iterable[dict[str, Any]],
                 as_of: str = "") -> list[dict[str, Any]]:
    """Drop packs whose ``effective_from`` lies after ``as_of`` (YYYY-MM-DD;
    lexicographic compare is date order). Undated packs are always active."""
    if as_of and not _DATE.match(as_of):
        raise ValueError(f"as_of must be YYYY-MM-DD, got {as_of!r}")
    return [p for p in packs
            if not p.get("effective_from")
            or not as_of
            or p["effective_from"] <= as_of]


def folder_required_forms(
    folder_path: str | Path,
    footprint: Iterable[str],
    *,
    as_of: str = "",
) -> list[Guarantees]:
    """End to end: the folder's effective, date-active stack applied to an
    action's footprint — ready for ``effective_control_form``."""
    stack = active_packs(resolve_folder_packs(folder_path), as_of=as_of)
    return required_forms(stack, footprint)

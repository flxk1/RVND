# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Policy matrix — the user-paintable cross of autonomy grade × oversight level.

Each cell ``(grade, oversight)`` holds a traffic light: ``go`` (agent acts),
``ask`` (agent shows the human first), ``block`` (human does it). The matrix is
the per-folder DEFAULT a workflow step inherits; a step may override its own cell
(``workflows.WorkflowStep.autonomy_grade`` / ``.oversight``).

Safety invariant: the painted light is composed with the gate's structural verdict
and the data's privacy-class floor, and can only make a cell STRICTER, never
looser — a regulated cell cannot be made ``go``; a NO-GO action cannot be painted
``go``. Pure + deterministic; the only I/O is a small JSON store keyed by path.

Grounding (mental model -> mechanic):
  * the two axes — reach (grade) × involvement (oversight) — *separate* what the
    Knight 2025 "levels of autonomy" taxonomy conflates [Levels of Autonomy 2025];
    crossing them is the delegation surface [Kolt 2025, principal-agent].
  * the **anti-diagonal default** (more reach demands more involvement) is requisite
    variety made into a default [Ashby 1956; Beer 1972 attenuator/amplifier], and
    the "high-both" target [Shneiderman 2020/2022].
  * the **L4xautonomous = block** corner is red-by-default because silent full-reach
    is the documented oversight failure [Green 2022; Crootof, Kaminski & Price 2023].
  * **paint can only tighten** (strictest-wins over the privacy floor + gate verdict)
    keeps the human's control real, not nominal [Santoni de Sio & van den Hoven 2018;
    gate NT-13 monotonicity].
  * ``effective_light``'s ``reason`` is the *tracing* condition — the user sees WHY a
    cell resolved as it did [Cavalcante Siebert 2023]; in the CLI this is ``explain``.
  * per-step binding routes the *residual* per action, where no sound procedure
    exists [Hart 1961 open texture].
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from . import verdict as _v

from .adapters.policy_languages import grade_levels as _grade_levels

GRADES = _grade_levels()   # action reach — consumed from governance's grammar
OVERSIGHT = ("autonomous", "notify", "review", "approve", "supervised", "manual")
LIGHTS = ("go", "ask", "block")
# Ordering + gate mapping derive from the shared tri-state — one source of truth
# (verdict.Verdict / strictest), the matrix just keeps its own public words.
_SEV = {lt: _v.severity(_v.from_light(lt)) for lt in LIGHTS}
_GLYPH = {"go": "G", "ask": "A", "block": "R"}

# Privacy-class oversight floor (mirrors lock.oversight.PRIVACY_CLASS_DEFAULTS).
_PRIVACY_FLOOR = {"public": "notify", "pseudonymous": "review",
                  "sensitive": "approve", "regulated": "supervised"}

# action_gate.Verdict value -> light, via the canonical tri-state.
_VERDICT_LIGHT = {g: _v.to_light(_v.from_gate(g))
                  for g in ("GO", "CONDITIONAL", "NO-GO")}


def _rec(gi: int, oi: int) -> str:
    """Recommended default light: the anti-diagonal — more reach demands more
    involvement; the full-reach × never-asks corner is red."""
    if oi == 5:                                  # manual
        return "block"
    if oi == 4:                                  # supervised
        return "ask"
    if oi == 3:                                  # approve
        return "go" if gi <= 1 else "ask"
    if oi == 2:                                  # review state
        return "go" if gi <= 2 else "ask"
    if oi == 1:                                  # notify
        return "go" if gi <= 3 else "ask"
    return "go" if gi <= 2 else ("ask" if gi == 3 else "block")   # autonomous


def recommended_default() -> dict:
    """The anti-diagonal default matrix as ``{grade: {oversight: light}}``."""
    return {g: {o: _rec(gi, oi) for oi, o in enumerate(OVERSIGHT)}
            for gi, g in enumerate(GRADES)}


def stricter(a: str, b: str) -> str:
    """Strictest of two lights — delegates to the shared compose rule."""
    return _v.to_light(_v.strictest(_v.from_light(a), _v.from_light(b)))


def _floor_oversight(requested: str, privacy_class: Optional[str]) -> str:
    floor = _PRIVACY_FLOOR.get(privacy_class or "")
    if floor is None:
        return requested
    return requested if OVERSIGHT.index(requested) >= OVERSIGHT.index(floor) else floor


def effective_light(matrix: dict, *, grade: str, oversight: str,
                    privacy_class: Optional[str] = None,
                    gate_verdict: Optional[str] = None) -> dict:
    """Compose the painted cell with the privacy floor and the gate verdict.

    Returns ``{light, painted, floored_oversight, gate_light, reason}``. The
    result can only be STRICTER than the painted cell — never looser.
    """
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}")
    if oversight not in OVERSIGHT:
        raise ValueError(f"oversight must be one of {OVERSIGHT}")
    floored = _floor_oversight(oversight, privacy_class)
    painted = (matrix.get(grade, {}).get(floored)
               or _rec(GRADES.index(grade), OVERSIGHT.index(floored)))
    # Absent gate verdict → "go" (the gate didn't run; it adds no constraint).
    # An UNRECOGNISED verdict goes through the now-fail-safe from_gate (→ DENY →
    # "block"), not the old fail-OPEN "go" default (M1).
    gate_light = _v.to_light(_v.from_gate(gate_verdict)) if gate_verdict else "go"
    eff = stricter(painted, gate_light)
    why = []
    if floored != oversight:
        why.append(f"privacy floor {privacy_class}→{floored}")
    if _SEV[gate_light] > _SEV[painted]:
        why.append(f"gate {gate_verdict}")
    if not why:
        why.append("painted policy")
    return {"light": eff, "painted": painted, "floored_oversight": floored,
            "gate_light": gate_light, "reason": "; ".join(why)}


def effective_control_form(matrix: dict, *, grade: str, oversight: str,
                           privacy_class: Optional[str] = None,
                           gate_verdict: Optional[str] = None,
                           required_forms: Iterable[str] = ()) -> dict:
    """``effective_light`` lifted into the § 1.5 control-form algebra.

    The effective light maps in via ``controlforms.from_traffic_light``
    (go→auto, ask→single_approver, block→block) and is composed — conjunction,
    strictest-wins — with any additionally required forms (a pack or step
    demanding four_eyes / expert_review). Required forms can only ADD
    guarantees; BLOCK absorbs from either side. Returns the ``effective_light``
    dict extended with ``control_form`` (canonical or composite name) and
    ``guarantees`` (sorted list).
    """
    from . import controlforms as _cf
    res = effective_light(matrix, grade=grade, oversight=oversight,
                          privacy_class=privacy_class, gate_verdict=gate_verdict)
    composed = _cf.compose_all(
        [_cf.from_traffic_light(res["light"]), *required_forms])
    res["control_form"] = _cf.name_of(composed)
    res["guarantees"] = sorted(composed)
    return res


def render_matrix_text(matrix: dict) -> str:
    """ASCII render for the CLI: rows = oversight, cols = grade (G/A/R)."""
    label = "oversight \\ grade"
    head = f"{label:>18} |" + "".join(f" {g:>4}" for g in GRADES)
    out = [head, "-" * len(head)]
    for o in OVERSIGHT:
        cells = "".join(f" {_GLYPH[matrix[g][o]]:>4}" for g in GRADES)
        out.append(f"{o:>18} |{cells}")
    out.append("  forbidden (NO-GO) |" + "    R" * len(GRADES))
    out.append("legend: G=go  A=ask(shows you)  R=you-do-it   "
               "(regulated/footprint floors compose stricter)")
    return "\n".join(out)


# --- generic JSON helpers (used for round-trips / explicit paths) ---

def _backfill(raw: dict) -> dict:
    """A full grid: take any valid cell from ``raw``, default the rest."""
    base = recommended_default()
    for g in GRADES:
        for o in OVERSIGHT:
            v = (raw.get(g, {}) or {}).get(o)
            if v in LIGHTS:
                base[g][o] = v
    return base


def load_matrix(path: str | Path) -> dict:
    """Load a matrix from ``path``; return the recommended default if absent."""
    p = Path(path)
    if not p.exists():
        return recommended_default()
    raw = json.loads(p.read_text(encoding="utf-8"))
    # validate + backfill any missing cell from the default
    base = recommended_default()
    for g in GRADES:
        for o in OVERSIGHT:
            v = raw.get(g, {}).get(o)
            if v in LIGHTS:
                base[g][o] = v
    return base


def save_matrix(path: str | Path, matrix: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(matrix, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return p


def set_cell(matrix: dict, grade: str, oversight: str, light: str) -> dict:
    if grade not in GRADES or oversight not in OVERSIGHT or light not in LIGHTS:
        raise ValueError("invalid grade/oversight/light")
    matrix.setdefault(grade, {})[oversight] = light
    return matrix


def set_row(matrix: dict, oversight: str, light: str) -> dict:
    """Bulk: set a whole oversight row across all grades."""
    for g in GRADES:
        set_cell(matrix, g, oversight, light)
    return matrix


def set_col(matrix: dict, grade: str, light: str) -> dict:
    """Bulk: set a whole grade column across all oversight levels."""
    for o in OVERSIGHT:
        set_cell(matrix, grade, o, light)
    return matrix


# ── hierarchy: in every workspace, global top-down, override cascade ─────────────
# Exactly like the lock: the matrix lives in each workspace's policy (.workspace-policy.json).
# A workspace with no own matrix INHERITS — it follows the nearest ancestor that set
# one, or the global anti-diagonal default. A workspace WITH its own matrix OVERRIDES
# for itself and its subtree (it may be looser than its parent — the runtime
# floors, gate verdict + privacy class, still bind via effective_light; that is a
# separate axis from this declared policy). Nearest setting wins.

def own_matrix(folder_path: str | Path, *,
               log_root: Optional[str | Path] = None) -> Optional[dict]:
    """This workspace's OWN grid (full, backfilled), or None when it inherits."""
    from .policy import load_policy
    raw = load_policy(folder_path).policy_matrix
    return _backfill(raw) if isinstance(raw, dict) else None


def _audit_matrix_change(folder_path: str | Path, actor: str, log_root,
                         extra: dict) -> None:
    """Painting the grid is a governance state change — it lands on the
    chain like every other policy change (acting-party stamp, § 1.5)."""
    from .mutation_log import LogEvent, MutationLog
    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy:policy_matrix",
        channel="system",
        actor=actor,
        extra={"policy_change": "policy_matrix", **extra},
    ))


def save_own_matrix(folder_path: str | Path, matrix: dict, *,
                    actor: str = "user",
                    log_root: Optional[str | Path] = None) -> dict:
    """Persist this workspace's own grid into its policy (creates the override).
    Audited: the change + acting party land on the chain."""
    from .policy import load_policy, save_policy
    pol = load_policy(folder_path)
    pol.policy_matrix = {g: {o: matrix[g][o] for o in OVERSIGHT} for g in GRADES}
    save_policy(folder_path, pol)
    _audit_matrix_change(folder_path, actor, log_root,
                         {"matrix": pol.policy_matrix})
    return pol.policy_matrix


def clear_own_matrix(folder_path: str | Path, *,
                     actor: str = "user",
                     log_root: Optional[str | Path] = None) -> None:
    """Drop this workspace's override — it goes back to inheriting (reset).
    Audited like the set."""
    from .policy import load_policy, save_policy
    pol = load_policy(folder_path)
    pol.policy_matrix = None
    save_policy(folder_path, pol)
    _audit_matrix_change(folder_path, actor, log_root, {"cleared": True})


def resolve_inherited(folder_path: str | Path, *,
                      log_root: Optional[str | Path] = None) -> dict:
    """What the workspace inherits from ABOVE: the nearest ancestor with an own grid,
    else the global default. Excludes the workspace's own grid."""
    from .memory import discover_ancestors
    anc = discover_ancestors(folder_path, log_root=log_root)   # shallowest-first
    for a in reversed(anc):                                    # nearest first
        m = own_matrix(a, log_root=log_root)
        if m is not None:
            return m
    return recommended_default()


def resolve_matrix(folder_path: str | Path, *,
                   log_root: Optional[str | Path] = None) -> dict:
    """The EFFECTIVE matrix: this workspace's own grid if it has one (override), else
    what it inherits (nearest ancestor, else global default)."""
    own = own_matrix(folder_path, log_root=log_root)
    return own if own is not None else resolve_inherited(folder_path, log_root=log_root)


def has_matrix_in_chain(folder_path: str | Path, *,
                        log_root: Optional[str | Path] = None) -> bool:
    """True if this workspace or any ancestor set an own matrix — the runtime opt-in
    (a sub-workspace with no own grid still inherits an ancestor's)."""
    from .memory import discover_ancestors
    if own_matrix(folder_path, log_root=log_root) is not None:
        return True
    for a in discover_ancestors(folder_path, log_root=log_root):
        if own_matrix(a, log_root=log_root) is not None:
            return True
    return False

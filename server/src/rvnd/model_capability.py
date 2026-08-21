# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Model capability — route an LLM task to a CAPABLE local model, or degrade HONESTLY.

The missing layer between what we HAVE (`models_registry`) and what tasks NEED. It carries three
things the code lacked: a per-task capability REQUIREMENT, a model capability PROFILE (inferred
from registry metadata, upgraded by an `attestation`-style gold-set probe), and the MATCH —
capable-run-local, or a named degrade. The rule (same doctrine as `capability_ir`'s
degrades-to-prompt, "loss reported never silent"): a task runs on the local model ONLY if the
model meets it; otherwise the honest outcome is deterministic-fallback / capture-first /
escalate-human / escalate-cloud — never a too-small model silently producing garbage as truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

#: capability tags a task may require of a model
CAPS = ("classification", "json", "instruction_following", "long_context", "reasoning", "general")


@dataclass(frozen=True)
class TaskReq:
    task: str
    requires: frozenset          # capability tags the model must have
    on_miss: str                 # the honest degrade when it doesn't


#: the LLM tasks (the roles the local model plays) → their requirement + degrade
TASKS: dict[str, TaskReq] = {
    "privacy_semantic": TaskReq("privacy_semantic", frozenset({"classification"}), "deterministic"),
    "extraction":       TaskReq("extraction", frozenset({"json", "instruction_following"}), "deterministic"),
    "interpretation":   TaskReq("interpretation", frozenset({"long_context", "reasoning"}), "escalate_human"),
    "ask_routing":      TaskReq("ask_routing", frozenset({"classification"}), "keyword_only"),
    "intake":           TaskReq("intake", frozenset({"classification"}), "capture_first"),
    "completion":       TaskReq("completion", frozenset({"general"}), "escalate_cloud"),
}


@dataclass
class ModelProfile:
    model_id: str
    capabilities: frozenset      # measured (probed) or inferred tags
    tier: str = "small"          # small | medium | large
    probed: bool = False         # True = measured by gold-set; False = inferred from metadata

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = sorted(self.capabilities)
        return d


# size → inferred capabilities. The CHEAP default; a gold-set probe (attestation pattern) upgrades
# a profile to ``probed=True`` with measured tags. Conservative: small models are NOT credited with
# json/reasoning, so extraction/interpretation degrade rather than hallucinate.
_TIER_CAPS = {
    "large":  frozenset({"classification", "json", "instruction_following", "long_context", "reasoning", "general"}),
    "medium": frozenset({"classification", "json", "instruction_following", "general"}),
    "small":  frozenset({"classification", "general"}),
}


def tier_for_params(billions: float) -> str:
    return "large" if billions >= 30 else "medium" if billions >= 7 else "small"


def infer_profile(model_id: str, *, params_billions: Optional[float] = None,
                  tier: Optional[str] = None) -> ModelProfile:
    """Inferred profile from registry metadata (size). Not probed — conservative by design."""
    t = tier or (tier_for_params(params_billions) if params_billions is not None else "small")
    return ModelProfile(model_id, _TIER_CAPS.get(t, _TIER_CAPS["small"]), tier=t, probed=False)


@dataclass
class Match:
    task: str
    model_id: Optional[str]
    capable: bool
    action: str                  # run_local | deterministic | capture_first | escalate_human | escalate_cloud | keyword_only
    missing: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def match(task: str, profile: Optional[ModelProfile]) -> Match:
    """Capable-run-local, or the task's honest degrade. No capable model → degrade, never guess."""
    req = TASKS.get(task)
    if req is None:
        raise ValueError(f"unknown task {task!r}; one of {sorted(TASKS)}")
    if profile is None:
        return Match(task, None, False, req.on_miss, sorted(req.requires),
                     f"no model registered for {task} — degrade to {req.on_miss}")
    missing = sorted(req.requires - profile.capabilities)
    if missing:
        return Match(task, profile.model_id, False, req.on_miss, missing,
                     f"{profile.model_id} lacks {missing} → degrade to {req.on_miss}")
    note = "meets the requirement" + ("" if profile.probed else " (inferred from size, not probed)")
    return Match(task, profile.model_id, True, "run_local", [], f"{profile.model_id} {note}")


def select(task: str, profiles: Iterable[ModelProfile]) -> Match:
    """Pick the best CAPABLE model for a task (prefer probed, then larger tier); else degrade."""
    if task not in TASKS:
        # validate FIRST — indexing TASKS[task] below raised KeyError, which the MCP facade's
        # missing-param guard misdiagnosed as "missing param": the param was PRESENT, its value
        # unknown. Same honest ValueError as match().
        raise ValueError(f"unknown task {task!r}; one of {sorted(TASKS)}")
    order = {"large": 0, "medium": 1, "small": 2}
    capable = [p for p in profiles if not (TASKS[task].requires - p.capabilities)]
    if not capable:
        # report the miss against the best available (or None)
        best = min(profiles, key=lambda p: order.get(p.tier, 9), default=None) if profiles else None
        return match(task, best)
    best = min(capable, key=lambda p: (0 if p.probed else 1, order.get(p.tier, 9)))
    return match(task, best)


# ── the bridge: models_registry → capability (the "missing layer" this module is named for) ──
def profile_from_registry_id(model_id: str) -> ModelProfile:
    """Infer a profile from a pulled model's id — parse a size hint (e.g. ``…-8b…``) for the tier,
    else the conservative ``small``. Not probed; a gold-set probe would upgrade it."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", model_id.lower())
    return infer_profile(model_id, params_billions=float(m.group(1)) if m else None)


def for_task(task: str) -> Match:
    """Read the LOCAL ``models_registry``, build profiles, and select the best model for ``task`` —
    or the honest degrade if none is capable (including when nothing is pulled). Registry only; no
    network. This is the caller-facing entry the rest of the system consults."""
    try:
        from .models_registry import list_models
        profiles = [profile_from_registry_id(e.id) for e in list_models()]
    except Exception:
        profiles = []
    return select(task, profiles)


def readiness() -> dict[str, Any]:
    """Read-only projection: for every task, is a capable local model available, or the honest
    degrade? The 'readiness/degrade' surface — declares, never certifies; runs nothing."""
    tasks = {t: for_task(t).as_dict() for t in TASKS}
    return {"version": "model_capability/v1",
            "ready": sorted(t for t, m in tasks.items() if m["capable"]),
            "degraded": sorted(t for t, m in tasks.items() if not m["capable"]),
            "tasks": tasks}

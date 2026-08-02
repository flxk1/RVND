# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Policy resolve — the rule graph IS the policy pipeline.

Plain governance prose is extracted to structured norms (``rule_extractor``),
each placed on the legal map at a per-workspace policy pseudo-instrument
(``policy:<slug>``) so the resolver can retrieve it, then reasoned over
(``reasoning_walker``) when a local model is available. The requisite-variety
ledger is derived from the facets — the norms, not regex cues, decide what the
governor absorbs. The ``.lg`` patch/netlist for apply is still compiled by
``policy_ingest`` (the express→netlist compiler), so ``patch_apply`` is unchanged.

Degrades honestly: no facets → the ``policy_ingest`` regex drafter as a fast
fallback; no capable model → the deterministic resolve (facets→graph→ledger)
with the reasoning step marked unavailable. Never fakes an answer; ``applied``
stays False until a human confirms. Return shape matches ``policy_ingest.ingest``
so the front door renders it unchanged.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from . import loomground_lang as _L
from . import model_capability, policy_ingest, reasoning_walker, rule_extractor
from .rule_extractor import RuleFacet
from .rule_registry import Anchor, RuleRegistry


def policy_anchor(registry: RuleRegistry) -> str:
    """The per-workspace policy pseudo-instrument norms are placed at, so a
    citation-free question can still retrieve them via ``rules_at``."""
    slug = registry.folder.name or "session"
    return "policy:" + slug


def _norm_sentence(s: str) -> str:
    """Fold a clause to a comparison key so an absorbed sentence and its residual
    twin match despite trailing punctuation or whitespace runs."""
    return " ".join((s or "").lower().split()).strip().rstrip(".;:")


def _express_from_facet(f: RuleFacet) -> str:
    """A one-line ledger entry for a resolved norm, keyed on its deontic class."""
    subj = (f.subject or "the subject").strip()
    act = (f.action or "").strip()
    tail = f"{subj} {act}".strip() if act else subj
    if f.modal == "prohibition":
        return f"prohibit — {tail}"
    if f.modal == "obligation":
        return f"gate — {tail} (obligation)"
    if f.modal == "right":
        return f"right — {tail}"
    if f.modal == "permission":
        return f"permit — {tail}"
    return f"{f.modal or 'rule'} — {tail}"


# policy_ingest splits sentences on this boundary; reused so the residual text
# handed back to the compiler is partitioned the same way the compiler reads it.
_PI_SENT_SPLIT = re.compile(r"(?<=[.;])\s+|\n+")


def _pi_sentences(text: str) -> list[str]:
    return [s for s in _PI_SENT_SPLIT.split(text or "") if s.strip()]


def _pi_spans(text: str) -> list[tuple[int, int, str]]:
    """The compiler's sentences as ``(start, end, text)`` offsets into ``text``.

    The extractor and the express compiler segment prose differently, so a
    sentence from one can only be reconciled against the other in the single
    coordinate system they share: character offsets into the source. Matching
    their text instead (substring containment) withholds any clause that merely
    reads as a fragment of another — a heading, a short restatement — which then
    reaches neither ``express`` nor a hand-back bucket and leaves the ledger."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for s in _PI_SENT_SPLIT.split(text or ""):
        if not s.strip():
            continue
        i = text.find(s, pos)
        if i < 0:                       # the splitter never rewrites text; be safe
            i = pos
        out.append((i, i + len(s), s))
        pos = i + len(s)
    return out


def _facet_spans(text: str, facets: list[RuleFacet]) -> list[tuple[int, int]]:
    """Offsets of each facet's source sentence in ``text``, searched in document
    order so a repeated sentence maps to its own occurrence. A facet whose raw
    sentence cannot be located contributes no span; the caller falls back to
    exact sentence equality, which may under-withhold but never drops a clause."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for f in facets:
        raw = (f.raw_sentence or "").strip()
        if not raw:
            continue
        i = text.find(raw, pos)
        if i < 0:
            i = text.find(raw)
        if i >= 0:
            spans.append((i, i + len(raw)))
            pos = max(pos, i + len(raw))
    return spans


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in spans)


def _prohibition_kind(f: RuleFacet) -> str:
    """The Loomground gate kind a prohibition facet governs, via the same
    action-slug reduction the express compiler uses, so a facet-compiled
    prohibition names the same gate a hand-written ``prohibit`` would."""
    kind = policy_ingest._action(f.action) if f.action else ""
    if not kind or kind == "x":
        kind = policy_ingest._action(f.subject)
    return kind or "x"


def _compile_prohibitions(prohib_facets: list[RuleFacet]) -> dict[str, Any]:
    """A patch fragment for the prohibition facets: one gate + one ``prohibit``
    per distinct kind, the actor bound through it to the boundary. This is the
    facet layer's own express→netlist step for the one primitive a prohibition
    facet maps onto cleanly — so the netlist a user applies matches the ledger,
    never a residual mis-extraction of the same sentence."""
    gates: dict[str, dict[str, Any]] = {}
    cords: list[dict[str, str]] = []
    grants: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    for f in prohib_facets:
        kind = _prohibition_kind(f)
        if kind in gates:
            continue
        gates[kind] = {"id": kind, "class": "gate",
                       "risk_floor": policy_ingest._risk((f.raw_sentence or "").lower())}
        grants.append({"gate": kind, "actor": policy_ingest._ACTOR})
        cords.append({"from": policy_ingest._ACTOR, "to": kind})
        cords.append({"from": kind, "to": "master"})
        prohibitions.append({"kind": kind})
    return {"gates": gates, "cords": cords, "grants": grants,
            "prohibitions": prohibitions}


def _merge_patch(base: dict[str, Any], add: dict[str, Any]) -> dict[str, Any]:
    """Union the facet-compiled prohibition fragment into the residual patch:
    dedupe nodes by id, cords by endpoints, grants by (gate, actor); concatenate
    the prohibition list. The prohibition sentences were withheld from the
    residual compile, so there is no double count."""
    patch: dict[str, Any] = {k: (list(v) if isinstance(v, list) else v)
                             for k, v in base.items()}
    nodes = patch.setdefault("nodes", [])
    have = {n["id"] for n in nodes}
    if policy_ingest._ACTOR not in have:
        nodes.insert(0, {"id": policy_ingest._ACTOR, "class": "actor"})
        have.add(policy_ingest._ACTOR)
    for g in add["gates"].values():
        if g["id"] not in have:
            nodes.append(g)
            have.add(g["id"])
    cords = patch.setdefault("cords", [])
    seen_c = {(c.get("from"), c.get("to")) for c in cords}
    for c in add["cords"]:
        if (c["from"], c["to"]) not in seen_c:
            cords.append({"from": c["from"], "to": c["to"]})
            seen_c.add((c["from"], c["to"]))
    grants = patch.setdefault("grants", [])
    seen_g = {(g.get("gate"), g.get("actor")) for g in grants}
    for g in add["grants"]:
        if (g["gate"], g["actor"]) not in seen_g:
            grants.append(g)
            seen_g.add((g["gate"], g["actor"]))
    patch.setdefault("prohibitions", []).extend(add["prohibitions"])
    if not patch["prohibitions"]:
        patch.pop("prohibitions")
    if not patch["grants"]:
        patch.pop("grants")
    return patch


def _default_model_fn() -> Callable[[str], str]:
    from .local_llm import complete

    def model_fn(prompt: str) -> str:
        res = complete(prompt, max_tokens=512)
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "local-LLM failed"))
        return res.get("response", "")

    return model_fn


def _reason(policy_text: str, registry: RuleRegistry, anchor: str, *,
            model_fn: Optional[Callable[[str], str]],
            capability: Optional[Any]) -> dict[str, Any]:
    """Run the model-reasoning step over the placed norms, or the honest degrade.

    Gates on ``model_capability.for_task("interpretation")``. Without a capable
    model the walker is NOT run and the caller keeps the deterministic resolve."""
    cap = capability if capability is not None else model_capability.for_task("interpretation")
    if not getattr(cap, "capable", False):
        return {"available": False,
                "reason": getattr(cap, "reason", "no capable local model for interpretation")}
    fn = model_fn or _default_model_fn()
    try:
        result = reasoning_walker.walk(policy_text, registry=registry, model_fn=fn,
                                       cited_entities={anchor}, profile="legal-de")
    except Exception as e:                                     # noqa: BLE001
        return {"available": False, "reason": f"reasoning step failed: {e}"}
    out: dict[str, Any] = {"available": True, "profile": "legal-de"}
    case = result.get("case")
    if case is not None:
        cd = case.to_dict()
        out["resolution"] = cd.get("resolution", {})
        out["coverage"] = cd.get("coverage")
        out["gaps"] = cd.get("gaps", [])
        out["case"] = cd
    if result.get("refused"):
        out["refused"] = result["refused"]
    return out


def resolve(policy_text: str, *, folder: Optional[str] = None,
            registry: Optional[RuleRegistry] = None, user: str = "",
            user_root: Optional[str] = None, log_root: Optional[str] = None,
            model_fn: Optional[Callable[[str], str]] = None,
            capability: Optional[Any] = None) -> dict[str, Any]:
    """Resolve policy prose through the rule graph and return the digital twin.

    Same keys as :func:`policy_ingest.ingest` (``ok``, ``applied``, ``note``,
    ``classification``, ``patch``, ``netlist``) plus additive ``resolved_norms``
    and ``reasoning``. ``registry`` overrides ``folder``; with neither, an
    ephemeral isolated registry is used (the resolve still runs, nothing persists)."""
    if not isinstance(policy_text, str) or not policy_text.strip():
        return policy_ingest.ingest(policy_text)

    # Step 0 — extract. The fingerprint gate is NOT applied here: the caller has
    # already classified this input as a policy (governance_chat's intent route),
    # so the normative-fingerprint pre-gate — which scores by recognised regulated
    # actors and would reject a plain single-sentence policy whose subject is "the
    # system" — is redundant and would send realistic policies to the drafter. The
    # sentence-level modal patterns are the precision filter on this path. No facets
    # → the regex drafter as a fast fallback (the extractor is lower-recall; bare-
    # `right` prose it misses must still reach a twin rather than vanish).
    facets = rule_extractor.extract_rules(policy_text, gated_by_fingerprint=False)
    if not facets:
        return policy_ingest.ingest(policy_text)

    # Fail-closed + the case-law genre guard: the express compiler's verdict over
    # the full text decides whether this is a policy instrument at all. A judgment
    # (quarantined) or an ill-formed twin is returned unchanged — no norms placed.
    guard = policy_ingest.ingest(policy_text)
    if not guard.get("ok") or guard.get("quarantined"):
        return guard

    # Compile a coherent apply-side patch. The prohibition facets are compiled by
    # the facet layer itself (``_compile_prohibitions``); their sentences are
    # WITHHELD from the express compiler, which runs only over the residual — so a
    # sentence the facet reads as a prohibition can never also surface in the
    # netlist as a redress or reservation the shallow cue mis-extracted from it.
    prohib_facets = [f for f in facets if f.modal == "prohibition"]
    prohib_norms = {_norm_sentence(f.raw_sentence) for f in prohib_facets if f.raw_sentence}
    prohib_norms.discard("")
    if not prohib_norms:
        # nothing withheld → the residual is the full text; reuse the guard compile.
        base_patch = guard.get("patch") or {}
        base_class = guard.get("classification") or {}
    else:
        # Withhold exactly the compiler-sentences overlapping a prohibition
        # facet's own source span, plus any that equal one outright (the fallback
        # for a facet whose sentence could not be located). Reconciling by span
        # rather than by substring keeps a clause that merely reads as a fragment
        # of a prohibition in the residual, where the ledger still accounts for it.
        proh_spans = _facet_spans(policy_text, prohib_facets)
        residual_text = "\n".join(
            s for (start, end, s) in _pi_spans(policy_text)
            if not _overlaps((start, end), proh_spans)
            and _norm_sentence(s) not in prohib_norms)
        base_patch, base_class = {"nodes": [], "cords": []}, {}
        if residual_text.strip():
            residual = policy_ingest.ingest(residual_text)
            if residual.get("ok") and not residual.get("quarantined"):
                base_patch = residual.get("patch") or {}
                base_class = residual.get("classification") or {}

    patch = _merge_patch(base_patch, _compile_prohibitions(prohib_facets))
    v = _L.validate(patch)
    if not v.get("ok"):
        # Never emit an ill-formed twin; the full-text compiler's patch is the
        # fail-closed floor (it is validated by construction).
        patch = guard.get("patch") or patch

    tmp: Optional[str] = None
    if registry is None:
        if folder is not None:
            registry = RuleRegistry(folder, user=user, user_root=user_root, log_root=log_root)
        else:
            tmp = tempfile.mkdtemp(prefix="rvnd-policy-resolve-")
            registry = RuleRegistry(Path(tmp) / "ws", user=user,
                                    user_root=Path(tmp) / "user", log_root=Path(tmp) / "log")
    try:
        anchor = policy_anchor(registry)
        # Step 1 — the bridge: place each facet as a norm at the policy anchor so
        # a citation-free question retrieves it (``rules_at`` / ``_norm_spans_for``).
        resolved: list[str] = []
        for i, f in enumerate(facets):
            r = registry.place_span(
                f.raw_sentence or policy_text, facet=f, kind="norm",
                anchors=[Anchor(anchor, "instrument", "cites", "workspace policy").to_dict()],
                pinpoint=f"policy#{i}", source_document="session-policy")
            resolved.append(r["id"])

        # Step 2 — reasoning (model-gated; honest degrade when no capable model).
        reasoning = _reason(policy_text, registry, anchor,
                            model_fn=model_fn, capability=capability)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    # The ledger is a partition: a clause the facets absorbed into `express` must
    # not also appear in a handed-back bucket. `express` comes from the facets;
    # `host/policy/unmapped` come from the express compiler over the residual —
    # subtract any absorbed sentence that still surfaced there (an obligation
    # facet's sentence stays in the residual for the compiler's host hand-off).
    absorbed = {_norm_sentence(f.raw_sentence) for f in facets if f.raw_sentence}
    absorbed.discard("")

    def _handback(bucket: str) -> list[str]:
        return [s for s in base_class.get(bucket, []) if _norm_sentence(s) not in absorbed]

    egress = [c for c in patch.get("cords", []) if c.get("to") == _L.MASTER]
    twin: dict[str, Any] = {
        "ok": True,
        "applied": False,
        "classification": {
            "express": [_express_from_facet(f) for f in facets],
            "host": _handback("host"),
            "policy": _handback("policy"),
            "unmapped": _handback("unmapped"),
        },
        "patch": patch,
        "netlist": _L.to_netlist(patch),
        "projection": _L.project(patch),
        "paths": [{"gate": c["from"], "path": [policy_ingest._ACTOR, c["from"], _L.MASTER]}
                  for c in egress],
        "host_handoffs": _handback("host"),
        "resolved_norms": resolved,
        "reasoning": reasoning,
        "note": (
            "Resolved via the rule graph — policy prose extracted to norms and "
            "placed on the legal map. Express lists the resolved norms; the patch "
            "compiles the prohibitions the facets absorbed plus the residual the "
            "express compiler drafts. Declares governance, does not certify "
            "compliance. Review and confirm before applying."),
    }
    return twin

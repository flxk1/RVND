# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governance map — the rules→roles/steps/risks projection, as one flat contract.

The netlist shows *who is wired to what*; this projection shows *which rule lands on which
role · step · risk, is it satisfied, is it live, is it settled or escalated*. It is the map
behind the collapsible panel: one flat list of typed rows, plus roll-ups and a group-by tree
the client folds. Grouping (by room / role / risk / status / instrument) is a facet on the
row, so the same list drives every view — no per-instrument special-casing, and 100+ rules
collapse to ~10 room bars each carrying its worst-status signal.

Doctrine (matches `conformity` / `requirements_house`):
  * Projection and assembly only — no new judgment here. It composes what the upstream layers
    already decided (`duty_identification` read the rule; `evidence_coverage` says furnished/
    empty; the loom patch says which gate/agents; `matcher` says applies/may-apply). This
    module keys, counts, and sorts.
  * Degrades gracefully — a row is valid with only the duty read. Evidence, gate binding, and
    live status are optional inputs; absent, the field is `n/a`/`null`, never guessed. A v1
    map (duties only) works; v2 depth (evidence, gates) fills the same fields.
  * Declares, never certifies — no score, no green check. An empty room is surfaced
    (a duty with no evidence), never smoothed over.
  * Deterministic and stable — same inputs → byte-identical `as_dict()`; the version string
    pins the contract the MCP op and the panel both build against.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Callable

SCHEMA_VERSION = "governance_map/v1"

# The "step" coordinate (value-chain stage per role) and the instrument-specific room cues are
# CARRIED DATA in `jurisdiction_packs` — which roles an instrument binds is a fact about that
# instrument, not this engine. Overridable per call via project(step_of=/room_of=).
def _role_steps() -> dict[str, str]:
    from . import jurisdiction_packs as _jp
    return _jp.role_steps()


# fallback room classifier — the REAL room comes from requirements_house; this is a labelled
# keyword fallback so the projection is self-contained when no room map is supplied. The cues
# here are instrument-NEUTRAL governance concepts; instrument-specific cues (a pack's "ce
# marking", "dpia") merge in from `jurisdiction_packs`, keyed by room so precedence is stable.
_ROOM_CUES_NEUTRAL = (
    ("Prohibited practices", ("prohibit", "shall not", "forbidden")),
    ("Risk management", ("risk management", "risk assessment", "impact assessment")),
    ("Human oversight", ("human oversight", "human review", "natural persons oversee")),
    ("Records & documentation", ("documentation", "record", "log", "keep up to date")),
    ("Transparency", ("inform", "disclos", "interacting with an ai", "transparen")),
    ("Security & robustness", ("security", "robust", "accuracy", "cyber", "breach")),
    ("Conformity", ("conformity", "assessment procedure")),
    ("Lawful basis & principles", ("lawful", "consent", "legal basis", "purpose limitation")),
)


def _room_cues() -> list[tuple[str, tuple[str, ...]]]:
    from . import jurisdiction_packs as _jp
    extra: dict[str, list[str]] = {}
    for room, cues in _jp.room_cues_extra():
        extra.setdefault(room, []).extend(cues)
    merged = [(room, cues + tuple(extra.pop(room, ())))
              for room, cues in _ROOM_CUES_NEUTRAL]
    merged += [(room, tuple(cs)) for room, cs in extra.items()]   # a pack's NEW rooms append
    return merged


def _classify_room(duty: Any) -> str:
    hay = f"{getattr(duty, 'action', '')} {getattr(duty, 'raw', '')}".lower()
    for room, cues in _room_cues():
        if any(c in hay for c in cues):
            return room
    return "Unassigned"


def _rule_id(instrument: str, pinpoint: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in f"{instrument} {pinpoint}".lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "rule"


# ── the ROW contract — one governance-map rule ────────────────────────────────────────────
@dataclass
class MapRule:
    # identity / provenance
    rule_id: str
    pinpoint: str                       # "Art. 9"
    instrument: str                     # whichever instrument the policy names ("AI Act", "PDPA", …)
    duty: str                           # short action phrase ("risk management system")
    operator: str                       # O | P | F | R | "?" (unread)
    # coordinates (the collapse keys)
    room: str
    role: Optional[str]                 # a role the instrument binds, or null (role-agnostic/unresolved)
    step: Optional[str]                 # value-chain stage
    risk_tier: Optional[str]            # a tier the instrument defines, or null
    areas: list[str]
    # resolution provenance
    resolution: str                     # deterministic | interpreter | ratified
    confidence: float
    needs_interpreter: bool
    # enforcement binding (optional — filled when compiled to a loom patch)
    gate_id: Optional[str]
    verdict: Optional[str]              # auto | ask | human | prohibited | reserved
    risk_floor: Optional[str]
    allowed_agents: list[str]
    # evidence / furnishing (optional — filled from evidence_coverage)
    coverage: str                       # furnished | empty | n/a
    artifacts: list[dict[str, Any]]     # [{id, kind, hash?, seal?}] — doc/model/image ids
    # live status / temporal (optional — filled from matcher / temporal)
    status: Optional[str]               # applies | may_apply | not_triggered
    currency: str                       # current | superseded | subject_to_overrule
    source: dict[str, Any]              # {instrument, pinpoint, celex?, url?, span?}
    # what to do about it (the CTA layer)
    demand_type: str                    # one of demand_cta.DEMAND_TYPES — the PRIMARY demand
    secondary: list[dict[str, Any]]     # OPEN set of secondary tasks [{ref|label, handler?}]
    cta: dict[str, Any]                 # {verb, label, handler, target} — opens, never executes
    carried: list[dict[str, Any]]       # user-ingested legal content, DISPLAYED not interpreted
    overlay: dict[str, Any]             # {grade, control_form, guarantees, floor, tightened_by_user}
    enforcement: dict[str, Any]         # {envelope, signatures} — the card's allow/disallow gate

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def enforce(self, *, candidate: dict[str, Any], text: Optional[str] = None,
                data: Optional[bytes] = None, filename: Optional[str] = None) -> dict[str, Any]:
        """A Policy Card is an enforceable gate: run its ``enforcement`` rules (envelope +
        signatures) over a candidate → an allow/hold/deny verdict, via the shared `card_gate`.
        No enforcement rules → allow (the card governs nothing at the ingress boundary)."""
        from . import card_gate
        return card_gate.enforce(self.enforcement, candidate=candidate, text=text,
                                 data=data, filename=filename)


@dataclass
class GroupNode:
    """One collapsed bar: a group key + the roll-up that makes 'collapsed' still informative."""
    key: str
    count: int
    empty: int
    interpreter: int
    prohibited: int
    furnished: int
    rule_ids: list[str]

    @property
    def gap_rank(self) -> tuple[int, int, int]:
        return (self.empty, self.interpreter, self.count)   # gaps-first ordering key

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["worst_status"] = ("empty" if self.empty else "interpreter" if self.interpreter
                             else "prohibited" if self.prohibited else "furnished")
        return d


#: the facets a client may group OR filter by — the one list the panel builds its chips from
#: and the op validates against, so no consumer invents an off-contract axis.
FACETS = ("room", "role", "risk", "status", "instrument", "demand")


@dataclass
class View:
    """A client's view state — and a deep-link. Every field is contract vocabulary, so a view
    round-trips to a URL and back: ``?group=room&sort=gaps&role=provider&focus=ai-act-art-9``."""
    group_by: str = "room"
    sort: str = "gaps"                                  # gaps | az | count
    filters: dict[str, Any] = field(default_factory=dict)   # {facet: value | [values]}
    focus: Optional[str] = None                         # a rule_id to reveal / scroll to

    @classmethod
    def parse(cls, raw: Optional[dict[str, Any]]) -> "View":
        raw = raw or {}
        return cls(group_by=raw.get("group_by", "room"), sort=raw.get("sort", "gaps"),
                   filters=dict(raw.get("filters", {})), focus=raw.get("focus"))


@dataclass
class GovernanceMap:
    rules: list[MapRule] = field(default_factory=list)

    # ---- summary roll-up (the top strip) ----
    def summary(self) -> dict[str, Any]:
        R = self.rules
        return {
            "total": len(R),
            "empty": sum(1 for r in R if r.coverage == "empty"),
            "interpreter": sum(1 for r in R if r.needs_interpreter),
            "prohibited": sum(1 for r in R if r.operator == "F"),
            "furnished": sum(1 for r in R if r.coverage == "furnished"),
            "may_apply": sum(1 for r in R if r.status == "may_apply"),
            "instruments": sorted({r.instrument for r in R}),
        }

    def _facet_key(self, r: MapRule, facet: str) -> str:
        if facet == "room":
            return r.room
        if facet == "role":
            return r.role or "(role-agnostic)"
        if facet == "risk":
            return r.risk_tier or "(any tier)"
        if facet == "instrument":
            return r.instrument
        if facet == "demand":
            return r.demand_type or "unclassified"
        if facet == "status":
            if r.coverage == "empty":
                return "empty — needs evidence"
            if r.needs_interpreter:
                return "interpreter — needs a read"
            if r.coverage == "furnished":
                return "furnished"
            return "unresolved"
        return "(all)"

    # ---- facets: what a client may group / filter by, and the values present ----
    def facet_values(self) -> dict[str, list[str]]:
        """Every facet and the group keys present — the panel builds its filter chips from THIS,
        never from a hardcoded list, so chips and grouping can never drift from the data."""
        return {f: sorted({self._facet_key(r, f) for r in self.rules}) for f in FACETS}

    # ---- filtering: restricted to the contract's facets (no off-contract axis) ----
    def filter(self, spec: dict[str, Any]) -> "GovernanceMap":
        """A sub-map keeping rows that match every facet constraint. ``spec`` maps a FACET to an
        allowed value or list of values. An unknown facet raises — nothing off-contract filters.
        Filtering reuses the same keying as grouping, so a filter chip and a group bar agree."""
        if not spec:
            return GovernanceMap(list(self.rules))
        norm: dict[str, set] = {}
        for f, v in spec.items():
            if f not in FACETS:
                raise ValueError(f"unknown filter facet {f!r}; one of {FACETS}")
            norm[f] = {v} if isinstance(v, str) else set(v)
        keep = [r for r in self.rules
                if all(self._facet_key(r, f) in vals for f, vals in norm.items())]
        return GovernanceMap(keep)

    def locate(self, rule_id: str, facet: str = "room") -> Optional[str]:
        """Which group key a rule falls under for a grouping — the deep-link scroll target."""
        for r in self.rules:
            if r.rule_id == rule_id:
                return self._facet_key(r, facet)
        return None

    # ---- group-by tree (the collapsible bars) ----
    def group_by(self, facet: str = "room", sort: str = "gaps") -> list[GroupNode]:
        if facet not in FACETS:
            raise ValueError(f"unknown facet {facet!r}; one of {FACETS}")
        buckets: dict[str, list[MapRule]] = {}
        for r in self.rules:
            buckets.setdefault(self._facet_key(r, facet), []).append(r)
        nodes = [GroupNode(
            key=k, count=len(v),
            empty=sum(1 for r in v if r.coverage == "empty"),
            interpreter=sum(1 for r in v if r.needs_interpreter),
            prohibited=sum(1 for r in v if r.operator == "F"),
            furnished=sum(1 for r in v if r.coverage == "furnished"),
            rule_ids=[r.rule_id for r in v],
        ) for k, v in buckets.items()]
        if sort == "az":
            nodes.sort(key=lambda n: n.key)
        elif sort == "count":
            nodes.sort(key=lambda n: (-n.count, n.key))
        else:  # gaps-first (default)
            nodes.sort(key=lambda n: (-n.empty, -n.interpreter, -n.count, n.key))
        return nodes

    def as_tree(self, facet: str = "room", sort: str = "gaps") -> dict[str, Any]:
        """The full contract the panel renders: version + summary + collapsible groups, each
        carrying its roll-up and its member rows."""
        by_id = {r.rule_id: r for r in self.rules}
        groups = [{"group": n.as_dict(),
                   "rules": [by_id[i].as_dict() for i in n.rule_ids]}
                  for n in self.group_by(facet, sort)]
        return {"version": SCHEMA_VERSION, "grouped_by": facet, "sorted_by": sort,
                "summary": self.summary(), "groups": groups}

    def as_dict(self) -> dict[str, Any]:
        """The flat contract: version + summary + every row (client does its own grouping)."""
        return {"version": SCHEMA_VERSION, "summary": self.summary(),
                "rules": [r.as_dict() for r in self.rules]}

    # ---- the one entrypoint the op and the panel both call ----
    def resolve(self, view: "View | dict | None" = None) -> dict[str, Any]:
        """Apply a View (filter → group → sort → focus) and return the complete panel/op payload:
        version · echoed view · full-map facets (for the chips) · filtered summary · groups · the
        resolved focus target. This is the SINGLE contract surface — the MCP op returns exactly
        this, and the panel renders exactly this, so neither can drift from the other.

        ``facets`` are computed on the full map (so a chip never vanishes just because the current
        filter excluded it); ``summary`` and ``groups`` reflect the FILTERED sub-map (what's on
        screen). ``focus`` resolves the deep-linked rule to its group under the active grouping
        (``group_key`` is None if the focused rule was filtered out — surfaced, not hidden)."""
        v = view if isinstance(view, View) else View.parse(view)
        sub = self.filter(v.filters)
        payload = sub.as_tree(v.group_by, v.sort)
        payload["view"] = {"group_by": v.group_by, "sort": v.sort,
                           "filters": v.filters, "focus": v.focus}
        payload["facets"] = self.facet_values()
        if v.focus:
            payload["focus_target"] = {"rule_id": v.focus,
                                       "group_key": sub.locate(v.focus, v.group_by)}
        return payload


def to_review_card(rule: MapRule) -> dict[str, Any]:
    """Project a rule into the uniform ``review_card`` contract — the Policy Card is not a new
    card shape, it is a ``review_card`` carrying a ``MapRule``. The rule's CTA becomes the
    review's override affordance; unread / may-apply / empty rules carry a reserved act, so the
    human-review status derives for free (reserved outranks completeness).

    completeness band drives status: unread/empty → 'low' (needs-review), may-apply → 'medium',
    else 'high' (auto). A CTA that opens the interpreter / confirms applicability is a reserved
    act (a human must act), whatever the certainty."""
    from . import review_card as _rc
    if rule.needs_interpreter or rule.coverage == "empty":
        band = "low"
    elif rule.status == "may_apply":
        band = "medium"
    else:
        band = "high"
    reserved = None
    if rule.cta.get("verb") in ("ratify", "confirm") or rule.needs_interpreter:
        reserved = {"handler": rule.cta.get("handler"), "cta": rule.cta, "rule_id": rule.rule_id}
    citations = [{"source": rule.source}]
    if rule.carried:                                    # user-ingested legal content, displayed
        citations += [{"carried": c} for c in rule.carried]
    return _rc.review_card(
        node_id=rule.rule_id, stage="policy-map",
        what=f"{rule.pinpoint}: {rule.duty}",
        why=f"{rule.instrument} · role={rule.role or 'any'} · demand={rule.demand_type or 'unread'}",
        citations=citations,
        signals={"completeness": band, "confidence": rule.confidence,
                 "coverage": rule.coverage, "resolution": rule.resolution},
        inputs=[{"pinpoint": rule.pinpoint, "gate": rule.gate_id, "overlay": rule.overlay,
                 "cta": rule.cta}],
        reserved_act=reserved,
    )


def build(*, provisions: Optional[list[dict[str, Any]]] = None,
          policy_text: Optional[str] = None, instrument: str = "policy",
          coverage: Optional[dict[str, dict[str, Any]]] = None,
          bindings: Optional[dict[str, dict[str, Any]]] = None,
          status_of: Optional[dict[str, str]] = None,
          currency_of: Optional[dict[str, str]] = None) -> "GovernanceMap":
    """Provisions/``policy_text`` → identified duties → a ``GovernanceMap``. The SHARED rule
    source for both ``serve()`` (the map contract) and the ``governance_kg`` op — one segmenter,
    one duty read — so the map and the KG can never disagree about what the rules are.
    ``provisions`` is ``[{pinpoint, text}]`` (a segmenter's output); else ``policy_text`` is
    segmented here (``legal_norm_splitter``, sentence-fallback for markerless paste)."""
    from . import duty_identification as _di
    prov = list(provisions or [])
    if not prov and policy_text:
        from .adapters.ingest.governance import legal_norm_splitter as _split
        segs = _split.segment_provisions(policy_text)
        if segs:
            for p in segs:
                prov.append({"pinpoint": getattr(p, "marker", "") or getattr(p, "pinpoint", ""),
                             "text": getattr(p, "text", "")})
        else:
            # markerless policy (what a user pastes) → one provision per sentence
            import re as _re
            for i, sent in enumerate(
                    (s.strip() for s in _re.split(r"(?<=[.;])\s+|\n+", policy_text) if s.strip()), 1):
                prov.append({"pinpoint": f"¶{i}", "text": sent})
    duties: list[Any] = []
    for p in prov:
        # All duties of a provision project — an Article carrying three duties is three rules.
        # (Keeping only got[0] silently dropped the rest, contradicting the map's own doctrine.)
        duties.extend(_di.identify_duties(p.get("text", ""), source=p.get("pinpoint", "")))
    return project(duties, instrument=instrument, coverage=coverage, bindings=bindings,
                   status_of=status_of, currency_of=currency_of)


def serve(view: "View | dict | None" = None, *,
          provisions: Optional[list[dict[str, Any]]] = None,
          policy_text: Optional[str] = None, instrument: str = "policy",
          coverage: Optional[dict[str, dict[str, Any]]] = None,
          bindings: Optional[dict[str, dict[str, Any]]] = None,
          status_of: Optional[dict[str, str]] = None,
          currency_of: Optional[dict[str, str]] = None,
          question: Optional[str] = None) -> dict[str, Any]:
    """The op's whole body: build the instrument's rules (``build()``), project, resolve a View →
    the contract payload. Everything downstream is the contract — the MCP op is a one-liner over
    this, so the op cannot emit anything off-contract."""
    gm = build(provisions=provisions, policy_text=policy_text, instrument=instrument,
               coverage=coverage, bindings=bindings, status_of=status_of, currency_of=currency_of)
    if question and view is None:
        # natural-language ask → a View (on-contract), resolved deterministically
        from . import governance_ask as _ask
        view = _ask.parse(question, facet_values=gm.facet_values())
    payload = gm.resolve(view)
    if question:
        payload["question"] = question           # echo the run query (auditable)
    return payload


def project(
    duties: list[Any], *, instrument: str,
    room_of: Optional[Callable[[Any], str]] = None,
    step_of: Optional[dict[str, str]] = None,
    coverage: Optional[dict[str, dict[str, Any]]] = None,
    bindings: Optional[dict[str, dict[str, Any]]] = None,
    status_of: Optional[dict[str, str]] = None,
    currency_of: Optional[dict[str, str]] = None,
    demand_of: Optional[Callable[[Any], str]] = None,
    secondary_of: Optional[dict[str, list[dict[str, Any]]]] = None,
    carried_of: Optional[dict[str, list[dict[str, Any]]]] = None,
    overlay_of: Optional[dict[str, dict[str, Any]]] = None,
    enforcement_of: Optional[dict[str, dict[str, Any]]] = None,
) -> GovernanceMap:
    """Assemble a GovernanceMap from duty reads + OPTIONAL upstream facts.

    ``duties`` are duck-typed duty reads (``duty_identification.IdentifiedDuty`` or any object
    with ``source/action/operator/role/risk_tier/areas/needs_interpreter/origin/confidence``).
    Everything else is optional and keyed by ``rule_id`` (= ``_rule_id(instrument, pinpoint)``):
    ``coverage[rid] = {coverage, artifacts}`` from evidence_coverage; ``bindings[rid] =
    {gate_id, verdict, risk_floor, allowed_agents}`` from the applied loom patch; ``status_of``
    from the matcher; ``currency_of`` from temporal. Absent inputs leave the field null/n-a."""
    from . import demand_cta as _dc
    steps = {**_role_steps(), **(step_of or {})}
    coverage = coverage or {}
    bindings = bindings or {}
    status_of = status_of or {}
    currency_of = currency_of or {}
    secondary_of = secondary_of or {}
    carried_of = carried_of or {}
    overlay_of = overlay_of or {}
    enforcement_of = enforcement_of or {}
    rows: list[MapRule] = []
    _seen_rids: dict[str, int] = {}
    for d in duties:
        pinpoint = getattr(d, "source", "") or ""
        rid = _rule_id(instrument, pinpoint)
        # a provision may carry SEVERAL duties → several rows share a pinpoint; suffix the
        # rule_id so locate()/deep-links stay unambiguous while nothing is dropped.
        _n = _seen_rids.get(rid, 0) + 1
        _seen_rids[rid] = _n
        if _n > 1:
            rid = f"{rid}-{_n}"
        origin = getattr(d, "origin", "deterministic")
        needs = bool(getattr(d, "needs_interpreter", False))
        resolution = ("ratified" if origin == "interpreter-ratified"
                      else "interpreter" if needs else "deterministic")
        role = getattr(d, "role", None)
        cov = coverage.get(rid, {})
        bind = bindings.get(rid, {})
        operator = getattr(d, "operator", "") or "?"
        # what to do about it — demand (unread rules are unclassified: the CTA will be "ratify")
        demand_type = "" if needs else (demand_of(d) if demand_of else _dc.classify_demand(d))
        cta = _dc.cta_for(demand_type=demand_type or "record", operator=operator,
                          coverage=cov.get("coverage", "n/a"), needs_interpreter=needs,
                          status=status_of.get(rid))
        ov = overlay_of.get(rid)
        overlay = _dc.overlay_effective(ov.get("floor", {}), ov.get("user")) if ov else {}
        rows.append(MapRule(
            rule_id=rid, pinpoint=pinpoint, instrument=instrument,
            duty=getattr(d, "action", "") or "(unread — interpreter)",
            operator=operator,
            room=(room_of(d) if room_of else _classify_room(d)),
            role=role, step=steps.get(role) if role else None,
            risk_tier=getattr(d, "risk_tier", None), areas=list(getattr(d, "areas", []) or []),
            resolution=resolution, confidence=float(getattr(d, "confidence", 0.0) or 0.0),
            needs_interpreter=needs,
            gate_id=bind.get("gate_id"), verdict=bind.get("verdict"),
            risk_floor=bind.get("risk_floor"), allowed_agents=list(bind.get("allowed_agents", [])),
            coverage=cov.get("coverage", "n/a"), artifacts=list(cov.get("artifacts", [])),
            status=status_of.get(rid), currency=currency_of.get(rid, "current"),
            source={"instrument": instrument, "pinpoint": pinpoint},
            demand_type=demand_type, secondary=list(secondary_of.get(rid, [])),
            cta=cta, carried=list(carried_of.get(rid, [])), overlay=overlay,
            enforcement=dict(enforcement_of.get(rid, {})),
        ))
    return GovernanceMap(rules=rows)

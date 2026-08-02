# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Capability resolver — the registration-time dependency closure.

Three routers already exist (see DECISION notes): the content→ND classifier
(`nd_routing`), the query→skill router (`workspace_router`), and the import readiness
report (`capability_ir.readiness_report`). The last resolves *skill* and
*connector* deps. None computes the FULL capability closure a vertical/skill
needs **before it is registered** — which NDs must be registered (rule, math),
which substrate registers/packs, and whether a code-execution **python sandbox**
is required.

This module closes that. Given a vertical/skill's declared parts plus a sample
of its content, it:

  1. INFERS required capabilities using the *same classifier signals that route
     content* — normative text ⇒ the rule/deontic ND (+ currency + a legal-system
     pack for dated law); mathematical content ⇒ the math ND (+ a python sandbox);
     code ⇒ a python sandbox.
  2. adds DECLARED capabilities (declared instrument NDs; IR step `requires` →
     skills/connectors).
  3. RESOLVES each against a :class:`Host` (what the substrate + this machine
     actually provide).
  4. returns a manifest: per capability → satisfied / missing, with the reason it
     was required and how to satisfy it.

A registry gate (G8) blocks registration when a *required* capability is
unsatisfiable — so "this vertical needs the rule-ND register / the math register
/ a python sandbox" is made sure at install, not discovered at runtime.

Pure stdlib + workspaces internals; no cloud calls.

Internal by design: consulted at skill registration; no operator surface of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..nd_routing import DefaultClassifier, score_normative, NORMATIVE_THRESHOLD
from .. import legal_systems as _ls


# Capability ids the catalogue knows about. kind drives how it is satisfied.
#   nd:*         — a Nightingale-D register must be present (substrate or vertical)
#   substrate:*  — a substrate mechanism (always present in the runtime)
#   pack:*       — a switchable substrate pack (legal system, etc.)
#   runtime:*    — an execution capability of THIS machine (e.g. python sandbox)
#   skill:* / connector:* — an external skill/connector the host must provide
@dataclass(frozen=True)
class CapabilityInfo:
    kind: str
    satisfy: str            # human-readable "how to satisfy"


CATALOGUE: dict[str, CapabilityInfo] = {
    "nd:rule":            CapabilityInfo("nd", "register the rule/deontic ND (domain_nds)"),
    "nd:math":            CapabilityInfo("nd", "register the math ND (domain_nds.MathND)"),
    "nd:contract":        CapabilityInfo("nd", "register the contract ND"),
    "substrate:currency": CapabilityInfo("substrate", "currency validity pipeline (built-in)"),
    "pack:legal-system":  CapabilityInfo("pack", "select a legal_systems pack (DE/EU/UK/US)"),
    "runtime:python-sandbox": CapabilityInfo("runtime", "a sandboxed Python execution runtime"),
}


@dataclass
class Host:
    """What is actually available to satisfy capabilities. ``None`` for skills/
    connectors means 'unknown — report as a requirement, never assert satisfied'
    (same honesty rule as readiness_report)."""
    registered_nds: set[str] = field(default_factory=lambda: {"nd:rule", "nd:math", "nd:contract"})
    available_packs: set[str] = field(default_factory=lambda: {"pack:legal-system"} if _ls.available() else set())
    substrate: set[str] = field(default_factory=lambda: {"substrate:currency"})
    has_python_sandbox: bool = False        # host-dependent; default not assumed
    available_skills: Optional[set[str]] = None
    available_connectors: Optional[set[str]] = None

    def satisfies(self, cap_id: str) -> bool:
        kind = cap_id.split(":", 1)[0]
        if kind == "nd":
            return cap_id in self.registered_nds
        if kind == "pack":
            return cap_id in self.available_packs
        if kind == "substrate":
            return cap_id in self.substrate
        if kind == "runtime":
            return cap_id == "runtime:python-sandbox" and self.has_python_sandbox
        if kind == "skill":
            return self.available_skills is not None and cap_id.split(":", 1)[1] in self.available_skills
        if kind == "connector":
            return self.available_connectors is not None and cap_id.split(":", 1)[1] in self.available_connectors
        return False


# ND capabilities the substrate can auto-provision because the ND class exists
# on disk (domain_nds) — "an optional ND that exists but isn't loaded". These
# never block: they are registered on demand at install.
PROVISIONABLE_NDS: frozenset[str] = frozenset({"nd:rule", "nd:math", "nd:contract"})


def _nd_classes_for(cap_id: str) -> list:
    """The domain-ND classes that provide a capability (empty if none exist)."""
    from .. import domain_nds as dn
    table = {
        "nd:rule": [dn.GDPRRuleND, dn.AIActRuleND, dn.MusicRightsRuleND, dn.ContractRuleND],
        "nd:math": [dn.MathND],
        "nd:contract": [dn.ContractRuleND],
    }
    return [c for c in table.get(cap_id, []) if c is not None]


@dataclass
class Requirement:
    cap_id: str
    reasons: list[str]
    status: str             # "satisfied" | "provisionable" | "missing"
    how: str

    def to_dict(self) -> dict[str, Any]:
        return {"cap_id": self.cap_id, "status": self.status,
                "reasons": self.reasons, "how": self.how}


def infer_capabilities(sample_text: Optional[str]) -> dict[str, list[str]]:
    """Infer required capabilities from a content sample, reusing the content
    router's own signals. Returns ``{cap_id: [reasons]}``."""
    need: dict[str, list[str]] = {}

    def add(cap: str, why: str):
        need.setdefault(cap, [])
        if why not in need[cap]:
            need[cap].append(why)

    if not sample_text:
        return need
    score, _ = score_normative(sample_text)
    cls = DefaultClassifier().classify(sample_text)
    pt = cls.primary_type
    if score >= NORMATIVE_THRESHOLD or pt in ("normative", "contract", "policy"):
        add("nd:rule", f"normative content (score={score:.2f}, type={pt})")
        add("substrate:currency", "dated legal norms need ratione-temporis dating")
        add("pack:legal-system", "legal vocabulary/conflict rules are jurisdiction-specific")
    if pt == "math":
        add("nd:math", "mathematical content detected")
        add("runtime:python-sandbox", "math typically needs deterministic computation")
    if pt == "code":
        add("runtime:python-sandbox", "code content needs an execution runtime")
    return need


def resolve(*, instrument_nds: Optional[Iterable[dict]] = None,
            ir_requires: Optional[Iterable[str]] = None,
            sample_text: Optional[str] = None,
            host: Optional[Host] = None) -> list[Requirement]:
    """Compute the capability closure and resolve it against ``host``."""
    host = host or Host()
    need = infer_capabilities(sample_text)

    def add(cap: str, why: str):
        need.setdefault(cap, [])
        if why not in need[cap]:
            need[cap].append(why)

    # Declared instrument NDs.
    for nd in (instrument_nds or []):
        name = (nd.get("nd") or "").lower()
        cap = "nd:math" if "math" in name else ("nd:contract" if "contract" in name else "nd:rule")
        add(cap, f"declared instrument ND {nd.get('nd')!r}")
    # IR step requirements: connector ids (and skill refs) carried by the spec.
    for req in (ir_requires or []):
        cap = req if (":" in req and req.split(":", 1)[0] in ("skill", "connector")) else f"connector:{req}"
        add(cap, "declared by an IR step `requires`")

    out: list[Requirement] = []
    for cap_id, reasons in sorted(need.items()):
        info = CATALOGUE.get(cap_id)
        how = info.satisfy if info else (f"provide {cap_id}")
        if host.satisfies(cap_id):
            status = "satisfied"
        elif cap_id in PROVISIONABLE_NDS:
            status = "provisionable"        # exists on disk; auto-register at install
        else:
            status = "missing"              # external (sandbox, connector) — must block
        out.append(Requirement(cap_id, reasons, status, how))
    return out


def missing(manifest: Iterable[Requirement]) -> list[Requirement]:
    """Truly unsatisfiable — external capabilities that cannot be auto-provisioned."""
    return [r for r in manifest if r.status == "missing"]


def provisionable(manifest: Iterable[Requirement]) -> list[Requirement]:
    return [r for r in manifest if r.status == "provisionable"]


def provision_into(manifest: Iterable[Requirement], router) -> list[str]:
    """Auto-register every provisionable ND into ``router`` (an NDRouter). Returns
    the capability ids actually provisioned. This is the self-heal: an optional ND
    that exists but isn't loaded is registered on demand rather than blocking."""
    done: list[str] = []
    for r in manifest:
        if r.status != "provisionable":
            continue
        classes = _nd_classes_for(r.cap_id)
        if not classes:
            continue
        for cls in classes:
            router.register(cls())
        done.append(r.cap_id)
    return done

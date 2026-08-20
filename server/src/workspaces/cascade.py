# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Verifier-gated model cascade — local first, cloud only on a real deferral.

The economic claim of the local-model stack: a workplace agent (the cloud
model driving the session) should NOT spend its tokens on standard tasks a
small local model can do. This module is the router that makes that true,
built to the bar a cascade should meet rather than the naive "try local then
cloud":

  1. **Deferral is decided by a VERIFIER on the output, not by the small
     model's self-reported confidence.** Small models are badly calibrated;
     the robust signal is whether the output passes a cheap deterministic
     check (parses, satisfies the task's invariants). A tier's answer is
     accepted only if its verifier passes; otherwise the cascade defers to
     the next tier. (Cascade / learning-to-defer / selective-prediction;
     cf. FrugalGPT.)
  2. **Cloud is the last resort and is GOVERNED.** Before any cloud hop the
     prompt is screened by the Shield (PII/confidential never escalates
     unminimised); if the screen is unavailable the cloud hop is refused, not
     risked. Every tier decision is recorded for audit.
  3. **The economics are measured honestly.** The ledger reports tokens and
     estimated cost per tier, the share handled locally (the agent-token
     saving), AND the verifier's accept/defer counts — so a savings figure
     can never be read without the quality signal beside it.

No brokered cloud credential configured ⇒ the cascade still runs local tiers and, if all
defer, returns the best local attempt marked ``escalation_withheld`` (it never
silently invents a cloud answer). Pure stdlib + the existing local_llm
transport; deterministic given the same tier responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import local_llm

__all__ = ["Tier", "Verifier", "CascadeResult", "run_cascade",
           "always_accept", "nonempty_verifier"]


# ── tiers ─────────────────────────────────────────────────────────────────────

@dataclass
class Tier:
    """One rung of the cascade. ``is_cloud`` rungs are Shield-gated and only
    tried when a key is present. ``price_per_1k`` drives the cost ledger
    (USD per 1k total tokens; indicative, editable — not a quote)."""
    name: str
    url: str
    model: str
    api_key: str = ""
    is_cloud: bool = False
    price_per_1k: float = 0.0
    timeout: float = 30.0
    proxy_url: str = ""
    capability_token: str = ""
    track_id: str = ""

    @property
    def configured(self) -> bool:
        if self.is_cloud:
            return bool(
                self.url and self.model and self.proxy_url
                and self.capability_token and self.track_id
            )
        return bool(self.url and self.model)


# ── verifiers ─────────────────────────────────────────────────────────────────
# A verifier inspects (prompt, response_text) and returns
# (accepted, reason, score|None). It is the deferral gate — cheap and
# deterministic. Reuse workspaces' real checkers (norm_contract, the eval scorer,
# the grounder) as verifiers for real tasks; these two are the primitives.

Verifier = Callable[[str, str], "tuple[bool, str, Optional[float]]"]


def always_accept(prompt: str, response: str):
    return True, "no verification requested", None


def nonempty_verifier(prompt: str, response: str):
    """Floor verifier: a non-empty, non-refusal answer. Real tasks should pass
    a stricter one (schema/groundedness/label-set)."""
    r = (response or "").strip()
    if not r:
        return False, "empty response", 0.0
    low = r.lower()
    if low.startswith(("i cannot", "i can't", "i'm sorry", "as an ai")):
        return False, "refusal / non-answer", 0.0
    return True, "non-empty answer", 1.0


# ── result + ledger ────────────────────────────────────────────────────────────

@dataclass
class TierAttempt:
    tier: str
    is_cloud: bool
    ran: bool
    accepted: bool
    reason: str
    tokens: int = 0
    cost: float = 0.0
    price_per_1k: float = 0.0            # the tier's configured rate (even if it never ran)
    latency_ms: int = 0
    score: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "tier", "is_cloud", "ran", "accepted", "reason", "tokens",
            "cost", "price_per_1k", "latency_ms", "score", "note")}


@dataclass
class CascadeResult:
    ok: bool
    response: str
    served_by: str                       # tier name, or "" if none accepted
    served_is_cloud: bool
    attempts: list[TierAttempt] = field(default_factory=list)
    escalation_withheld: bool = False    # all local deferred, no broker credential/Shield
    error: str = ""

    # ── the economics, computed from attempts ──
    def ledger(self, *, cloud_price_per_1k: Optional[float] = None) -> dict[str, Any]:
        """Honest cost/quality ledger. The headline saving is the share of
        work served locally — the tokens the cloud agent did NOT spend. The
        counterfactual is 'cloud did the whole task itself': the accepted
        answer's token volume priced at the cloud rate."""
        local_tokens = sum(a.tokens for a in self.attempts if a.ran and not a.is_cloud)
        cloud_tokens = sum(a.tokens for a in self.attempts if a.ran and a.is_cloud)
        actual_cost = sum(a.cost for a in self.attempts if a.ran)
        served = next((a for a in self.attempts if a.accepted), None)
        # Counterfactual: the served answer's tokens, all at the cloud rate.
        # Use the cloud tier's CONFIGURED price (carried on the attempt even
        # when the cloud tier never ran — which is the win we're pricing).
        cf_rate = cloud_price_per_1k
        if cf_rate is None:
            cloud_rates = [a.price_per_1k for a in self.attempts if a.is_cloud]
            cf_rate = max(cloud_rates) if cloud_rates else 0.0
        served_tokens = served.tokens if served else 0
        cloud_only_cost = (served_tokens / 1000.0) * cf_rate if cf_rate else 0.0
        ran = [a for a in self.attempts if a.ran]
        accepted_local = bool(served and not served.is_cloud)
        return {
            "served_by": self.served_by,
            "served_is_cloud": self.served_is_cloud,
            "accepted_locally": accepted_local,
            "local_tokens": local_tokens,
            "cloud_tokens": cloud_tokens,
            "actual_cost": round(actual_cost, 6),
            "cloud_only_cost_estimate": round(cloud_only_cost, 6),
            "estimated_saving": round(max(0.0, cloud_only_cost - actual_cost), 6),
            "agent_tokens_offloaded_to_local": local_tokens if accepted_local else 0,
            # quality signal that MUST travel with the saving:
            "tiers_run": len(ran),
            "local_deferrals": sum(1 for a in ran if not a.is_cloud and not a.accepted),
            "escalated_to_cloud": any(a.ran and a.is_cloud for a in self.attempts),
            "escalation_withheld": self.escalation_withheld,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "response": self.response,
                "served_by": self.served_by, "served_is_cloud": self.served_is_cloud,
                "escalation_withheld": self.escalation_withheld, "error": self.error,
                "attempts": [a.to_dict() for a in self.attempts],
                "ledger": self.ledger()}


# ── the Shield gate before a cloud hop ────────────────────────────────────────

def _default_shield(prompt: str) -> "tuple[str, str]":
    """Screen a prompt before it leaves for a cloud tier. Returns
    (action, text): action ∈ {allow, minimise, refuse, unavailable}.
    Reuses the existing Lock; if the Lock is unavailable, returns
    ``unavailable`` and the caller refuses the cloud hop (fail-closed)."""
    try:
        from .lock_classify import _lock_string
        d = _lock_string(prompt, context="cascade-egress")
        action = d.get("action", "refuse")
        if action == "allow":
            return "allow", prompt
        if action == "minimise":
            return "minimise", d.get("text") or ""
        return "refuse", ""
    except Exception:                                       # noqa: BLE001
        return "unavailable", ""


# ── the cascade ────────────────────────────────────────────────────────────────

def run_cascade(
    prompt: str,
    tiers: list[Tier],
    *,
    verifier: Verifier = nonempty_verifier,
    shield: Callable[[str], "tuple[str, str]"] = _default_shield,
    allow_unscreened_cloud: bool = False,
    max_tokens: int = 512,
    temperature: float = 0.0,
    completer: Callable[..., dict[str, Any]] = local_llm.complete_via,
) -> CascadeResult:
    """Run ``prompt`` down the tiers, accepting the first verifier-passing
    answer. Local tiers run in order; cloud tiers are Shield-gated. Returns a
    :class:`CascadeResult` with the full per-tier trace and the economics
    ledger. ``completer`` is injectable for testing (defaults to the real
    OpenAI-compatible transport)."""
    res = CascadeResult(ok=False, response="", served_by="", served_is_cloud=False)
    best_local = None                       # remember best local attempt for withheld case

    for idx, tier in enumerate(tiers):
        if not tier.configured:
            res.attempts.append(TierAttempt(
                tier=tier.name, is_cloud=tier.is_cloud, ran=False, accepted=False,
                reason="not configured", price_per_1k=tier.price_per_1k, note="skipped"))
            continue

        send_prompt = prompt
        if tier.is_cloud:
            action, screened = shield(prompt)
            if action == "refuse":
                res.attempts.append(TierAttempt(
                    tier=tier.name, is_cloud=True, ran=False, accepted=False,
                    reason="shield refused egress — confidential content",
                    price_per_1k=tier.price_per_1k, note="cloud hop blocked by Shield"))
                continue
            if action == "unavailable" and not allow_unscreened_cloud:
                res.attempts.append(TierAttempt(
                    tier=tier.name, is_cloud=True, ran=False, accepted=False,
                    reason="shield unavailable — cloud hop refused (fail-closed)",
                    price_per_1k=tier.price_per_1k, note="set allow_unscreened_cloud to override (not advised)"))
                continue
            if action == "minimise":
                send_prompt = screened

        if tier.is_cloud:
            upstream = urlparse(tier.url).hostname or ""
            out = completer(
                tier.proxy_url,
                tier.model,
                send_prompt,
                api_key="",
                extra_headers={
                    "X-Lock-Upstream": upstream,
                    "X-Lock-Track": tier.track_id,
                    "X-Rvnd-Capability": tier.capability_token,
                },
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=tier.timeout,
            )
        else:
            out = completer(
                tier.url, tier.model, send_prompt, api_key=tier.api_key,
                temperature=temperature, max_tokens=max_tokens,
                timeout=tier.timeout,
            )
        if not out.get("ok"):
            res.attempts.append(TierAttempt(
                tier=tier.name, is_cloud=tier.is_cloud, ran=True, accepted=False,
                reason=f"call failed: {out.get('error', 'unknown')}",
                price_per_1k=tier.price_per_1k,
                latency_ms=int(out.get("latency_ms", 0)), note="error"))
            continue

        text = out.get("response", "")
        usage = out.get("usage", {}) or {}
        tokens = int(usage.get("total_tokens")
                     or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)))
        cost = (tokens / 1000.0) * tier.price_per_1k
        accepted, reason, score = verifier(prompt, text)
        att = TierAttempt(
            tier=tier.name, is_cloud=tier.is_cloud, ran=True, accepted=accepted,
            reason=reason, tokens=tokens, cost=cost, price_per_1k=tier.price_per_1k,
            latency_ms=int(out.get("latency_ms", 0)), score=score)
        res.attempts.append(att)

        if accepted:
            res.ok = True
            res.response = text
            res.served_by = tier.name
            res.served_is_cloud = tier.is_cloud
            # Record the tiers we didn't need to reach — so the trace and the
            # cost counterfactual (the cloud rate we avoided) are self-contained.
            for later in tiers[idx + 1:]:
                res.attempts.append(TierAttempt(
                    tier=later.name, is_cloud=later.is_cloud, ran=False,
                    accepted=False, reason="not reached — earlier tier accepted",
                    price_per_1k=later.price_per_1k, note="unreached"))
            return res
        if not tier.is_cloud and best_local is None:
            best_local = (tier.name, text)

    # Nothing accepted. If a cloud tier never got to run (no key / shield),
    # surface the best local attempt rather than fabricate — withheld, honest.
    cloud_ran = any(a.ran and a.is_cloud for a in res.attempts)
    if not cloud_ran and best_local is not None:
        res.escalation_withheld = True
        res.response = best_local[1]
        res.served_by = best_local[0]
        res.error = ("all local tiers deferred and no cloud tier was available "
                     "(no key or Shield blocked); returning best local attempt "
                     "unverified — escalate manually")
        return res
    res.error = "all tiers ran and none passed the verifier"
    return res

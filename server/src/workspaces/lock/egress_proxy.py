# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Enforced egress proxy — the gate between agent and cloud LLM.

The MCP middleware is advisory: a cloud-LLM agent can ignore it. To actually
*enforce* that no unapproved data reaches a cloud LLM, the agent's outbound
HTTP traffic must funnel through this proxy, which:

  1. Intercepts every request to api.anthropic.com / api.openai.com
  2. Parses the prompt content out of the request body
  3. Runs the tier-cascade scan (A/B/C) on the prompt
  4. Applies the configured oversight policy per finding
  5. Forwards (optionally modified) request to the upstream LLM
  6. Audit-logs every decision

Combined with OS-level egress filtering (nftables / pf / Windows Firewall)
that blocks direct connections to LLM provider endpoints — only allowing
the local proxy to reach them — the agent has no path to the cloud except
through this gate. That's the load-bearing privacy guarantee.

Configuration:

    AGENT_TOOL_LOCK_PROXY_PORT       — listen port (default 8443)
    AGENT_TOOL_LOCK_PROXY_OVERSIGHT  — default oversight level
    AGENT_TOOL_LOCK_PROXY_UPSTREAM   — comma-separated allowed upstream hosts
    AGENT_TOOL_LOCK_AUDIT_LOG        — audit log path (inherits global config)
    AGENT_TOOL_LOCK_PROXY_TRACK_FOLDER    — workspace folder → broker mode
    AGENT_TOOL_LOCK_PROXY_TRACK_LOG_ROOT  — chain log root for that folder

Pointing an agent at the proxy:

    ANTHROPIC_BASE_URL=http://localhost:8443   # for Anthropic SDK
    OPENAI_BASE_URL=http://localhost:8443/v1   # for OpenAI SDK

Broker mode (per-track credential injection): when bound to a workspace folder,
every request must declare its egress track via ``X-Lock-Track: <connector_id>``
(set once through the SDK's default-headers mechanism). The proxy resolves the
track's ``credential_ref`` from the folder's signed chain, strips the client's
credential headers, and injects the resolved secret into the upstream's own
credential header — the agent never holds the key, and a track with no cable,
an unresolvable reference, or a deny floor cannot egress (fail-closed). See
``track_broker.py``.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional


# Whole-operation deadline (connect + read) on the upstream forward. Default
# 60s; overridable so a stuck provider is bounded to the operator's tolerance
# rather than a hardcoded minute, and so tests can drive a real hang without
# waiting the full default. Mirrors WORKSPACE_LOCAL_LLM_TIMEOUT_SECS on the
# local-model path.
def _egress_timeout_secs() -> float:
    raw = os.environ.get("WORKSPACE_EGRESS_TIMEOUT_SECS", "").strip()
    if not raw:
        return 60.0
    try:
        val = float(raw)
    except ValueError:
        return 60.0
    return val if val > 0 else 60.0


# Upper bound on concurrently in-flight forwards. ThreadingHTTPServer spawns an
# unbounded worker thread per connection; under a sustained upstream outage each
# forward blocks up to WORKSPACE_EGRESS_TIMEOUT_SECS holding a thread, so a flood
# of requests during the outage becomes an unbounded-thread self-DoS (RV-09). We
# cap concurrency and SHED excess with a 503 — fail-closed, a clear operator
# signal, deliberately NOT a silent queue. Default 64 is well above any real
# single-agent forward rate, so normal operation is unaffected; a non-positive or
# unparseable override falls back to the default.
def _egress_max_concurrency() -> int:
    raw = os.environ.get("WORKSPACE_EGRESS_MAX_CONCURRENCY", "").strip()
    if not raw:
        return 64
    try:
        val = int(raw)
    except ValueError:
        return 64
    return val if val > 0 else 64

from .core import Finding, Mode, AuditLog, _redact_text_with_regex
from .core import tier_a_check_arguments, tier_b_scan_text
from .tier_c import tier_c_check_semantic
from .oversight import OversightLevel
from .onboarding.config import load_config, apply_config_to_env
from .gate import GateDecision, gate_for_cloud
from .decisions import DecisionsStore
from . import host_deps


_DEFAULT_PORT = 8443
_DEFAULT_OVERSIGHT = OversightLevel.APPROVE

# Upstream LLM providers — only these hosts are allowed.
_ALLOWED_UPSTREAMS = {
    "api.anthropic.com": "https://api.anthropic.com",
    "api.openai.com": "https://api.openai.com",
    "api.cohere.ai": "https://api.cohere.ai",
    "generativelanguage.googleapis.com": "https://generativelanguage.googleapis.com",
}

# D4 — bind the credential to its upstream. The upstream is chosen from a CALLER
# header, but each provider authenticates with a distinct credential header; without
# binding, a key issued for one provider could be forwarded to another allowlisted
# host (a cross-provider key leak). Every request header that carries a secret:
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "x-goog-api-key", "api-key"})
# ...and the credential header(s) each upstream is allowed to receive. A request
# carrying a credential header NOT in its upstream's set is refused (fail-closed).
_UPSTREAM_CREDENTIALS = {
    "api.anthropic.com": frozenset({"x-api-key"}),
    "api.openai.com": frozenset({"authorization"}),
    "api.cohere.ai": frozenset({"authorization"}),
    "generativelanguage.googleapis.com": frozenset({"x-goog-api-key"}),
}

# Broker mode — how each upstream receives an injected per-track credential:
# (header name, value template). The template keeps prefix conventions (Bearer)
# out of the stored reference, so a ref resolves to the bare key for every
# provider. An upstream absent here cannot be brokered — refused fail-closed,
# the injection-side counterpart of the D4 binding above.
_UPSTREAM_INJECT = {
    "api.anthropic.com": ("x-api-key", "{secret}"),
    "api.openai.com": ("Authorization", "Bearer {secret}"),
    "api.cohere.ai": ("Authorization", "Bearer {secret}"),
    "generativelanguage.googleapis.com": ("x-goog-api-key", "{secret}"),
}


def _credential_binding_violation(headers, upstream_host: str) -> Optional[str]:
    """Return the name of a credential header present in ``headers`` that the chosen
    ``upstream_host`` is NOT allowed to receive (a cross-provider key would leak), or
    None if every credential present is bound to this upstream. Fail-closed: an
    upstream with no declared credential set accepts none."""
    allowed = _UPSTREAM_CREDENTIALS.get(upstream_host, frozenset())
    for name in headers.keys():
        low = name.lower()
        if low in _CREDENTIAL_HEADERS and low not in allowed:
            return low
    return None


# ===========================================================================
# Approval callback contract
# ===========================================================================


ApprovalCallback = Callable[["PendingRequest"], "ApprovalDecision"]


@dataclass
class ApprovalDecision:
    """User's decision on a pending outbound LLM request."""

    action: str       # "allow" | "block" | "modify"
    modified_body: bytes | None = None   # if action == "modify"
    reason: str = ""
    waived_findings: list[str] = field(default_factory=list)


@dataclass
class PendingRequest:
    """One outbound LLM request awaiting decision."""

    request_id: str
    upstream_host: str
    method: str
    path: str
    body: bytes
    extracted_text: str        # the prompt/messages content scanned
    findings: list[Finding]
    oversight: OversightLevel
    timestamp: float = field(default_factory=time.time)


# ===========================================================================
# Prompt extraction — handle Anthropic + OpenAI message shapes
# ===========================================================================


def extract_prompt_text(host: str, body: bytes) -> str:
    """Pull the natural-language prompt content out of a provider-specific request body.

    Supports:
      - Anthropic Messages API: {"messages": [{"role": ..., "content": ...}]}
      - OpenAI Chat Completions: {"messages": [{"role": ..., "content": ...}]}
      - OpenAI Completions (legacy): {"prompt": "..."}

    Returns concatenated text content. Returns empty string if body can't be
    parsed — the request will be blocked at upstream forwarding because we
    can't verify what's in it.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""

    parts: list[str] = []

    # Legacy OpenAI completions
    if isinstance(payload.get("prompt"), str):
        parts.append(payload["prompt"])
    elif isinstance(payload.get("prompt"), list):
        parts.extend(str(p) for p in payload["prompt"])

    # System prompt (Anthropic string or structured content-block array)
    parts.extend(_block_strings(payload.get("system")))

    # Messages array — both providers use the same shape
    messages = payload.get("messages") or []
    for msg in messages:
        parts.extend(_block_strings(msg.get("content")))

    return "\n\n".join(p for p in parts if p)


def _block_strings(node: Any) -> list[str]:
    """Collect every natural-language string from a message-content node.

    Handles plain strings, content-block arrays, and the block shapes the old
    text-only extractor MISSED — ``tool_result`` (``content`` str or nested
    block list), ``tool_use`` (``input`` dict values), and document/source text
    — so PII riding in those blocks is scanned, not waved through. Anything not
    understood contributes nothing; the caller fail-closes when a non-empty body
    yields no scannable text.
    """
    out: list[str] = []
    if node is None:
        return out
    if isinstance(node, str):
        out.append(node)
        return out
    if isinstance(node, list):
        for b in node:
            out.extend(_block_strings(b))
        return out
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            out.append(node["text"])
        if "content" in node:                       # tool_result content
            out.extend(_block_strings(node["content"]))
        if "input" in node:                          # tool_use args (arbitrary nesting)
            out.extend(_all_strings(node["input"]))
        src = node.get("source")                     # document / source blocks
        # (defined below: _all_strings) walks arbitrary tool-arg structures.
        if isinstance(src, dict) and isinstance(src.get("data"), str):
            # Only treat TEXT documents as scannable. Image/binary base64 is not
            # natural-language text — extracting it would give a false sense of
            # scanning AND defeat the fail-closed path, so leave it out: an
            # image-only body then yields no scannable text and is refused.
            mt = str(src.get("media_type", ""))
            if not mt or mt.startswith("text"):
                out.append(src["data"])
    return out


def _all_strings(obj: Any) -> list[str]:
    """Every string anywhere in an arbitrarily-nested structure — used for
    tool_use ``input`` (free-form tool args with unknown keys), where the
    targeted block walk can't know the field names."""
    if isinstance(obj, str):
        return [obj]
    # Numbers/bools are DATA, not natural-language text. Stringifying them would
    # (a) make the redactor (which can't mutate ints in place) leave numeric PII
    # unredacted on the minimise path, and (b) let a numeric tool-arg satisfy the
    # fail-closed "has scannable text" check for an otherwise image-only body.
    # So they contribute no scannable text. (String-form ids/SSNs still scan.)
    if isinstance(obj, list):
        return [s for x in obj for s in _all_strings(x)]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _all_strings(v)]
    return []


# ===========================================================================
# Scanning
# ===========================================================================


def scan_prompt(text: str) -> list[Finding]:
    """Run Tier B/C cascade on prompt text. Returns findings.

    Kept for backwards compatibility with existing callers that only want
    findings. New code should use ``gate_prompt()`` which also handles
    confidential-context loading, persisted decisions, and oversight policy.
    """
    findings: list[Finding] = []
    findings.extend(tier_b_scan_text(text))
    findings.extend(tier_c_check_semantic(text))
    return findings


def _egress_policy_folder(proxy) -> str | None:
    """The folder to compose workspace-hierarchy policy against, or ``None`` to
    skip (the default — so existing behaviour is unchanged). Opt-in via
    ``RVND_EGRESS_POLICY``; when on, HOST-LEVEL by default (the process cwd →
    global-default policy) unless a workspace is bound, which only REFINES it."""
    if os.environ.get("RVND_EGRESS_POLICY", "").strip().lower() not in (
            "1", "on", "true", "yes"):
        return None
    return (os.environ.get("AGENT_TOOL_LOCK_PROXY_TRACK_FOLDER")
            or getattr(proxy, "track_folder", None) or os.getcwd())


def _compose_egress_policy(decision: GateDecision, folder: str, actor: str) -> GateDecision:
    """Compose the workspace-hierarchy POLICY (``decide_action`` via
    ``govern_egress``) with the DATA gate, **strictest-wins / escalate-only**: the
    policy can only tighten the data gate's action (allow→ask_user→refuse), never
    relax it — so the load-bearing privacy guarantee is preserved by construction.
    A confidential egress the policy then permits mints a GovernanceCertification.
    Best-effort: if policy is unavailable, the data gate stands unchanged."""
    from dataclasses import replace
    try:
        from ..governance import govern_egress
        confidential = decision.action in ("minimise", "refuse", "ask_user")
        gov = govern_egress(folder, actor=actor, confidential=confidential,
                            pii=confidential, action_class="egress.cloud-llm",
                            mint_cert=confidential)
        light = gov.get("light")
    except Exception:  # noqa: BLE001 — policy must never relax the data gate
        return decision
    rank = {"allow": 0, "minimise": 1, "ask_user": 2, "refuse": 3}
    want = {"go": "allow", "ask": "ask_user", "block": "refuse"}.get(light)
    if want and rank.get(want, 0) > rank.get(decision.action, 0):
        extra = str(gov.get("reason") or "").strip()
        return replace(decision, action=want,
                       reason=(decision.reason + (f" | policy: {extra}" if extra else "")
                               ).strip(" |"))
    return decision


def gate_prompt(
    text: str,
    *,
    oversight: OversightLevel,
    vault_path: str | None = None,
    decisions: DecisionsStore | None = None,
    audit: AuditLog | None = None,
    source: str = "cloud_llm_request",
    task_id: str | None = None,
    folder: str | None = None,
    actor: str = "agent",
) -> GateDecision:
    """Run the full minimisation gate on prompt text destined for a cloud LLM.

    Composes:
      - vault confidential-context load (via kg_context_for_vault)
      - lock_text (Tier B regex + Tier C semantic)
      - persisted-decisions short-circuit (via DecisionsStore.recall)
      - oversight-aware action translation (refuse vs ask_user)
      - (when ``folder`` is given) the workspace-hierarchy POLICY, strictest-wins —
        so the egress boundary speaks the SAME ``decide_action`` verdict as the
        hook. ``folder`` absent → unchanged (the default).

    Returns a ``GateDecision`` with action ∈ {allow, minimise, refuse, ask_user}.
    Never raises — fail-closed (refuse) on any internal error.
    """
    decision = gate_for_cloud(
        text,
        vault_path=vault_path,
        oversight=oversight,
        mode=Mode.STANDARD,
        decisions=decisions,
        audit=audit,
        source=source,
        task_id=task_id,
    )
    if folder:
        decision = _compose_egress_policy(decision, str(folder), actor)
    return decision


def redact_body_in_place(body: bytes, host: str) -> bytes:
    """Apply regex-based PII redaction to every natural-language text field in
    a provider-specific request body, then re-serialise.

    Used when the gate returns ``action="minimise"`` — we forward the request
    with sensitive spans replaced by typed placeholders, preserving the
    request structure so the upstream LLM still receives a valid prompt.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body

    def _redact_value(v):
        if isinstance(v, str):
            return _redact_text_with_regex(v)
        return v

    # Legacy OpenAI completions
    if isinstance(payload.get("prompt"), str):
        payload["prompt"] = _redact_value(payload["prompt"])
    elif isinstance(payload.get("prompt"), list):
        payload["prompt"] = [_redact_value(p) for p in payload["prompt"]]

    # System prompt — use the same structured-block walk as extraction.
    if "system" in payload:
        payload["system"] = _redact_block(payload["system"])

    # Messages array — redact the SAME shapes _block_strings extracts, so the
    # minimise path cannot forward unredacted PII that the scan saw (tool_result
    # content, tool_use args, text documents), not just plain "text" blocks.
    messages = payload.get("messages") or []
    for msg in messages:
        if "content" in msg:
            msg["content"] = _redact_block(msg["content"])

    return json.dumps(payload).encode("utf-8")


def _redact_any(obj: Any) -> Any:
    """Return a copy with every string redacted — for free-form tool_use args."""
    if isinstance(obj, str):
        return _redact_text_with_regex(obj)
    if isinstance(obj, list):
        return [_redact_any(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_any(v) for k, v in obj.items()}
    return obj


def _redact_block(node: Any) -> Any:
    """Redact a message-content node in the same shapes _block_strings reads,
    preserving structure for JSON validity."""
    if isinstance(node, str):
        return _redact_text_with_regex(node)
    if isinstance(node, list):
        return [_redact_block(b) for b in node]
    if isinstance(node, dict):
        nb = dict(node)
        if isinstance(nb.get("text"), str):
            nb["text"] = _redact_text_with_regex(nb["text"])
        if "content" in nb:
            nb["content"] = _redact_block(nb["content"])
        if "input" in nb:
            nb["input"] = _redact_any(nb["input"])
        src = nb.get("source")
        if isinstance(src, dict) and isinstance(src.get("data"), str):
            mt = str(src.get("media_type", ""))
            if not mt or mt.startswith("text"):
                nb["source"] = {**src, "data": _redact_text_with_regex(src["data"])}
        return nb
    return node


# ===========================================================================
# Default approval callbacks per oversight level
# ===========================================================================


def autonomous_callback(pending: PendingRequest) -> ApprovalDecision:
    """AUTONOMOUS — silently allow, log everything."""
    return ApprovalDecision(action="allow", reason="oversight=autonomous (silent)")


def notify_callback(pending: PendingRequest) -> ApprovalDecision:
    """NOTIFY — allow; user is notified post-execution by audit log inspection."""
    return ApprovalDecision(action="allow", reason="oversight=notify (audit-logged)")


def block_on_findings_callback(pending: PendingRequest) -> ApprovalDecision:
    """STRICT — block if any HIGH finding."""
    high = [f for f in pending.findings if f.severity == "high"]
    if high:
        return ApprovalDecision(
            action="block",
            reason=f"oversight=strict and {len(high)} HIGH finding(s) present",
        )
    return ApprovalDecision(action="allow", reason="no HIGH findings")


def interactive_callback_stdin(pending: PendingRequest) -> ApprovalDecision:
    """SUPERVISED — read user decision from stdin.

    Suitable for terminal use where the proxy is foregrounded. Production
    deployments should swap this for a web-UI callback or a Cowork-side prompt.

    Options offered to the user:
      ``a`` allow once       — forward this request, do not remember.
      ``A`` allow always     — forward + persist as scope=always.
      ``s`` allow session    — forward + persist as scope=session.
      ``b`` block once       — refuse this request, do not remember.
      ``B`` block always     — refuse + persist as scope=always.
      ``w`` waive and allow  — forward with reason recorded (legacy path).
    """
    import sys

    print(f"\n━━ outbound LLM request {pending.request_id} ━━")
    print(f"  upstream:   {pending.upstream_host}")
    print(f"  path:       {pending.path}")
    print(f"  bytes:      {len(pending.body)}")
    print(f"  findings:   {len(pending.findings)}")
    for i, f in enumerate(pending.findings, 1):
        marker = "●" if f.severity == "high" else "◐" if f.severity == "medium" else "○"
        print(f"    [{i}] {marker} {f.severity.upper():6} tier={f.tier} {f.type}: {f.detail}")
    print(f"\n  Action?")
    print(f"    [a] allow once         [A] allow ALWAYS")
    print(f"    [s] allow this session [b] block once")
    print(f"    [B] block ALWAYS       [w] waive-and-allow (legacy)")
    print(f"  > ", end="", flush=True)
    try:
        raw = sys.stdin.readline().strip()
    except Exception:
        raw = ""

    # Case-sensitive parsing — uppercase means "remember".
    if raw == "A":
        return ApprovalDecision(
            action="allow",
            reason="user allowed (remember always)",
            waived_findings=["scope:always"],
        )
    if raw == "B":
        return ApprovalDecision(
            action="block",
            reason="user blocked (remember always)",
            waived_findings=["scope:always"],
        )
    if raw.lower().startswith("s"):
        return ApprovalDecision(
            action="allow",
            reason="user allowed (remember session)",
            waived_findings=["scope:session"],
        )
    if raw.lower().startswith("a"):
        return ApprovalDecision(action="allow", reason="user allowed (once)")
    if raw.lower().startswith("w"):
        print(f"  Waiver reason: ", end="", flush=True)
        try:
            reason = sys.stdin.readline().strip()
        except Exception:
            reason = ""
        return ApprovalDecision(
            action="allow",
            reason=f"user waived: {reason}",
            waived_findings=[f.detail for f in pending.findings],
        )
    return ApprovalDecision(action="block", reason="user blocked (once)")


def make_default_callback(oversight: OversightLevel, *, allow_user_override: bool = True) -> ApprovalCallback:
    """Build the right callback for the given oversight level.

    Args:
        oversight: the oversight level.
        allow_user_override: if True, AUTONOMOUS/NOTIFY/REVIEW will still
            engage the interactive prompt when HIGH findings are present.
            Default True — the load-bearing semantic that the user can always
            override the policy with an explicit waiver.
    """
    if oversight == OversightLevel.AUTONOMOUS:
        if not allow_user_override:
            return autonomous_callback
        return _conditional_interactive(threshold="high")
    if oversight == OversightLevel.NOTIFY:
        return notify_callback
    if oversight == OversightLevel.REVIEW:
        return notify_callback  # forward; review happens via audit log
    if oversight == OversightLevel.APPROVE:
        return _conditional_interactive(threshold="high")
    if oversight == OversightLevel.SUPERVISED:
        return interactive_callback_stdin
    # MANUAL
    return _block_all_callback


def _conditional_interactive(threshold: str) -> ApprovalCallback:
    """Auto-allow if no findings at or above threshold; interactive otherwise."""
    severity_rank = {"low": 0, "medium": 1, "high": 2}

    def callback(pending: PendingRequest) -> ApprovalDecision:
        threshold_rank = severity_rank.get(threshold, 2)
        triggered = [f for f in pending.findings
                     if severity_rank.get(f.severity, 0) >= threshold_rank]
        if not triggered:
            return ApprovalDecision(action="allow",
                                    reason=f"no findings ≥ {threshold}; auto-allowed")
        return interactive_callback_stdin(pending)

    return callback


def _block_all_callback(pending: PendingRequest) -> ApprovalDecision:
    """MANUAL — proxy does not forward. User must execute the request manually."""
    return ApprovalDecision(
        action="block",
        reason="oversight=manual (proxy never forwards; user executes manually)",
    )


# ===========================================================================
# Proxy server
# ===========================================================================


class EgressProxy:
    """The outbound enforcement gate.

    Lifecycle:
        proxy = EgressProxy(oversight=OversightLevel.APPROVE)
        proxy.start()   # non-blocking; spawns server thread
        ...
        proxy.stop()
    """

    def __init__(
        self,
        *,
        port: int | None = None,
        oversight: OversightLevel | None = None,
        approval_callback: ApprovalCallback | None = None,
        upstream_overrides: dict[str, str] | None = None,
        audit_log_path: str | None = None,
        vault_path: str | None = None,
        decisions_path: str | None = None,
        track_folder: str | None = None,
        track_log_root: str | None = None,
        capability_verifier: Any | None = None,
    ):
        self.port = port or int(os.environ.get("AGENT_TOOL_LOCK_PROXY_PORT", _DEFAULT_PORT))
        oversight_env = os.environ.get("AGENT_TOOL_LOCK_PROXY_OVERSIGHT", "")
        if oversight is None and oversight_env:
            try:
                oversight = OversightLevel[oversight_env.upper()]
            except KeyError:
                oversight = _DEFAULT_OVERSIGHT
        self.oversight = oversight or _DEFAULT_OVERSIGHT
        self.callback: ApprovalCallback = approval_callback or make_default_callback(self.oversight)
        self.allowed_upstreams = dict(_ALLOWED_UPSTREAMS)
        if upstream_overrides:
            self.allowed_upstreams.update(upstream_overrides)
        self.audit_log_path = audit_log_path or os.environ.get("AGENT_TOOL_LOCK_AUDIT_LOG", "")
        self.vault_path = vault_path or os.environ.get("AGENT_TOOL_LOCK_VAULT_PATH", "") or None
        decisions_p = decisions_path or os.environ.get("AGENT_TOOL_LOCK_DECISIONS_PATH", "")
        # DecisionsStore defaults to ~/.config/agent-tool-lock/decisions.jsonl
        # when no path is given. Initialise once per proxy instance so all
        # incoming requests share the recall cache.
        self.decisions = DecisionsStore(decisions_p) if decisions_p else DecisionsStore()
        # CL3: the identity attributed to durable clearances recorded at this proxy
        # (a persistent 'always' decision must name who made it). The operator
        # running the proxy; configurable, with a clear non-anonymous default.
        self.operator = os.environ.get("WORKSPACE_L0_DEFAULT_ACTOR", "").strip() or "egress-operator"
        # Broker mode — bind the proxy to one workspace folder; every request must
        # then declare its egress track (X-Lock-Track) and is credentialed from the
        # track's credential_ref, never from client-supplied headers.
        self.track_folder = (track_folder
                             or os.environ.get("AGENT_TOOL_LOCK_PROXY_TRACK_FOLDER", "").strip()
                             or None)
        self.track_log_root = (track_log_root
                               or os.environ.get("AGENT_TOOL_LOCK_PROXY_TRACK_LOG_ROOT", "").strip()
                               or None)
        # Constructing the enforcement boundary requires an existing trust
        # root. Missing key material is a startup refusal, never a mode that
        # silently accepts tokenless requests.
        if capability_verifier is None:
            host_deps.ensure_wired()
            factory = host_deps.capability_verifier_factory
            if factory is None:
                raise RuntimeError("session capability verifier is not wired")
            capability_verifier = factory()
        self.capability_verifier = capability_verifier
        # AuditLog wrapper for lock_text() to use. None when no audit configured.
        self._lock_audit = AuditLog(self.audit_log_path) if self.audit_log_path else None

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

        # Bound concurrent in-flight forwards. Acquired non-blocking at the top of
        # every POST; when saturated the request is shed with a 503 instead of
        # spawning another thread that would block on a possibly-stalled upstream.
        self.max_concurrency = _egress_max_concurrency()
        self._inflight = threading.BoundedSemaphore(self.max_concurrency)

        # Counters — exposed for tests + doctor
        self.stats = {
            "received": 0,
            "allowed": 0,
            "blocked": 0,
            "modified": 0,
            "recalled": 0,           # short-circuited via DecisionsStore
            "user_approved": 0,      # ask_user → callback returned allow
            "shed": 0,               # 503'd — concurrency cap reached (load shed)
            "errors": 0,
        }

    def start(self, *, host: str = "127.0.0.1") -> None:
        """Start the proxy. Non-blocking — spawns a daemon thread."""
        if self._server is not None:
            return
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def audit_log(self, entry: dict) -> None:
        if not self.audit_log_path:
            return
        try:
            with open(self.audit_log_path, "a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def record_capability_refusal(self, reason: str, path: str) -> None:
        """Append a signed incident when this proxy is bound to a workspace."""
        if not self.track_folder:
            return
        try:
            host_deps.ensure_wired()
            recorder = host_deps.record_capability_refusal
            if recorder is None:
                return
            recorder(
                self.track_folder,
                reason=reason,
                path=path,
                log_root=self.track_log_root,
            )
        except Exception:
            # Admission already refused. Evidence failure must not convert the
            # request into an allow; the ordinary proxy audit remains available.
            return


# ===========================================================================
# HTTP handler
# ===========================================================================


def _persist_scope_decision(store, text: str, decision: str,
                            markers: list[str] | None, reason: str,
                            actor: str = "") -> None:
    """Persist a user decision when the callback tagged a remember-scope marker
    ("scope:always" / "scope:session") in waived_findings. CL1: this runs for
    BLOCK as well as ALLOW — a "block always" must be durable, not silently
    discarded because the persist path only handled allow. CL3: a durable
    clearance carries the operator's identity; an anonymous 'always' is rejected
    by the store (fail-closed) and therefore simply not persisted (re-prompts)."""
    if not text:
        return
    for marker in (markers or []):
        if marker.startswith("scope:"):
            scope = marker.split(":", 1)[1].strip()
            if scope in ("session", "always"):
                try:
                    store.remember(text, decision, scope=scope, reason=reason, actor=actor)
                except (OSError, ValueError):
                    pass
                return


def _upstream_request_url(base_url: str, request_target: str) -> str:
    """Build an upstream URL from a validated origin-form request target.

    A proxy request target is untrusted input. Only ``/path`` and
    ``/path?query`` forms are accepted; absolute-form, authority-form,
    scheme-relative, fragments and control characters are refused. The final
    authority is checked against the configured base URL after joining.
    """
    if (not request_target.startswith("/")
            or request_target.startswith("//")
            or any(ch in request_target for ch in ("\r", "\n", "\x00"))):
        raise ValueError("request target must be an origin-form path")

    target = urllib.parse.urlsplit(request_target)
    if target.scheme or target.netloc or target.fragment:
        raise ValueError("request target may not select an authority or fragment")

    base = urllib.parse.urlsplit(base_url)
    if base.scheme not in ("http", "https") or not base.netloc:
        raise ValueError("configured upstream base URL is invalid")

    joined = urllib.parse.urljoin(base_url.rstrip("/") + "/", request_target)
    final = urllib.parse.urlsplit(joined)
    if (final.scheme, final.netloc) != (base.scheme, base.netloc):
        raise ValueError("request target changed the configured upstream authority")
    return joined


def _make_handler(proxy: EgressProxy):
    """Build an HTTP request handler class bound to a proxy instance."""

    class EgressProxyHandler(BaseHTTPRequestHandler):
        # Don't spam stderr with default access logging
        def log_message(self, format, *args):
            return

        def do_POST(self):
            self._handle_request()

        def do_GET(self):
            # Allow GET only for liveness — `GET /__lock_health__`
            if self.path == "/__lock_health__":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "oversight": proxy.oversight.label,
                    "stats": proxy.stats,
                    "max_concurrency": proxy.max_concurrency,
                    # whether a broker holds the plug, and for which folder — the
                    # egress board may render a track as "enforced" only when a
                    # broker is bound to that track's own folder (localhost-only
                    # endpoint; the folder path is already printed at startup)
                    "broker_bound": bool(proxy.track_folder),
                    "broker_folder": proxy.track_folder or None,
                }).encode("utf-8"))
                return
            self.send_error(405, "Use POST for LLM requests")

        def _handle_request(self):
            # Bound concurrency BEFORE any work. ThreadingHTTPServer would happily
            # spawn a thread per connection; under a sustained upstream outage each
            # forward blocks holding a thread, so an unbounded flood is a self-DoS
            # (RV-09). Acquire non-blocking: at the cap we SHED with a 503 (fail-
            # closed, a clear operator signal) rather than queue silently. The
            # health GET path is deliberately not gated — operators must still be
            # able to observe saturation while forwards are being shed.
            if not proxy._inflight.acquire(blocking=False):
                proxy.stats["shed"] += 1
                proxy.audit_log({
                    "kind": "proxy_shed",
                    "ts": time.time(),
                    "reason": f"egress concurrency cap ({proxy.max_concurrency}) "
                              "reached — shedding load",
                    "path": self.path,
                })
                self.send_error(503, "egress proxy at capacity")
                return
            try:
                self._forward()
            finally:
                proxy._inflight.release()

        def _forward(self):
            proxy.stats["received"] += 1
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length > 0 else b""

            # Capability admission precedes upstream selection, credential
            # injection, prompt parsing, and every possible network action.
            # This proxy currently listens on loopback TCP, which exposes no
            # portable peer-UID primitive. UID is therefore enforced by the
            # operate path and OS firewall; the proxy verifies signature,
            # freshness, revocation, and (in broker mode) exact folder binding.
            token = self.headers.get("X-Rvnd-Capability", "")
            try:
                proxy.capability_verifier.verify(
                    token,
                    expected_folder=proxy.track_folder,
                )
            except Exception as exc:
                proxy.stats["blocked"] += 1
                proxy.record_capability_refusal(str(exc), self.path)
                proxy.audit_log({
                    "kind": "capability_refused",
                    "ts": time.time(),
                    "reason": str(exc),
                    "path": self.path,
                })
                self.send_error(403, f"session capability refused: {exc}")
                return

            # Pick upstream: clients should send Host header for the upstream
            # they want to reach, OR use full URL paths. We support both:
            # - Header `X-Lock-Upstream: api.anthropic.com`
            # - Header `Host: api.anthropic.com` (when client points DNS / hosts file at us)
            upstream_host = (
                self.headers.get("X-Lock-Upstream")
                or self.headers.get("Host", "").split(":")[0]
                or "api.anthropic.com"
            )

            if upstream_host not in proxy.allowed_upstreams:
                proxy.stats["blocked"] += 1
                proxy.audit_log({
                    "kind": "proxy_block",
                    "ts": time.time(),
                    "reason": f"upstream '{upstream_host}' not in allowlist",
                    "path": self.path,
                })
                self.send_error(403, f"upstream '{upstream_host}' not allowed by lock")
                return

            try:
                upstream_url = _upstream_request_url(
                    proxy.allowed_upstreams[upstream_host], self.path
                )
            except ValueError as exc:
                proxy.stats["blocked"] += 1
                proxy.audit_log({
                    "kind": "proxy_block",
                    "ts": time.time(),
                    "reason": str(exc),
                    "path": self.path,
                })
                self.send_error(403, "invalid request target")
                return

            # Request→track binding (broker mode) or D4 credential/upstream binding.
            # In broker mode the client's credential headers are stripped and the
            # track's own credential is injected, so the incoming D4 check is moot —
            # the binding is enforced at injection, keyed by the same upstream map.
            binding = None
            if proxy.track_folder:
                from .track_broker import TRACK_HEADER, TrackBinding, bind_track
                binding = bind_track(proxy.track_folder,
                                     self.headers.get(TRACK_HEADER),
                                     log_root=proxy.track_log_root)
                if binding.ok and upstream_host not in _UPSTREAM_INJECT:
                    # An upstream the broker cannot credential must not be reachable
                    # with a track credential half-applied — refuse, fail-closed.
                    binding = TrackBinding(
                        ok=False,
                        reason=f"upstream '{upstream_host}' has no credential "
                               "injection binding; cannot broker",
                        connector_id=binding.connector_id,
                    )
                if not binding.ok:
                    proxy.stats["blocked"] += 1
                    proxy.audit_log({
                        "kind": "proxy_block",
                        "ts": time.time(),
                        "reason": binding.reason,
                        "track": binding.connector_id,
                        "path": self.path,
                    })
                    self.send_error(403, f"egress refused: {binding.reason}")
                    return
            else:
                # D4 — credential/upstream binding: refuse if the request carries a
                # credential header that does not belong to the chosen upstream, rather
                # than forwarding (e.g.) an Anthropic x-api-key to OpenAI. Fail-closed:
                # a mismatched key is a cross-provider leak, blocked before any forward.
                _bad_cred = _credential_binding_violation(self.headers, upstream_host)
                if _bad_cred is not None:
                    proxy.stats["blocked"] += 1
                    proxy.audit_log({
                        "kind": "proxy_block",
                        "ts": time.time(),
                        # name the header, NEVER its value (it is the secret).
                        "reason": f"credential header '{_bad_cred}' not valid for upstream "
                                  f"'{upstream_host}' — refusing cross-provider key forward",
                        "path": self.path,
                    })
                    self.send_error(403, "credential does not match upstream (lock D4)")
                    return

            # Extract + run the full gate (vault context + lock + decisions recall)
            text = extract_prompt_text(upstream_host, body)
            request_id = f"req-{int(time.time()*1000)}-{proxy.stats['received']}"

            if not text.strip() and len(body.strip()) > 2:
                # Non-trivial body but nothing scannable extracted — unparseable
                # JSON, or a content shape we don't understand (image/binary/an
                # unknown block). We cannot verify what would leave the machine,
                # so fail CLOSED: refuse rather than forward the original
                # unscanned body. (Was the load-bearing egress fail-OPEN, D3.)
                gate = GateDecision(
                    action="refuse",
                    reason="cannot verify request content — no scannable text in a "
                           "non-empty body (fail-closed)",
                    source="cloud_llm_request",
                )
            else:
                gate = gate_prompt(
                    text,
                    oversight=proxy.oversight,
                    vault_path=proxy.vault_path,
                    decisions=proxy.decisions,
                    audit=proxy._lock_audit,
                    source="cloud_llm_request",
                    task_id=request_id,
                    folder=_egress_policy_folder(proxy),          # None unless RVND_EGRESS_POLICY
                    actor=os.environ.get("RVND_AGENT", "agent"),
                )

            # Translate gate -> final action. Defaults match gate.action 1:1 except
            # for ask_user where the proxy invokes the approval callback.
            final_action = gate.action     # "allow" | "minimise" | "refuse" | "ask_user"
            final_reason = gate.reason
            forward_body = body
            waived_findings: list[str] = []

            if gate.action == "minimise":
                # Rebuild the request body with regex-redacted text fields.
                forward_body = redact_body_in_place(body, upstream_host)
                proxy.stats["modified"] += 1
            elif gate.action == "ask_user":
                # Surface to the user via the configured approval callback.
                pending = PendingRequest(
                    request_id=request_id,
                    upstream_host=upstream_host,
                    method=self.command,
                    path=self.path,
                    body=body,
                    extracted_text=text,
                    findings=gate.findings,
                    oversight=proxy.oversight,
                )
                cb_decision = proxy.callback(pending)
                if cb_decision.action == "block":
                    final_action = "refuse"
                    final_reason = cb_decision.reason
                    waived_findings = cb_decision.waived_findings
                    # CL1: a "block always" / "block session" must be persisted
                    # too — previously only allow decisions were remembered, so a
                    # user's durable block was silently discarded.
                    _persist_scope_decision(proxy.decisions, text, "block",
                                            waived_findings,
                                            cb_decision.reason or "user blocked at proxy",
                                            actor=proxy.operator)
                elif cb_decision.action == "modify":
                    final_action = "minimise"
                    final_reason = cb_decision.reason
                    forward_body = cb_decision.modified_body or body
                    proxy.stats["modified"] += 1
                else:
                    final_action = "allow"
                    final_reason = cb_decision.reason
                    proxy.stats["user_approved"] += 1
                    waived_findings = cb_decision.waived_findings
                    # Persist if the callback supplied a remember-scope hint
                    # ("scope:always" / "scope:session" in waived_findings).
                    _persist_scope_decision(proxy.decisions, text, "allow",
                                            waived_findings,
                                            cb_decision.reason or "user approved at proxy",
                                            actor=proxy.operator)

            # Track floor "hold" — this channel always needs a person. When the
            # gate itself asked (ask_user) that person has already spoken; when it
            # would forward silently (allow/minimise — including a recalled
            # decision), consult the callback on the body that would actually be
            # forwarded. Deliberately no remember-scope persistence here: a durable
            # clearance would contradict a floor that requires a person every time.
            if (binding is not None and binding.hold
                    and gate.action != "ask_user"
                    and final_action in ("allow", "minimise")):
                pending = PendingRequest(
                    request_id=request_id,
                    upstream_host=upstream_host,
                    method=self.command,
                    path=self.path,
                    body=forward_body,
                    extracted_text=text,
                    findings=gate.findings,
                    oversight=proxy.oversight,
                )
                cb_decision = proxy.callback(pending)
                if cb_decision.action == "block":
                    final_action = "refuse"
                    final_reason = (cb_decision.reason
                                    or f"track '{binding.connector_id}' floor is hold — blocked")
                elif cb_decision.action == "modify":
                    final_action = "minimise"
                    final_reason = cb_decision.reason
                    forward_body = cb_decision.modified_body or forward_body
                    proxy.stats["modified"] += 1
                else:
                    final_reason = (cb_decision.reason
                                    or f"track '{binding.connector_id}' floor is hold — approved")
                    proxy.stats["user_approved"] += 1

            if gate.recalled_from_decisions:
                proxy.stats["recalled"] += 1

            # Legacy "action" semantic (allow|block|modify) — kept for backwards
            # compatibility with existing audit-log consumers.
            _legacy_action_map = {"allow": "allow", "minimise": "modify", "refuse": "block"}
            audit_entry = {
                "kind": "proxy_decision",
                "ts": time.time(),
                "request_id": request_id,
                "upstream": upstream_host,
                "path": self.path,
                "bytes_in": len(body),
                "text_length": len(text),  # never log the text itself
                "findings_count": len(gate.findings),
                "findings_summary": [
                    {"tier": f.tier, "severity": f.severity, "type": f.type, "field": f.field}
                    for f in gate.findings
                ],
                "oversight": proxy.oversight.label,
                "action": _legacy_action_map.get(final_action, final_action),  # legacy
                "gate_action": gate.action,
                "final_action": final_action,
                "reason": final_reason,
                "recalled_from_decisions": gate.recalled_from_decisions,
                "vault_context_loaded": bool(proxy.vault_path),
                "waived_findings": waived_findings,
                "lock_bypassed": bool(getattr(gate, "lock_bypassed", False)),
                "would_have": str(getattr(gate, "would_have", "") or ""),
            }
            if binding is not None:
                # The reference and track id, never the secret.
                audit_entry["track"] = binding.connector_id
                audit_entry["credential_ref"] = binding.credential_ref
                audit_entry["track_floor"] = binding.floor
                audit_entry["mode"] = "brokered"
            proxy.audit_log(audit_entry)

            if final_action == "refuse":
                proxy.stats["blocked"] += 1
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "blocked by agent-tool-lock",
                    "request_id": request_id,
                    "reason": final_reason,
                    "findings": audit_entry["findings_summary"],
                }).encode("utf-8"))
                return

            if final_action == "allow":
                proxy.stats["allowed"] += 1

            try:
                req = urllib.request.Request(
                    upstream_url,
                    data=forward_body,
                    method=self.command,
                )
                # Forward headers except hop-by-hop (and, in broker mode, the track
                # declaration plus every client credential header — the track's own
                # credential is injected below; the agent never holds the key).
                _hop_by_hop = {"host", "x-lock-upstream", "content-length", "connection",
                               "transfer-encoding"}
                if binding is not None:
                    _hop_by_hop = _hop_by_hop | {"x-lock-track"} | _CREDENTIAL_HEADERS
                for k, v in self.headers.items():
                    if k.lower() not in _hop_by_hop:
                        req.add_header(k, v)
                if binding is not None:
                    _hdr, _tpl = _UPSTREAM_INJECT[upstream_host]
                    req.add_header(_hdr, _tpl.format(secret=binding.secret))
                req.add_header("Content-Length", str(len(forward_body)))
                with urllib.request.urlopen(req, timeout=_egress_timeout_secs()) as resp:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in _hop_by_hop:
                            self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(resp.read())
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.end_headers()
                self.wfile.write(e.read())
            except (urllib.error.URLError, OSError, socket.timeout,
                    http.client.HTTPException) as e:
                # http.client.HTTPException (e.g. IncompleteRead when a slow
                # upstream sends headers then stalls the body) subclasses
                # neither URLError nor OSError, so without it a half-response
                # escaped this block and the handler thread dropped the
                # connection uncleanly — the agent saw a raw connection reset,
                # not the fail-closed 502 the proxy promises on any upstream
                # trouble. Fold it into the same 502 path.
                proxy.stats["errors"] += 1
                self.send_error(502, f"upstream error: {e}")

    return EgressProxyHandler


# ===========================================================================
# CLI entry
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="agent-tool-lock proxy")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument(
        "--oversight",
        default=os.environ.get("AGENT_TOOL_LOCK_PROXY_OVERSIGHT", "approve"),
        choices=["autonomous", "notify", "review", "approve", "supervised", "manual"],
    )
    parser.add_argument("--audit-log", default=os.environ.get("AGENT_TOOL_LOCK_AUDIT_LOG", ""))
    parser.add_argument("--track-folder",
                        default=os.environ.get("AGENT_TOOL_LOCK_PROXY_TRACK_FOLDER", ""),
                        help="workspace folder to broker for — enables per-track "
                             "credential injection (X-Lock-Track required)")
    parser.add_argument("--track-log-root",
                        default=os.environ.get("AGENT_TOOL_LOCK_PROXY_TRACK_LOG_ROOT", ""))
    args = parser.parse_args(argv)

    # Apply persisted config (so AGENT_TOOL_LOCK_LLM_BACKEND etc. are set)
    cfg = load_config()
    apply_config_to_env(cfg)

    proxy = EgressProxy(
        port=args.port,
        oversight=OversightLevel[args.oversight.upper()],
        audit_log_path=args.audit_log,
        track_folder=args.track_folder or None,
        track_log_root=args.track_log_root or None,
    )
    proxy.start()

    print(f"━━ agent-tool-lock egress proxy ━━")
    print(f"  listening:  http://127.0.0.1:{args.port}")
    print(f"  oversight:  {proxy.oversight.label} (level {proxy.oversight.value})")
    print(f"  max conc.:  {proxy.max_concurrency} in-flight forwards "
          f"(over-cap requests shed with 503)")
    print(f"  upstreams:  {sorted(proxy.allowed_upstreams.keys())}")
    print(f"  audit log:  {args.audit_log or '(not set — pass --audit-log)'}")
    print(f"  vault:      {proxy.vault_path or '(not set — confidential-context disabled)'}")
    print(f"  decisions:  {proxy.decisions.path}")
    if proxy.track_folder:
        print(f"  broker:     bound to {proxy.track_folder} — every request must "
              f"declare its egress track (X-Lock-Track)")
    else:
        print(f"  broker:     not bound — client-supplied credentials, D4 binding only")
    print()
    print(f"  Point your agent at this proxy:")
    print(f"    ANTHROPIC_BASE_URL=http://127.0.0.1:{args.port}")
    print(f"    OPENAI_BASE_URL=http://127.0.0.1:{args.port}/v1")
    print()
    print(f"  For LOAD-BEARING enforcement, also block direct outbound to LLM APIs.")
    print(f"  Apply deploy/firewall/ (nftables/pf/Windows), then check it took:")
    print(f"    PYTHONPATH=server/src python3 scripts/verify_egress_lock.py")
    print()
    print(f"  Ctrl+C to stop.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n  stopping...")
        proxy.stop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

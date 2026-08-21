# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shared test infrastructure for the local-model stress suite (B10+).

Five families of helpers — used by the four stress-test files that sit
alongside this module:

  * ``MockCloudLLM`` / ``MockLocalLLM`` — context managers that patch
    ``capture_llm_exchange`` (cloud dispatch boundary recorder) and
    ``local_llm_classify`` / ``local_llm_complete`` (local route). Each
    records every call argument + the deterministic synthetic token
    counts the test asserts on.
  * ``SyntheticWorkload`` — factory that produces the canonical 200-row
    distribution (50% no-PII, 30% Tier-B, 15% Tier-B+ confusable, 5%
    Tier-C-only-detectable) used by every test in this directory. Seeded
    so two runs of the same suite see identical inputs.
  * ``TokenCounter`` — additive int counters per dispatch mode for the
    reduction test (``mode_a`` baseline vs ``mode_b`` validator).
  * ``assert_no_pii_leaked`` — walks audit events and asserts no PII span
    from the original inputs ever lands in any ``capture_llm.prompt`` /
    ``prompt_context`` field on the chain.

Mocks are import-boundary patches: we override
``rvnd.local_llm.classify`` and ``rvnd.local_llm.complete`` (the
rvnd.lock Tier C ensemble + the MCP route both reach through those),
and ``rvnd.llm_capture.capture_llm_exchange`` for the cloud-dispatch
boundary. Tests never reach a real local LLM or a real cloud model.
"""

from __future__ import annotations

import random
import re
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Synthetic input distribution
# ---------------------------------------------------------------------------


# Tier-B PII-shaped strings — every line trips a Tier-B regex.
_TIER_B_BANK = [
    "Email me at jane.doe\x40example.com for the agreement.",
    "Wire to DE89370400440532013000 by Friday.",
    "Steuer-ID 12 345 678 902 is on file.",
    "DNI 12345678Z attached.",
    "MRN: AB1234567 — please update the chart.",
    "Patient #45821 admitted overnight.",
    "Card 4111 1111 1111 1111 charged today.",
    "Call me on +49 30 1234 5678 about the matter.",
    "API key " "sk_" "live_abcdef0123456789ZZZZ rotated.",
    "Bearer eyJhbGciOiJIUzI1NiJsupersecret token issued.",
]

# Tier-B+ confusable bypass — homoglyph hiding an email pattern.
_TIER_B_PLUS_BANK = [
    "Email me at jane.doe" + "@" + "example.ⅽom for review.",
    "Reach out to john.smith" + "@" + "аcme.com today.",
    "Send a copy to admin" + "@" + "exаmple.com please.",
    "Forward the note to support" + "@" + "bаnk.com asap.",
    "DM ben" + "@" + "cоmpany.com about the change.",
]

# Tier-C-semantic-only — PII the regex demonstrably does NOT catch.
# Names without a possessive/title gate, narrative descriptions of
# identifiable people, etc. The local-model ensemble is meant to flag
# these once it's wired; in this test the mock returns whatever the
# scenario configured.
_TIER_C_BANK = [
    "The patient from the third ward had a relapse on Tuesday.",
    "Our former CFO confessed during the post-mortem.",
    "The applicant born 1973 in Stuttgart lost the appeal.",
    "Our intern at the Hamburg office filed a complaint.",
    "The witness who lives near the harbour testified.",
]

# Plain prose with no PII whatsoever.
_NO_PII_BANK = [
    "Q3 revenue overview compiled by finance.",
    "All hands meeting moved to Thursday morning.",
    "Please update the project status in the tracker.",
    "Quarterly close timeline attached for review.",
    "Marketing pipeline forecast looks healthy.",
    "OKRs draft circulated for comment.",
    "Roadmap workshop notes shared in Notion.",
    "Team off-site venue tentatively booked.",
    "Customer NPS trend continues upward.",
    "Engineering velocity stable this sprint.",
]


def _expand(bank: list[str], n: int, rng: random.Random) -> list[str]:
    """Cycle/sample ``bank`` to fill ``n`` slots."""
    if not bank:
        return [""] * n
    out: list[str] = []
    while len(out) < n:
        out.extend(rng.sample(bank, k=min(len(bank), n - len(out))))
    return out[:n]


@dataclass
class SyntheticInput:
    """One row of the synthetic workload."""

    text: str
    expected_tier: str   # "none" | "tier_b" | "tier_b_plus" | "tier_c"


class SyntheticWorkload:
    """Factory for the canonical 200-row workload.

    Distribution (default, total=200):
      * 100 (50%) — no-PII
      * 60  (30%) — Tier-B PII
      * 30  (15%) — Tier-B+ confusable
      * 10  (5%)  — Tier-C-only (semantic, regex-invisible)

    Seeded (default 1234). Calling :py:meth:`build` twice with the same
    seed yields the same list (order included).
    """

    DEFAULT_DISTRIBUTION = {
        "none":        0.50,
        "tier_b":      0.30,
        "tier_b_plus": 0.15,
        "tier_c":      0.05,
    }

    def __init__(self, total: int = 200, seed: int = 1234,
                 distribution: dict[str, float] | None = None):
        self.total = total
        self.seed = seed
        self.distribution = dict(distribution or self.DEFAULT_DISTRIBUTION)

    def build(self) -> list[SyntheticInput]:
        rng = random.Random(self.seed)
        counts = {
            k: int(round(self.total * v))
            for k, v in self.distribution.items()
        }
        # Reconcile rounding drift by padding/truncating the largest bucket.
        diff = self.total - sum(counts.values())
        if diff != 0:
            biggest = max(counts, key=counts.get)
            counts[biggest] += diff

        rows: list[SyntheticInput] = []
        rows.extend(
            SyntheticInput(t, "none")
            for t in _expand(_NO_PII_BANK, counts["none"], rng)
        )
        rows.extend(
            SyntheticInput(t, "tier_b")
            for t in _expand(_TIER_B_BANK, counts["tier_b"], rng)
        )
        rows.extend(
            SyntheticInput(t, "tier_b_plus")
            for t in _expand(_TIER_B_PLUS_BANK, counts["tier_b_plus"], rng)
        )
        rows.extend(
            SyntheticInput(t, "tier_c")
            for t in _expand(_TIER_C_BANK, counts["tier_c"], rng)
        )
        # Deterministic shuffle so PII isn't clustered at the tail.
        rng.shuffle(rows)
        return rows


# ---------------------------------------------------------------------------
# Token counter
# ---------------------------------------------------------------------------


def synthetic_token_count(text: str) -> int:
    """Synthetic-but-deterministic token count: ``len(text)//4``.

    Matches the documented mock token model in the test brief; used so
    the cloud-token-reduction assertions are deterministic.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class TokenCounter:
    """Additive counters per dispatch mode."""

    cloud_input_tokens: int = 0
    cloud_output_tokens: int = 0
    cloud_calls: int = 0
    local_invocations: int = 0

    def record_cloud(self, prompt: str, response: str) -> None:
        self.cloud_calls += 1
        self.cloud_input_tokens += synthetic_token_count(prompt)
        self.cloud_output_tokens += synthetic_token_count(response)

    def record_local(self) -> None:
        self.local_invocations += 1

    @property
    def total_cloud_tokens(self) -> int:
        return self.cloud_input_tokens + self.cloud_output_tokens


# ---------------------------------------------------------------------------
# Mock cloud LLM (boundary recorder)
# ---------------------------------------------------------------------------


@dataclass
class CloudCall:
    """One recorded cloud dispatch."""

    prompt: str
    response: str
    model: str = "mock-cloud-sonnet"
    input_tokens: int = 0
    output_tokens: int = 0
    lock_pre_call: bool = False  # set True by the harness wrapper
    extra: dict[str, Any] = field(default_factory=dict)


class MockCloudLLM:
    """Context manager — patches the cloud dispatch boundary.

    Records every cloud call (prompt + synthetic response + token count)
    so tests can assert on (a) what reached the cloud, (b) the order
    relative to lock events, (c) the total token spend.

    Patches ``rvnd.llm_capture.capture_llm_exchange`` since that is
    THE single entry the cloud-LLM-calling code goes through (see the
    docstring on ``capture_llm_exchange``).

    A second, lighter shim (``dispatch``) is also exposed — tests that
    want to simulate "we called the cloud" without going through the
    capture path can use the bound ``cloud.dispatch(prompt)`` and the
    call will be recorded the same way.
    """

    def __init__(self, *, response_factory: Callable[[str], str] | None = None,
                 token_counter: TokenCounter | None = None):
        self.calls: list[CloudCall] = []
        self.token_counter = token_counter or TokenCounter()
        self._stack: ExitStack | None = None
        self._response_factory = response_factory or (
            lambda p: f"[mock-cloud-response-{len(p)}]"
        )

    # -- patch lifecycle ---------------------------------------------------

    def __enter__(self) -> "MockCloudLLM":
        self._stack = ExitStack()
        # Patch the capture boundary so tests that exercise the audit
        # path see the synthetic exchange recorded; we also patch the
        # MCP-exposed convenience name on the module.
        self._stack.enter_context(patch(
            "rvnd.llm_capture.capture_llm_exchange",
            side_effect=self._capture_side_effect,
        ))
        return self

    def __exit__(self, *exc):
        if self._stack is not None:
            self._stack.close()
            self._stack = None
        return False

    # -- recording ---------------------------------------------------------

    def _capture_side_effect(self, exchange, *, mode, oversight,
                              folder_context, log_root=None, actor="agent",
                              user_decision_callback=None):
        """Stand-in for ``capture_llm_exchange``.

        We DO NOT touch the L0 memory in this side-effect; the only thing
        tests need is the boundary record. Tests that want a real audit
        chain don't use the boundary patch — they use ``dispatch``.
        """
        prompt = getattr(exchange, "prompt_context", "") or ""
        response = getattr(exchange, "response", "") or ""
        self.dispatch(prompt, response, model=getattr(exchange, "model", "cloud"))
        # Return the same dict shape the real callers see.
        from rvnd.llm_capture import CaptureResult, VerbosityLevel, IngestMode
        return CaptureResult(
            captured=True, pair_id="mock-pair",
            verbosity=VerbosityLevel.FULL,
            prompted_user=False,
            audit_id="mock-audit",
            mode=IngestMode.AGENTIC,
            skipped_reason="", oversight_bypassed=False,
        )

    def dispatch(self, prompt: str, response: str | None = None,
                  *, model: str = "mock-cloud-sonnet",
                  lock_pre_call: bool = False) -> str:
        """Record one cloud dispatch and return the synthetic response."""
        resp = response if response is not None else self._response_factory(prompt)
        in_tok = synthetic_token_count(prompt)
        out_tok = synthetic_token_count(resp)
        self.calls.append(CloudCall(
            prompt=prompt, response=resp, model=model,
            input_tokens=in_tok, output_tokens=out_tok,
            lock_pre_call=lock_pre_call,
        ))
        self.token_counter.record_cloud(prompt, resp)
        return resp


# ---------------------------------------------------------------------------
# Mock local LLM
# ---------------------------------------------------------------------------


@dataclass
class LocalCall:
    text: str
    categories: tuple[str, ...]
    model: str
    label: str
    failure_mode: str = ""


class MockLocalLLM:
    """Context manager — patches ``rvnd.local_llm.classify`` (and
    ``rvnd.local_llm.complete`` for completeness) at the import boundary.

    Configurable failure modes:

      * ``"ok"``                — return ``{ok: True, category: <classify_fn(text)>}``.
      * ``"503"``               — return ``{ok: False, error: "HTTP 503"}``.
      * ``"timeout"``           — return ``{ok: False, error: "timeout"}``.
      * ``"crash_mid_response"`` — return ``{ok: False, error: "stream broke"}``.
      * ``"partial_response"``  — return ``{ok: False, error: "unexpected response shape"}``.

    The classification function is per-model: ``classify_fn[model_id]
    -> Callable[[text], "pii_yes" | "pii_no" | "insufficient"]``. A
    missing model raises (mimics ensemble's "unavailable" path); a
    present model whose mode is one of the failure states above takes
    that branch.

    For the brief's "Tier C only" inputs, the default classify_fn maps
    text to ``pii_yes`` when the marker substring ``"patient"`` /
    ``"former CFO"`` etc. shows up; this lets the privacy test prove
    that Tier-C-only items still get gated.
    """

    def __init__(self, models: dict[str, str] | None = None,
                  classify_fn: dict[str, Callable[[str], str]] | None = None,
                  token_counter: TokenCounter | None = None):
        # default: every default-ensemble model present and "ok" with
        # deterministic mapper.
        self.models = dict(models or {
            "phi-3.5-mini-q4":          "ok",
            "qwen-2.5-coder-7b-q4":     "ok",
            "mistral-7b-instruct-q4":   "ok",
        })
        self.classify_fn = dict(classify_fn or {})
        self.calls: list[LocalCall] = []
        self.token_counter = token_counter or TokenCounter()
        self._stack: ExitStack | None = None

    # -- patch lifecycle ---------------------------------------------------

    def __enter__(self) -> "MockLocalLLM":
        self._stack = ExitStack()
        # Tier C ensemble reaches through rvnd.mcp_server.local_llm_classify;
        # we patch that function-level name (the ensemble does a lazy import).
        self._stack.enter_context(patch(
            "rvnd.mcp_server.local_llm_classify",
            side_effect=self._classify_side_effect,
        ))
        self._stack.enter_context(patch(
            "rvnd.local_llm.classify",
            side_effect=self._classify_low_side_effect,
        ))
        self._stack.enter_context(patch(
            "rvnd.local_llm.complete",
            side_effect=self._complete_side_effect,
        ))
        self._stack.enter_context(patch(
            "rvnd.local_llm.list_available",
            side_effect=self._list_available_side_effect,
        ))
        return self

    def __exit__(self, *exc):
        if self._stack is not None:
            self._stack.close()
            self._stack = None
        return False

    # -- classification fns ------------------------------------------------

    def _default_classify(self, text: str) -> str:
        """Default mapper used when no per-model classify_fn is supplied.

        Detects the synthetic Tier-C markers ("patient ward", "former CFO",
        "applicant born", "intern at", "witness who") and labels them PII.
        Everything else → ``pii_no``.
        """
        low = (text or "").lower()
        markers = ("patient", "former cfo", "applicant born",
                   "intern at", "witness who",
                   "jane.doe", "john.smith", "@", "iban", "card 4111",
                   "mrn", "steuer", "dni", "sk_live", "bearer")
        if any(m in low for m in markers):
            return "pii_yes"
        return "pii_no"

    def _resolve(self, model: str, text: str) -> tuple[str, str]:
        """Returns (label, failure_mode) for the supplied (model, text)."""
        mode = self.models.get(model, "missing")
        if mode == "missing":
            return ("insufficient", "missing")
        if mode != "ok":
            return ("insufficient", mode)
        fn = self.classify_fn.get(model, self._default_classify)
        try:
            return (fn(text), "")
        except Exception:
            return ("insufficient", "fn_error")

    # -- side effects (patch targets) --------------------------------------

    def _classify_side_effect(self, text, categories, folder_context="",
                                model="", **_):
        label, fail = self._resolve(model or "phi-3.5-mini-q4", text)
        self.token_counter.record_local()
        self.calls.append(LocalCall(
            text=text, categories=tuple(categories or ()),
            model=model or "phi-3.5-mini-q4",
            label=label, failure_mode=fail,
        ))
        if fail and fail != "missing":
            err_map = {
                "503":                 "HTTP 503",
                "timeout":             "timeout after 30s",
                "crash_mid_response":  "stream broke mid-response",
                "partial_response":    "unexpected response shape",
            }
            return {
                "ok": False,
                "error": err_map.get(fail, f"local-llm failed: {fail}"),
                "endpoint_host": "mock-local",
            }
        if fail == "missing":
            # Caller treats this as "model unavailable" — return error so the
            # ensemble records it as insufficient + any_unavailable.
            return {
                "ok": False,
                "error": f"model {model!r} not present in registry",
                "endpoint_host": "mock-local",
            }
        return {
            "ok": True, "category": label,
            "raw_response": label, "model_used": model,
            "latency_ms": 12, "endpoint_host": "mock-local",
        }

    def _classify_low_side_effect(self, text, categories, model=None, **_):
        # Lower-level rvnd.local_llm.classify shim — delegate.
        return self._classify_side_effect(
            text=text, categories=categories, model=model or "",
        )

    def _complete_side_effect(self, prompt, model=None, temperature=0.0,
                                max_tokens=512, **_):
        self.token_counter.record_local()
        mid = model or next(iter(self.models), "phi-3.5-mini-q4")
        status = self.models.get(mid, "missing")
        if status != "ok":
            err_map = {
                "503":                 "HTTP 503",
                "timeout":             "timeout after 30s",
                "crash_mid_response":  "stream broke mid-response",
                "partial_response":    "unexpected response shape",
                "missing":             f"model {mid!r} not present",
            }
            return {
                "ok":            False,
                "error":         err_map.get(status, f"local-llm failed: {status}"),
                "endpoint_host": "mock-local",
            }
        return {
            "ok": True, "response": f"[mock-local-complete-{len(prompt)}]",
            "model_used": mid,
            "latency_ms": 9, "endpoint_host": "mock-local",
            "usage": {"prompt_tokens": synthetic_token_count(prompt),
                       "completion_tokens": 4},
        }

    def _list_available_side_effect(self, **_):
        present = [m for m, status in self.models.items() if status != "missing"]
        return {
            "ok": True, "endpoint": "mock-local",
            "models": present, "reachable": True,
        }


# ---------------------------------------------------------------------------
# PII-leak assertion
# ---------------------------------------------------------------------------


_PII_FRAGMENT_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"     # email
    r"|DE\d{2}[0-9]{18}"                                    # full IBAN
    r"|\b\d{2}\s?\d{3}\s?\d{3}\s?\d{3}\b"                  # Steuer-ID
    r"|\b\d{8}[A-HJ-NP-TV-Z]\b"                            # DNI
    r"|\bMRN[: ]?[A-Z0-9-]{4,15}\b"                        # MRN
    r"|\bsk_(?:live|test)_[A-Za-z0-9_\-]{10,}\b"            # API key
    r"|\bBearer\s+[A-Za-z0-9._\-/+=]{16,}\b"               # bearer
    r"|\b(?:\d[ \-]?){13,19}\b"                            # candidate CC
    r"|\+?\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}"  # phone
)


def assert_no_pii_leaked(cloud_calls: list[CloudCall],
                          original_inputs: list[str]) -> None:
    """Walk every recorded cloud call and assert no PII span from the
    original inputs survived to the cloud prompt.

    Two-step check:
      1. Scan each cloud prompt with the PII regex; any match = leak.
      2. Cross-reference: any verbatim substring from an original Tier-B
         input that's also in a cloud prompt = leak.
    """
    leaked: list[str] = []
    for call in cloud_calls:
        body = call.prompt or ""
        for m in _PII_FRAGMENT_RE.finditer(body):
            leaked.append(f"regex:{m.group(0)} in cloud_prompt[:80]={body[:80]!r}")
    # Verbatim Tier-B substring cross-check.
    pii_phrases = {
        "jane.doe" "\x40" "example.com", "DE89370400440532013000",
        "12 345 678 902", "12345678Z", "AB1234567",
        "sk_" "live_abcdef0123456789ZZZZ",
        "4111 1111 1111 1111", "+49 30 1234 5678",
    }
    for call in cloud_calls:
        body = call.prompt or ""
        for phrase in pii_phrases:
            if phrase in body and phrase in " ".join(original_inputs):
                leaked.append(f"verbatim:{phrase}")
    assert not leaked, (
        f"PII LEAKED into cloud: {len(leaked)} leak(s). First 5: {leaked[:5]}"
    )

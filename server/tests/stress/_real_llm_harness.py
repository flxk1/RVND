# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real-LLM stress-harness gatekeeper + measurement utilities.

This module is the bridge between the mocked stress suite (which runs in
CI on every PR) and a *real* local-LLM endpoint (which only a configured
dev box with the registered models loaded can actually
exercise).

The two design rules:

  1. **Skip cleanly when no real LLM is reachable.**
     Tests under ``tests/stress/real/`` MUST NOT fail in environments
     without a local-LLM endpoint. They skip with a clear reason so the
     controller knows what would have happened.

  2. **Record, don't claim.**
     The point of the real-LLM suite is *measurement*. Tests print
     numbers (latency p50/p95/p99, agreement rate, INSUFFICIENT rate,
     wall-clock, cloud-token reduction in this run on this hardware
     against this workload) and only assert on *invariants* —
     no-PII-leaked, every-call-returned, sanity counts. They never
     assert "agreement should be >= X%" or "p95 latency should be <
     Yms". The controller reads the numbers and decides.

Opt-in flow::

    WORKSPACES_STRESS_USE_REAL_LLM=1 \\
    WORKSPACE_LOCAL_LLM_URL=http://localhost:1234/v1 \\
        pytest tests/stress/real/ -v -s

Without ``-s`` the printed measurements vanish into pytest's capture
buffer and the tests look like they did nothing useful. The runbook at
Real-provider stress tests require externally configured model credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Opt-in / availability probe
# ---------------------------------------------------------------------------


STRESS_OPT_IN_ENV = "WORKSPACES_STRESS_USE_REAL_LLM"
ENDPOINT_URL_ENV = "WORKSPACE_LOCAL_LLM_URL"


def _opt_in_set() -> bool:
    raw = os.environ.get(STRESS_OPT_IN_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _http_get_json(url: str, timeout: float = 5.0) -> tuple[bool, dict[str, Any], str]:
    """GET ``url`` and parse JSON. Returns (ok, body, error_string)."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return True, body, ""
    except urllib.error.URLError as e:
        return False, {}, f"unreachable: {getattr(e, 'reason', str(e))}"
    except Exception as e:  # noqa: BLE001 — best-effort probe
        return False, {}, f"{type(e).__name__}: {e}"


def real_llm_available() -> tuple[bool, str]:
    """Returns ``(is_available, reason_string_if_not)``.

    Checks, in order:

      1. ``WORKSPACES_STRESS_USE_REAL_LLM`` env var is set to 1/true/yes/on.
      2. ``WORKSPACE_LOCAL_LLM_URL`` is set.
      3. ``GET <url>/models`` returns 200 with a parseable JSON body.
      4. At least one model is registered under role ``lock-c`` or
         ``validator`` in the local model registry.
      5. Each registered model's id appears in the ``/models`` response.

    Any failure short-circuits with a human-readable reason explaining
    exactly which prereq missed. The string is what the skip marker will
    show in pytest output.
    """
    if not _opt_in_set():
        return False, (
            f"real LLM not available: opt-in env var "
            f"{STRESS_OPT_IN_ENV} not set (use '1' to enable)"
        )

    url = os.environ.get(ENDPOINT_URL_ENV, "").strip()
    if not url:
        return False, (
            f"real LLM not available: {ENDPOINT_URL_ENV} not set; "
            f"point it at an OpenAI-compatible URL "
            f"(e.g. http://localhost:1234/v1)"
        )

    models_url = url.rstrip("/") + "/models"
    ok, body, err = _http_get_json(models_url, timeout=5.0)
    if not ok:
        return False, (
            f"real LLM not available: GET {models_url} failed ({err}); "
            f"start your local LLM server first"
        )

    available_ids: set[str] = set()
    if isinstance(body, dict):
        data = body.get("data", []) if isinstance(body.get("data"), list) else []
        for row in data:
            if isinstance(row, dict) and row.get("id"):
                available_ids.add(str(row["id"]))

    if not available_ids:
        return False, (
            f"real LLM not available: {models_url} returned 200 but no "
            f"models in 'data[]'; check server config"
        )

    # Pull registered models from the registry.
    try:
        from rvnd import models_registry
    except Exception as e:  # noqa: BLE001
        return False, (
            f"real LLM not available: cannot import rvnd.models_registry "
            f"({type(e).__name__}: {e})"
        )

    registered: dict[str, list[str]] = {}
    for role in ("lock-c", "validator"):
        ids = models_registry.models_for_role(role)
        if ids:
            registered[role] = ids
    if not registered:
        return False, (
            "real LLM not available: no models registered under role "
            "'lock-c' or 'validator'; run `workspaces models register` first"
        )

    # Cross-check: every registered model must be present in /models.
    missing: list[str] = []
    for role, ids in registered.items():
        for mid in ids:
            if mid not in available_ids:
                missing.append(f"{mid} (role={role})")
    if missing:
        return False, (
            f"real LLM not available: registered model(s) not listed by "
            f"{models_url}: {missing[:3]}{'…' if len(missing) > 3 else ''}; "
            f"server has: {sorted(available_ids)[:5]}"
        )

    return True, ""


# ---------------------------------------------------------------------------
# Skip-or-run fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_llm_or_skip() -> dict[str, Any]:
    """Session-scoped fixture. Skips the test when the real LLM isn't
    reachable; otherwise yields a dict with the resolved endpoint URL and
    the registered model ids per role.

    The dict shape::

        {
          "url": "http://localhost:1234/v1",
          "models_by_role": {"lock-c": [...], "validator": [...]},
          "all_models": [...],
        }
    """
    ok, reason = real_llm_available()
    if not ok:
        pytest.skip(reason)
    # Re-resolve so the fixture surfaces a clean view.
    from rvnd import models_registry
    by_role: dict[str, list[str]] = {}
    for role in ("lock-c", "validator"):
        ids = models_registry.models_for_role(role)
        if ids:
            by_role[role] = ids
    all_models = sorted({mid for ids in by_role.values() for mid in ids})
    return {
        "url": os.environ.get(ENDPOINT_URL_ENV, ""),
        "models_by_role": by_role,
        "all_models": all_models,
    }


# ---------------------------------------------------------------------------
# Latency recorder
# ---------------------------------------------------------------------------


class RealLatencyRecorder:
    """Collect per-call latency samples and compute p50 / p95 / p99 at
    the end.

    The recorder is per-test (not session-wide) so two tests measuring
    against the same model produce independent reports. Bucketed by
    model id; calling :py:meth:`percentiles` against an unseen model
    returns an empty dict.
    """

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = {}

    def record(self, model: str, ms: float) -> None:
        if ms < 0:
            ms = 0.0
        self._samples.setdefault(model, []).append(float(ms))

    def models(self) -> list[str]:
        return sorted(self._samples.keys())

    def count(self, model: str) -> int:
        return len(self._samples.get(model, []))

    def percentiles(self, model: str) -> dict[str, float]:
        """Return a dict with p50, p95, p99, min, max, mean for the
        named model. Empty dict if no samples have been recorded."""
        s = self._samples.get(model, [])
        if not s:
            return {}
        ordered = sorted(s)
        return {
            "count": float(len(ordered)),
            "min":   ordered[0],
            "p50":   _percentile(ordered, 50),
            "p95":   _percentile(ordered, 95),
            "p99":   _percentile(ordered, 99),
            "max":   ordered[-1],
            "mean":  statistics.fmean(ordered),
        }

    def summary(self) -> str:
        """Multi-line printable summary across every recorded model."""
        if not self._samples:
            return "(no latency samples recorded)"
        out: list[str] = []
        out.append(
            f"{'model':<35} {'n':>4} {'min':>7} {'p50':>7} "
            f"{'p95':>7} {'p99':>7} {'max':>7} {'mean':>8}"
        )
        for m in self.models():
            p = self.percentiles(m)
            out.append(
                f"{m:<35} {int(p['count']):>4} "
                f"{p['min']:>6.1f}ms "
                f"{p['p50']:>6.1f}ms "
                f"{p['p95']:>6.1f}ms "
                f"{p['p99']:>6.1f}ms "
                f"{p['max']:>6.1f}ms "
                f"{p['mean']:>7.1f}ms"
            )
        return "\n".join(out)


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Linear-interpolation percentile over an already-sorted list."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_samples) - 1)
    if f == c:
        return sorted_samples[f]
    return sorted_samples[f] + (sorted_samples[c] - sorted_samples[f]) * (k - f)


# ---------------------------------------------------------------------------
# Ensemble agreement recorder
# ---------------------------------------------------------------------------


@dataclass
class _Disagreement:
    input_hash: str
    phi_label: str
    qwen_label: str


class RealEnsembleAgreementRecorder:
    """Track per-call ensemble outcomes between two models.

    Reports:
      - agreement count + rate
      - disagreement count + rate (with first 3 examples for inspection)
      - INSUFFICIENT count + rate
      - per-label tally
    """

    def __init__(self) -> None:
        self.total = 0
        self.agree = 0
        self.disagree = 0
        self.insufficient = 0
        self._disagreements: list[_Disagreement] = []
        self._label_tally: dict[str, int] = {}

    def record(self, input_hash: str, phi_label: str, qwen_label: str) -> None:
        self.total += 1
        self._label_tally[phi_label] = self._label_tally.get(phi_label, 0) + 1
        self._label_tally[qwen_label] = self._label_tally.get(qwen_label, 0) + 1
        if phi_label == "insufficient" or qwen_label == "insufficient":
            self.insufficient += 1
        if phi_label == qwen_label and phi_label != "insufficient":
            self.agree += 1
        elif phi_label != qwen_label:
            self.disagree += 1
            if len(self._disagreements) < 3:
                self._disagreements.append(_Disagreement(
                    input_hash=input_hash,
                    phi_label=phi_label,
                    qwen_label=qwen_label,
                ))

    def report(self) -> dict[str, Any]:
        denom = max(1, self.total)
        return {
            "total":              self.total,
            "agreement_count":    self.agree,
            "agreement_rate":     self.agree / denom,
            "disagreement_count": self.disagree,
            "disagreement_rate":  self.disagree / denom,
            "insufficient_count": self.insufficient,
            "insufficient_rate":  self.insufficient / denom,
            "label_tally":        dict(self._label_tally),
            "disagreement_examples": [
                {
                    "input_hash":  d.input_hash,
                    "phi":         d.phi_label,
                    "qwen":        d.qwen_label,
                }
                for d in self._disagreements
            ],
        }

    def summary(self) -> str:
        r = self.report()
        lines = [
            f"total            = {r['total']}",
            f"agreement        = {r['agreement_count']:>4}  "
            f"({100*r['agreement_rate']:>5.1f}%)",
            f"disagreement     = {r['disagreement_count']:>4}  "
            f"({100*r['disagreement_rate']:>5.1f}%)",
            f"insufficient     = {r['insufficient_count']:>4}  "
            f"({100*r['insufficient_rate']:>5.1f}%)",
            f"label_tally      = {r['label_tally']}",
        ]
        if r["disagreement_examples"]:
            lines.append("first disagreements:")
            for ex in r["disagreement_examples"]:
                lines.append(
                    f"  - input_hash={ex['input_hash'][:12]}  "
                    f"phi={ex['phi']:<12} qwen={ex['qwen']}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Real token counter
# ---------------------------------------------------------------------------


@dataclass
class RealTokenCounter:
    """Track real cloud-LLM token usage alongside local-LLM invocations.

    The cloud LLM is mocked at the boundary (we don't want the real-LLM
    stress suite to also charge an Anthropic bill); the *local* LLM is
    real. So "token reduction" means: how many synthetic cloud tokens
    were the local models able to avoid sending?
    """

    cloud_input_tokens: int = 0
    cloud_output_tokens: int = 0
    cloud_calls: int = 0
    local_invocations: int = 0
    local_wall_ms: float = 0.0

    def record_cloud(self, prompt: str, response: str) -> None:
        self.cloud_calls += 1
        self.cloud_input_tokens += max(1, len(prompt) // 4) if prompt else 0
        self.cloud_output_tokens += max(1, len(response) // 4) if response else 0

    def record_local(self, ms: float = 0.0) -> None:
        self.local_invocations += 1
        self.local_wall_ms += float(ms)

    @property
    def total_cloud_tokens(self) -> int:
        return self.cloud_input_tokens + self.cloud_output_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def stable_hash(text: str) -> str:
    """Deterministic short hash for surfacing disagreements without
    spilling input text into the report."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def now_ms() -> float:
    return time.time() * 1000.0

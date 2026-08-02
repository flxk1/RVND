# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""llama-cpp-python backend — in-process, no external daemon. The L1 binary path.

This is the canonical production backend: a GGUF model file ships inside the
distributable PyInstaller binary at `<bin_dir>/models/*.gguf`. llama-cpp-python
loads it at startup; classification calls go in-process with no HTTP, no
daemon, no external dependency.

Gracefully degrades when llama-cpp-python is not installed (e.g. during dev
on a machine without the wheel): returns a "not available" classification
without raising. The onboarding wizard's "Stage 2 — Install llama-cpp" step
is where the user is told to install the wheel.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


_PROMPT_TEMPLATE = """You are a privacy classifier at the boundary between local user context and a cloud LLM. Your job: only "clear info" is allowed to pass. Flag PII and confidential information.

Two categories trigger a flag:

1. PII (GDPR Art. 4) — information relating to an identified or IDENTIFIABLE natural person:
   - Direct identifiers (free-text names, addresses).
   - Pseudonymous identifiers ("User-A78F", "Subject-12") when re-identification is reasonably possible — still personal data per Art. 4(5).
   - Quasi-identifiers (combinations that single out one person, e.g. "the CEO of Company X who joined in 2024").
   - Special-category data per Art. 9 (health, ethnicity, political opinion, sexual orientation, trade-union membership).

2. CONFIDENTIAL information — context-specific terms the caller provides below. If the text mentions any of these terms, or paraphrases content tied to them, flag as confidential.

Caller's confidential terms (from the user's knowledge graph):
%s

Do NOT flag (return type=none) for:
- Genuinely aggregate statistics with no re-identification path.
- Methodology, code, generic descriptions that don't identify anyone or reveal confidential context.
- Public, non-sensitive information.

When uncertain, prefer flagging. False negatives leak data to the cloud; false positives only cause friction.

Examples:
1. "User-A78F purchased product X on 2026-04-12"
   -> {"contains_pii": true, "type": "name", "confidence": 0.85, "reason": "pseudonymous identifier per Art. 4(5)"}
2. "Project Vesper will ship Phase 3 in June" (confidential terms include "Vesper")
   -> {"contains_pii": true, "type": "confidential", "confidence": 0.95, "reason": "confidential term 'Vesper' from KG context"}
3. "Cluster analysis of 4,200 users found 12%% high-spend"
   -> {"contains_pii": false, "type": "none", "confidence": 0.85, "reason": "aggregate, no re-identification"}
4. "Patient NK-44 diagnosed with NSTEMI on June 3"
   -> {"contains_pii": true, "type": "health", "confidence": 0.95, "reason": "Art. 9 health data + pseudonymous identifier"}

Respond with ONE JSON object only, no preamble:
{"contains_pii": true|false, "type": "name"|"health"|"financial"|"membership"|"confidential"|"none", "confidence": 0.0-1.0, "reason": "short reason"}

Text:
---
%s
---
JSON:"""


class LlamaCppBackend:
    """In-process GGUF model via llama-cpp-python."""

    # Class-level cache so we don't reload the model on every classify() call.
    # Keyed by absolute model path.
    _model_cache: dict[str, Any] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 2048,
        n_threads: int | None = None,
        verbose: bool = False,
    ):
        self.model_path = os.path.expanduser(os.path.expandvars(model_path))
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.verbose = verbose
        self._llama: Any | None = None
        self._unavailable_reason: str | None = None
        # Don't load lazily inside classify — surface availability up front
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._llama is not None or self._unavailable_reason is not None:
            return

        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
        except ImportError:
            self._unavailable_reason = (
                "llama-cpp-python is not installed. "
                "Run: pip install llama-cpp-python"
            )
            return

        if not Path(self.model_path).exists():
            self._unavailable_reason = (
                f"model file not found at {self.model_path}. "
                "Run the onboarding wizard to download the bundled model."
            )
            return

        with LlamaCppBackend._lock:
            cached = LlamaCppBackend._model_cache.get(self.model_path)
            if cached is not None:
                self._llama = cached
                return

            try:
                self._llama = Llama(
                    model_path=self.model_path,
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                    verbose=self.verbose,
                )
                LlamaCppBackend._model_cache[self.model_path] = self._llama
            except Exception as e:  # llama-cpp-python can raise various errors
                self._unavailable_reason = f"failed to load model: {e}"

    def classify(self, text: str, context: str = "") -> dict:
        """Classify `text` for PII + confidential content.

        Args:
            text: the text to classify.
            context: optional confidential-terms string (typically a newline-
                separated list of KG-sourced terms, names, projects). When
                empty, only the PII half of the prompt is exercised.
        """
        if not text or not text.strip():
            return {"contains_pii": False, "type": "none", "confidence": 1.0, "reason": "empty"}

        if self._llama is None:
            # FAIL CLOSED — privacy gate must not silently pass prompts to the cloud
            # just because the local validator is broken. Caller (tier_c → upstream
            # policy) routes per LOCAL_LLM_ON_INSUFFICIENT.
            return {
                "contains_pii": True,
                "type": "none",
                "confidence": 1.0,
                "reason": f"fail-closed: backend unavailable: {self._unavailable_reason or 'unknown'}",
            }

        context_section = context.strip() if context and context.strip() else "(none — caller did not pass any KG context)"
        prompt = _PROMPT_TEMPLATE % (context_section, text)
        try:
            result = self._llama(
                prompt,
                max_tokens=128,
                temperature=0.0,
                stop=["\n\n", "---"],
                echo=False,
            )
            raw = result["choices"][0]["text"].strip()
        except Exception as e:
            # FAIL CLOSED on inference failure (OOM, model crash, timeout, etc.).
            return {
                "contains_pii": True,
                "type": "none",
                "confidence": 1.0,
                "reason": f"fail-closed: inference failed: {e}",
            }

        return _parse_classification_response(raw)

    def is_available(self) -> bool:
        return self._llama is not None

    def describe(self) -> str:
        if self._llama is not None:
            return f"llama_cpp: model={self.model_path} n_ctx={self.n_ctx}"
        return f"llama_cpp: UNAVAILABLE ({self._unavailable_reason})"


def _parse_classification_response(raw: str) -> dict:
    """Extract the first {...} object from the model's response.

    Parse failures fail CLOSED — a privacy gate that silently allows prompts
    through when the local validator can't be parsed is worse than one that
    over-gates and asks the human. Upstream policy (LOCAL_LLM_ON_INSUFFICIENT)
    decides whether to escalate to cloud, escalate to human, or refuse.
    """
    import json

    start = raw.find("{")
    if start == -1:
        return {"contains_pii": True, "type": "none", "confidence": 1.0,
                "reason": "fail-closed: model output missing JSON"}
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start : i + 1])
                    return {
                        "contains_pii": bool(obj.get("contains_pii", False)),
                        "type": str(obj.get("type", "none")),
                        "confidence": float(obj.get("confidence", 0.7)),
                        "reason": str(obj.get("reason", ""))[:200],
                    }
                except (json.JSONDecodeError, ValueError, TypeError):
                    return {"contains_pii": True, "type": "none", "confidence": 1.0,
                            "reason": "fail-closed: model output malformed JSON"}
    return {"contains_pii": True, "type": "none", "confidence": 1.0,
            "reason": "fail-closed: model output unbalanced braces"}

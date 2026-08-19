# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""ONNX Runtime GenAI backend — in-process, no external daemon.

Loads an ONNX GenAI model directory (``genai_config.json`` + weights) via
onnxruntime-genai and serves the same classify() contract as the llama_cpp
backend, sharing its prompt template and response parser. This is the
static-binary path: onnxruntime-genai packages into a PyInstaller bundle
without llama-cpp-python.

Gracefully degrades when onnxruntime-genai is not installed or the model
directory is missing: classify() fails closed (flags the text) so the
privacy gate never silently passes unscanned content.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .llama_cpp import _PROMPT_TEMPLATE, _parse_classification_response

# Budget for the model's JSON answer; the parser extracts the first {...}
# object, so trailing continuation tokens are harmless.
_MAX_NEW_TOKENS = 128


class OnnxGenaiBackend:
    """In-process ONNX GenAI model via onnxruntime-genai."""

    # Class-level cache so the multi-GB model loads once per process.
    # Keyed by resolved model directory.
    _model_cache: dict[str, tuple[Any, Any]] = {}
    _lock = threading.Lock()

    def __init__(self, model_dir: str):
        self.model_dir = os.path.expanduser(os.path.expandvars(model_dir))
        self._og: Any | None = None
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._unavailable_reason: str | None = None
        # Don't load lazily inside classify — surface availability up front
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._model is not None or self._unavailable_reason is not None:
            return

        try:
            import onnxruntime_genai as og  # type: ignore[import-not-found]
        except ImportError:
            self._unavailable_reason = (
                "onnxruntime-genai is not installed. "
                "Run: pip install onnxruntime-genai"
            )
            return

        d = Path(self.model_dir)
        if not d.is_dir():
            self._unavailable_reason = f"model directory not found at {d}"
            return
        if not (d / "genai_config.json").exists():
            self._unavailable_reason = (
                f"no genai_config.json in {d} — not an ONNX GenAI model directory"
            )
            return

        with OnnxGenaiBackend._lock:
            cached = OnnxGenaiBackend._model_cache.get(self.model_dir)
            if cached is not None:
                self._model, self._tokenizer = cached
                self._og = og
                return

            try:
                model = og.Model(self.model_dir)
                tokenizer = og.Tokenizer(model)
            except Exception as e:  # onnxruntime-genai raises various errors
                self._unavailable_reason = f"failed to load model: {e}"
                return
            OnnxGenaiBackend._model_cache[self.model_dir] = (model, tokenizer)
            self._model, self._tokenizer = model, tokenizer
            self._og = og

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

        if self._model is None:
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
            og = self._og
            input_ids = self._tokenizer.encode(prompt)
            params = og.GeneratorParams(self._model)
            # Greedy decoding — classification must be deterministic. This
            # overrides the sampling defaults in the model dir's
            # genai_config.json "search" section.
            params.set_search_options(
                do_sample=False,
                max_length=len(input_ids) + _MAX_NEW_TOKENS,
            )
            generator = og.Generator(self._model, params)
            generator.append_tokens(input_ids)
            while not generator.is_done():
                generator.generate_next_token()
            seq = generator.get_sequence(0)
            raw = self._tokenizer.decode(seq[len(input_ids):])
        except Exception as e:
            # FAIL CLOSED on inference failure (OOM, model crash, timeout, etc.).
            return {
                "contains_pii": True,
                "type": "none",
                "confidence": 1.0,
                "reason": f"fail-closed: inference failed: {e}",
            }

        return _parse_classification_response(raw.strip())

    def is_available(self) -> bool:
        return self._model is not None

    def describe(self) -> str:
        if self._model is not None:
            return f"onnx_genai: model_dir={self.model_dir}"
        return f"onnx_genai: UNAVAILABLE ({self._unavailable_reason})"

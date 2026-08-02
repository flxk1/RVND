# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Backend factory + abstract base class.

Spec strings:
    "mock"                              — deterministic, no model
    "llama_cpp:/path/to/model.gguf"     — in-process via llama-cpp-python
    "onnx_genai:/path/to/model_dir"     — ONNX Runtime GenAI

The Ollama HTTP backend was removed in 0.6.5 per project policy. Use
llama_cpp or onnx_genai for in-process inference.
"""

from __future__ import annotations

from typing import Protocol


class BackendError(Exception):
    """Raised when a backend cannot be constructed or used."""


class LLMBackend(Protocol):
    """Minimal contract every backend implements.

    Returns a classification dict on call — keys:
        contains_pii: bool
        type: "name" | "health" | "financial" | "membership" | "confidential" | "none"
        confidence: float 0.0-1.0
        reason: str (short)

    The "confidential" type is for context-specific terms the caller passes via
    `context` — typically a KG-sourced confidential-terms list.

    Should never raise; on failure return a "contains_pii: False, type: 'none'"
    dict with reason explaining the degraded state.
    """

    def classify(self, text: str, context: str = "") -> dict:
        ...

    def is_available(self) -> bool:
        """Lightweight health check. Returns True if the backend is ready."""
        ...

    def describe(self) -> str:
        """Human-readable description of this backend instance."""
        ...


def make_local_llm(spec: str) -> LLMBackend:
    """Build a backend instance from a spec string.

    Raises BackendError on:
    - unknown spec prefix
    - required deps missing
    - malformed spec string

    Lazy-imports backend implementations so e.g. importing this module doesn't
    require llama-cpp-python to be installed.
    """
    if not spec:
        raise BackendError(
            "empty backend spec. Valid prefixes: "
            "'mock', 'llama_cpp:<path>', 'onnx_genai:<dir>'"
        )

    if spec == "mock":
        from .mock import MockBackend
        return MockBackend()

    if ":" not in spec:
        raise BackendError(
            f"backend spec '{spec}' is missing argument. "
            "Expected '<backend>:<arg>' e.g. 'llama_cpp:/path/to/model.gguf'"
        )

    prefix, _, arg = spec.partition(":")

    if prefix == "llama_cpp":
        from .llama_cpp import LlamaCppBackend
        return LlamaCppBackend(model_path=arg)

    if prefix == "ollama":
        raise BackendError(
            "the 'ollama' backend was removed in 0.6.5 per project policy. "
            "Use 'llama_cpp:<path>' or 'onnx_genai:<dir>' instead."
        )

    if prefix == "onnx_genai":
        from .onnx_genai import OnnxGenaiBackend
        return OnnxGenaiBackend(model_dir=arg)

    raise BackendError(
        f"unknown backend prefix '{prefix}'. "
        "Valid: 'mock', 'llama_cpp', 'onnx_genai'."
    )

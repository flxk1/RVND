# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local-LLM backends for Tier C semantic PII check.

Implementations of the LLM contract used by Tier C. The factory
:func:`make_local_llm` picks a backend by spec string.

Available backends:

    mock                          Deterministic, no model. Test-only.
    llama_cpp:<path-to-gguf>      In-process via llama-cpp-python. The L1
                                  binary path — model bundles into the
                                  distributable binary. No external deps.
    onnx_genai:<dir>              ONNX Runtime GenAI — fully-static-binary
                                  path. Stubbed; wired when onnxruntime-genai
                                  is shipped with the binary.

All backends implement the same minimal contract — pluggable, swappable
without changing Tier C's caller code.

Per the MODELS.md sovereignty principle: no cloud LLM, no external service
dependency, no telemetry. The llama_cpp backend with bundled GGUF is the
canonical L1 production path.

Note: the Ollama HTTP backend was removed in 0.6.5 per project policy.
"""

from .factory import BackendError, LLMBackend, make_local_llm
from .mock import MockBackend

__all__ = [
    "BackendError",
    "LLMBackend",
    "MockBackend",
    "make_local_llm",
]

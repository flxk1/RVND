# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Ollama HTTP backend — FALLBACK only.

Adds an external dependency (the Ollama daemon). Kept for development
flexibility and as a path for users who already have Ollama running. NOT the
production path — the production path is llama_cpp with bundled GGUF.

This module replaces the older `tier_c_ollama.py` and matches the
backend-factory contract.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


_DEFAULT_HOST = "http://localhost:11434"
_DEFAULT_TIMEOUT = 5.0

_PROMPT_TEMPLATE = """You are a privacy classifier. Determine whether the following text contains personally identifiable information that a typical regex (email, phone, IBAN, SSN-pattern) would miss but a human reader would recognise. Examples include: free-text names of identifiable individuals, descriptions of medical conditions tied to a specific person, financial circumstances of an identifiable person, sensitive group membership of an identifiable person.

Respond with a single JSON object on one line, no preamble:
{"contains_pii": true|false, "type": "name"|"health"|"financial"|"membership"|"none", "confidence": 0.0-1.0, "reason": "short reason"}

Text to classify:
---
%s
---"""


class OllamaBackend:
    """HTTP backend to a local Ollama daemon."""

    def __init__(self, model: str = "llama3.2:3b-instruct-q4_K_M", base_url: str | None = None):
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_HOST") or _DEFAULT_HOST).rstrip("/")
        try:
            self.timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self.timeout = _DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url + "/api/tags")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def classify(self, text: str, context: str = "") -> dict:
        # `context` is accepted for interface parity with the other backends
        # backends but is not yet plumbed into the Ollama prompt path (Ollama
        # is the fallback backend; production Tier C goes through llama_cpp).
        _ = context
        if not text or not text.strip():
            return {"contains_pii": False, "type": "none", "confidence": 1.0, "reason": "empty"}

        if not self.is_available():
            return {
                "contains_pii": False,
                "type": "none",
                "confidence": 0.0,
                "reason": f"ollama daemon unreachable at {self.base_url}",
            }

        prompt = _PROMPT_TEMPLATE % text
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 128},
        }

        try:
            req = urllib.request.Request(
                self.base_url + "/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            response = json.loads(raw)
            text_out = response.get("response", "")
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
            return {"contains_pii": False, "type": "none", "confidence": 0.0,
                    "reason": "ollama call failed"}

        return _parse_classification_response(text_out)

    def describe(self) -> str:
        return f"ollama: model={self.model} base_url={self.base_url} (FALLBACK — external dep)"


def _parse_classification_response(raw: str) -> dict:
    """Extract the first {...} object from the model's response."""
    start = raw.find("{")
    if start == -1:
        return {"contains_pii": False, "type": "none", "confidence": 0.0,
                "reason": "model output missing JSON"}
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
                    return {"contains_pii": False, "type": "none", "confidence": 0.0,
                            "reason": "model output malformed JSON"}
    return {"contains_pii": False, "type": "none", "confidence": 0.0,
            "reason": "model output unbalanced braces"}

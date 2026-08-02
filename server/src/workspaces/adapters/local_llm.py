# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local-model adapter routed through RVND's audited completion path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adapter_loader import AdapterDeclaration


@dataclass
class LocalLlmAdapter:
    kind: str = "local_llm"
    decl: AdapterDeclaration = None  # type: ignore[assignment]

    def dispatch(self, payload: dict[str, Any], *,
                 folder_context: str | None = None) -> dict[str, Any]:
        if not folder_context:
            raise ValueError("local_llm adapter requires folder_context")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("local_llm adapter requires a non-empty prompt")

        cfg = self.decl.kind_config
        from ..mcp_impl import local_llm_complete

        return local_llm_complete(
            prompt=prompt,
            folder_context=folder_context,
            model=str(payload.get("model") or cfg.get("model") or ""),
            temperature=float(payload.get("temperature", cfg.get("temperature", 0.0))),
            max_tokens=int(payload.get("max_tokens", cfg.get("max_tokens", 512))),
            capture=bool(payload.get("capture", cfg.get("capture", True))),
        )


def build(decl: AdapterDeclaration) -> LocalLlmAdapter:
    return LocalLlmAdapter(decl=decl)

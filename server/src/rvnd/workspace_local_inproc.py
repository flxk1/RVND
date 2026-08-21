# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""In-process local completion — run a pulled GGUF directly, no daemon.

The installer pulls local GGUFs (qwen-coder-7b, phi-3.5-mini, mistral-7b)
that load in-process via ``llama-cpp-python`` — the same mechanism the coding
companion uses, so one set of local models serves both. This module gives the
workspace cascade an in-process rung with the SAME call shape as the HTTP transport
(:func:`rvnd.local_llm.complete_via`), so ``run_cascade`` can mix in-process
local tiers and an HTTP/cloud tier behind one ``completer``.

A tier is in-process when its ``url`` is the sentinel ``inproc`` (or
``inproc:<path>``); the tier's ``model`` is then either a registered model id
(resolved to its GGUF via ``models_registry``) or a direct path to a ``.gguf``.

Loud, not silent: if ``llama-cpp-python`` is not installed or the GGUF is not on
disk, this returns ``{ok: False, error: ...}`` so the cascade defers to the next
tier with a recorded reason — never a fabricated answer.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

INPROC_SENTINEL = "inproc"

# Module-level model cache: a GGUF is expensive to load, so keep one handle per
# resolved path for the life of the process (keyed by path).
_LLAMA_CACHE: dict[str, Any] = {}


def is_inproc(url: str) -> bool:
    """Whether a tier ``url`` selects the in-process transport."""
    u = (url or "").strip()
    return u == INPROC_SENTINEL or u.startswith(INPROC_SENTINEL + ":")


def resolve_gguf(model: str) -> str:
    """Resolve a tier ``model`` to a GGUF path on disk.

    Accepts a direct path (``…/x.gguf``) or a registered model id (looked up in
    ``models_registry`` → ``artifact_path``). Returns "" if it cannot resolve to
    an existing file — the caller reports that loudly.
    """
    m = (model or "").strip()
    if not m:
        return ""
    p = Path(m).expanduser()
    if p.suffix == ".gguf" and p.exists():
        return str(p)
    # treat as a registered id
    try:
        from . import models_registry
        for entry in models_registry.list_models():
            if entry.id == m and entry.artifact_path:
                ap = Path(entry.artifact_path).expanduser()
                if ap.exists():
                    return str(ap)
    except Exception:
        pass
    # last chance: maybe it was a path that just doesn't exist yet
    return str(p) if p.exists() else ""


def _load(path: str, *, n_ctx: int = 4096) -> Any:
    """Load (and cache) a llama_cpp model. Raises ImportError/OSError loudly."""
    cached = _LLAMA_CACHE.get(path)
    if cached is not None:
        return cached
    from llama_cpp import Llama  # raises ImportError if not installed
    model = Llama(model_path=path, n_ctx=n_ctx, verbose=False)
    _LLAMA_CACHE[path] = model
    return model


def complete_inproc(
    url: str,
    model: str,
    prompt: str,
    *,
    api_key: str = "",          # unused; kept for transport-signature parity
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: float | None = None,   # unused in-process; parity only
) -> dict[str, Any]:
    """One in-process chat completion. Same return contract as
    :func:`rvnd.local_llm.complete_via`:
    ``{ok, response, model_used, latency_ms, usage}`` or ``{ok: False, error}``.
    """
    path = resolve_gguf(model)
    if not path:
        return {"ok": False,
                "error": (f"in-process model {model!r} not found on disk "
                          f"(register it or pass a .gguf path; run the installer "
                          f"pull_models.sh)")}
    try:
        llm = _load(path)
    except ImportError:
        return {"ok": False,
                "error": ("llama-cpp-python not installed; the in-process local "
                          "tier cannot run. remedy: pip install llama-cpp-python")}
    except Exception as e:  # noqa: BLE001 — surface load failure, defer cleanly
        return {"ok": False, "error": f"model load failed: {type(e).__name__}: {e}"}

    started = time.time()
    try:
        out = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens)
        elapsed_ms = int((time.time() - started) * 1000)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"generation failed: {type(e).__name__}: {e}"}

    try:
        text = out["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return {"ok": False,
                "error": f"unexpected in-process response: {type(e).__name__}: {e}",
                "raw_response": str(out)[:500]}
    usage = out.get("usage", {}) if isinstance(out, dict) else {}
    return {"ok": True, "response": text, "model_used": Path(path).stem,
            "latency_ms": elapsed_ms, "usage": usage, "endpoint_host": "in-process"}


def workspace_completer(url: str, model: str, prompt: str, **kw) -> dict[str, Any]:
    """Dispatching completer for ``run_cascade``: route each tier by its ``url``.

    In-process tier (``url`` is the ``inproc`` sentinel) -> :func:`complete_inproc`;
    any other tier -> the HTTP/OpenAI-compatible transport. One ``completer`` for
    a cascade that mixes in-process local rungs with an HTTP or cloud rung.
    """
    if is_inproc(url):
        return complete_inproc(url, model, prompt, **kw)
    from .local_llm import complete_via
    return complete_via(url, model, prompt, **kw)

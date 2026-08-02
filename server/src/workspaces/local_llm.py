# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Local-LLM route — OpenAI-compatible HTTP client (0.6.7+).

Workspace exposes the *route*, not the *model*. Users bring their own local
LLM endpoint (llama.cpp server, vllm, LM Studio, Ollama, any
OpenAI-compatible HTTP). Workspace adds MCP tools that route requests there
with the same audit + capture floor as cloud calls.

Configuration (env vars; per-folder policy override planned for 0.7):

    WORKSPACE_LOCAL_LLM_URL=http://localhost:1234/v1
    WORKSPACE_LOCAL_LLM_MODEL=phi-3.5-mini
    WORKSPACE_LOCAL_LLM_API_KEY=                          # usually unset
    WORKSPACE_LOCAL_LLM_TIMEOUT_SECS=30

Audit posture: every local-LLM call gets a ``capture_llm`` event written
to the folder's mutation log with ``model_provider="local"``. The
endpoint host is recorded (port stripped to reduce log noise). The
lock is NOT bypassed by local routing — users deploying local LLMs to
keep data off the wire still benefit from the gate; the audit proves it.

This module is dependency-light: uses stdlib ``urllib`` for the HTTP
call so Workspace doesn't pull in requests/httpx as a hard dependency.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


DEFAULT_URL_ENV = "WORKSPACE_LOCAL_LLM_URL"
DEFAULT_MODEL_ENV = "WORKSPACE_LOCAL_LLM_MODEL"
DEFAULT_API_KEY_ENV = "WORKSPACE_LOCAL_LLM_API_KEY"
DEFAULT_TIMEOUT_ENV = "WORKSPACE_LOCAL_LLM_TIMEOUT_SECS"


def _endpoint_url() -> str | None:
    return os.environ.get(DEFAULT_URL_ENV)


def _default_model() -> str:
    return os.environ.get(DEFAULT_MODEL_ENV, "")


def _api_key() -> str:
    return os.environ.get(DEFAULT_API_KEY_ENV, "")


def _timeout_secs() -> float:
    try:
        return float(os.environ.get(DEFAULT_TIMEOUT_ENV, "30"))
    except ValueError:
        return 30.0


def _host_only(url: str) -> str:
    """Reduce a URL to just the host (no port) for audit-log noise reduction."""
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _is_secure_or_loopback(url: str) -> bool:
    """True if sending a credential to ``url`` keeps it off the public wire:
    HTTPS (encrypted) or a loopback host (never leaves the machine)."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if (p.scheme or "").lower() == "https":
        return True
    return (p.hostname or "").lower() in _LOOPBACK_HOSTS


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    """Stdlib HTTP POST. Returns parsed JSON or raises on transport/HTTP error."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def complete_via(
    url: str,
    model_id: str,
    prompt: str,
    *,
    api_key: str = "",
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: float | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One OpenAI-compatible chat completion against an EXPLICIT endpoint.

    The transport primitive both the local route (:func:`complete`) and any
    higher tier (e.g. a cloud cascade) share — one wire shape, one error
    contract, exercised by one test surface. Same return dict as
    :func:`complete`."""
    # D2: never put a credential on a plaintext wire. A non-HTTPS URL carrying an
    # api_key would send the Bearer token in cleartext — fail CLOSED. (Loopback
    # localhost is exempt: a local model on 127.0.0.1 over http never leaves the
    # machine, and the local route legitimately runs keyless http there.)
    if api_key and not _is_secure_or_loopback(url):
        return {"ok": False,
                "error": ("refusing to send an API key over a non-HTTPS endpoint "
                          f"({_host_only(url)}) — use an https:// URL"),
                "endpoint_host": _host_only(url)}
    headers = dict(extra_headers or {})
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # OpenAI Chat Completions shape — supported by llama.cpp server, vllm,
    # LM Studio, Ollama (via /v1/chat/completions), and the cloud providers.
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    chat_url = url.rstrip("/") + "/chat/completions"
    started = time.time()
    try:
        result = _post_json(chat_url, body, headers,
                            timeout if timeout is not None else _timeout_secs())
        elapsed_ms = int((time.time() - started) * 1000)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"LLM HTTP {e.code}: {e.reason}",
                "endpoint_host": _host_only(url)}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"LLM unreachable: {e.reason}",
                "endpoint_host": _host_only(url)}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False,
                "error": f"LLM call failed: {type(e).__name__}: {e}",
                "endpoint_host": _host_only(url)}
    try:
        response_text = result["choices"][0]["message"]["content"]
        model_used = result.get("model", model_id)
    except (KeyError, IndexError, TypeError) as e:
        return {"ok": False,
                "error": f"unexpected response shape: {type(e).__name__}: {e}",
                "endpoint_host": _host_only(url),
                "raw_response": str(result)[:500]}
    return {"ok": True, "response": response_text, "model_used": model_used,
            "latency_ms": elapsed_ms, "endpoint_host": _host_only(url),
            "usage": result.get("usage", {})}


def complete(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Route a completion request to the configured local-LLM endpoint.

    Returns:
        ``{ok, response, model_used, latency_ms, endpoint_host}`` on success
        ``{ok: false, error, endpoint_host?}`` on failure
    """
    url = _endpoint_url()
    if not url:
        return {
            "ok": False,
            "error": (
                f"no local-LLM endpoint configured. Set "
                f"{DEFAULT_URL_ENV} to an OpenAI-compatible URL "
                f"(e.g. http://localhost:1234/v1)."
            ),
        }
    model_id = model or _default_model()
    if not model_id:
        return {
            "ok": False,
            "error": (
                f"no model configured. Set {DEFAULT_MODEL_ENV} or pass "
                f"model= in the call."
            ),
            "endpoint_host": _host_only(url),
        }
    return complete_via(url, model_id, prompt, api_key=_api_key(),
                        temperature=temperature, max_tokens=max_tokens)


def classify(
    text: str,
    categories: list[str],
    model: str | None = None,
) -> dict[str, Any]:
    """Specialised classification helper: route a categorisation request.

    Wraps ``complete`` with a prompt that asks the model to pick one of
    ``categories`` for ``text``. Returns ``{ok, category, raw_response,
    model_used, latency_ms}``.

    Used downstream by tier_c semantic check once the local model is wired.
    """
    cat_list = ", ".join(f'"{c}"' for c in categories)
    prompt = (
        f"Classify the following text into exactly one of these categories: "
        f"{cat_list}.\n\n"
        f"Return ONLY the category name, nothing else.\n\n"
        f"Text: {text!r}\n\nCategory:"
    )
    result = complete(prompt, model=model, temperature=0.0, max_tokens=64)
    if not result.get("ok"):
        return result

    # Extract the chosen category — be forgiving about quoting / casing.
    raw = result["response"].strip().strip('"').strip("'")
    chosen = None
    for cat in categories:
        if cat.lower() == raw.lower():
            chosen = cat
            break
    if chosen is None:
        # Fallback: substring match
        for cat in categories:
            if cat.lower() in raw.lower():
                chosen = cat
                break

    return {
        "ok": True,
        "category": chosen,
        "raw_response": raw,
        "model_used": result.get("model_used"),
        "latency_ms": result.get("latency_ms"),
        "endpoint_host": result.get("endpoint_host"),
    }


def resolve_models_for_role(role: str) -> list[str]:
    """Return the registered local-model ids for a Workspace role.

    Thin wrapper over :func:`workspaces.models_registry.models_for_role` so
    consumers that already import ``workspaces.local_llm`` don't have to also
    import the registry module. Used by ``workspaces.lock.core.tier_c_semantic_check``
    (role="lock-c") and any future role-driven dispatcher (role="validator",
    role="code-fix", etc.).

    Empty list when no models are registered for the role — caller decides
    whether to fall back to hard-coded defaults, refuse, or escalate.
    """
    from . import models_registry
    return models_registry.models_for_role(role)


def list_available() -> dict[str, Any]:
    """Probe the configured endpoint for available models.

    Returns ``{ok, endpoint, models, reachable}``.
    """
    url = _endpoint_url()
    if not url:
        return {
            "ok": False,
            "error": f"no local-LLM endpoint configured ({DEFAULT_URL_ENV})",
            "reachable": False,
        }

    api_key = _api_key()
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    models_url = url.rstrip("/") + "/models"
    try:
        result = _get_json(models_url, headers, _timeout_secs())
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "endpoint": _host_only(url),
            "error": f"unreachable: {e.reason}",
            "reachable": False,
        }
    except Exception as e:
        return {
            "ok": False,
            "endpoint": _host_only(url),
            "error": f"{type(e).__name__}: {e}",
            "reachable": False,
        }

    # OpenAI /v1/models response: {"object": "list", "data": [{"id": ...}, ...]}
    models = [m.get("id") for m in result.get("data", []) if m.get("id")]
    return {
        "ok": True,
        "endpoint": _host_only(url),
        "models": models,
        "reachable": True,
    }

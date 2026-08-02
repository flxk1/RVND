#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Egress import guard — a preventive invariant that keeps the fail-closed
egress proxy (workspaces/lock/egress_proxy.py) the only path to a cloud LLM.

The proxy enforces the tier scan, oversight and per-track credential injection
on outbound requests to api.anthropic.com / api.openai.com / api.cohere.ai /
generativelanguage.googleapis.com. That guarantee only holds while no other
module reaches those providers directly. Today no module does; this guard exists
to keep it that way — a bypass would ship a cloud path around the gate.

It fails closed on any module outside the sanctioned allowlist that:

  * imports a cloud-LLM SDK — anthropic, openai, cohere,
    google.generativeai / google.genai (Google's SDK, not onnxruntime_genai);
  * instantiates one of those SDKs' clients via an attribute call
    (e.g. ``anthropic.Anthropic(...)``, ``openai.OpenAI(...)``);
  * dynamically imports one with a string literal —
    ``__import__("anthropic")`` or ``importlib.import_module("openai")``;
  * hardcodes a provider base URL literal (api.anthropic.com, api.openai.com,
    api.cohere.ai, generativelanguage.googleapis.com).

It walks the AST rather than grepping source lines, so a URL inside a comment
or an unrelated ``genai`` package name does not trip it. Stdlib only — no third
-party deps — so CI runs it without a pip install. Dynamic imports via computed
strings and hostnames built by string concatenation are beyond static analysis
and are not caught.

  PYTHONPATH=server/src python3 scripts/egress_import_guard.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Cloud-LLM SDK top-level modules that must only be reached through the proxy.
# google.generativeai / google.genai are matched as dotted paths so the local
# onnxruntime_genai backend is not mistaken for Google's SDK.
_SDK_MODULES = frozenset({"anthropic", "openai", "cohere"})
_SDK_DOTTED = (("google", "generativeai"), ("google", "genai"))

# Provider base-URL hosts. A string literal containing any of these in a
# non-sanctioned module is a hardcoded cloud endpoint.
_PROVIDER_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "api.cohere.ai",
    "generativelanguage.googleapis.com",
)

# Sanctioned modules, path-anchored relative to server/src (never bare
# basenames — a same-named module elsewhere must not inherit the exemption).
# egress_proxy.py is the gate itself; it names the hosts and speaks the
# providers' wire protocol by design.
_ALLOWLIST = frozenset({
    "workspaces/lock/egress_proxy.py",
})

_SRC_ROOT = Path("server/src")


def _dotted_from(node: ast.ImportFrom) -> tuple[str, ...]:
    return tuple((node.module or "").split("."))


def _is_sdk_import(node: ast.AST) -> str | None:
    """The offending SDK name if this import/import-from targets a cloud SDK."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            head, *rest = alias.name.split(".")
            if head in _SDK_MODULES:
                return alias.name
            for pkg, sub in _SDK_DOTTED:
                if head == pkg and rest[:1] == [sub]:
                    return alias.name
        return None
    if isinstance(node, ast.ImportFrom):
        parts = _dotted_from(node)
        if parts and parts[0] in _SDK_MODULES:
            return node.module or parts[0]
        for pkg, sub in _SDK_DOTTED:
            if parts[:2] == (pkg, sub) or (parts[:1] == (pkg,)
                    and any(a.name == sub for a in node.names)):
                return f"{pkg}.{sub}"
    return None


def _dynamic_import_target(node: ast.Call) -> str | None:
    """The SDK name if this call dynamically imports one via a string
    literal — ``__import__("anthropic")`` or
    ``importlib.import_module("openai")``."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "__import__":
        pass
    elif (isinstance(func, ast.Attribute) and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"):
        pass
    else:
        return None
    if not node.args:
        return None
    arg = node.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    parts = tuple(arg.value.split("."))
    if parts[:1] and parts[0] in _SDK_MODULES:
        return arg.value
    for pkg, sub in _SDK_DOTTED:
        if parts[:2] == (pkg, sub):
            return arg.value
    return None


def _instantiation_target(node: ast.Call) -> str | None:
    """The SDK module name if this call instantiates a client off an SDK
    module attribute, e.g. ``anthropic.Anthropic(...)`` or
    ``google.genai.Client(...)``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    # Unwind the attribute chain to its root Name.
    root = func
    while isinstance(root, ast.Attribute):
        root = root.value
    if not isinstance(root, ast.Name):
        return None
    if root.id in _SDK_MODULES:
        return root.id
    # google.generativeai.* / google.genai.* — root is "google", need the sub.
    if root.id == "google" and isinstance(func.value, ast.Attribute):
        sub = func.value
        base = sub.value
        if isinstance(base, ast.Name) and base.id == "google":
            if sub.attr in ("generativeai", "genai"):
                return f"google.{sub.attr}"
    return None


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Violations in one module as (line, message)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [(getattr(exc, "lineno", 1) or 1, f"could not parse: {exc}")]
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            sdk = _is_sdk_import(node)
            if sdk:
                out.append((node.lineno, f"imports cloud-LLM SDK '{sdk}'"))
        elif isinstance(node, ast.Call):
            sdk = _instantiation_target(node)
            if sdk:
                out.append((node.lineno, f"instantiates cloud-LLM SDK '{sdk}'"))
            sdk = _dynamic_import_target(node)
            if sdk:
                out.append((node.lineno,
                            f"dynamically imports cloud-LLM SDK '{sdk}'"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for host in _PROVIDER_HOSTS:
                if host in node.value:
                    out.append((node.lineno,
                                f"hardcodes provider base URL '{host}'"))
                    break
    return sorted(out)


def scan_tree(src_root: Path, allowlist: frozenset[str],
              prefix: str) -> list[str]:
    """``prefix/rel:line message`` for every violation under src_root."""
    findings: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        if rel in allowlist:
            continue
        for line, msg in scan_file(path):
            findings.append(f"{prefix}{rel}:{line} {msg}")
    return findings


def main(argv: list[str]) -> int:
    # Default: scan server/src and report paths as server/src/… . A caller may
    # point the guard at another root (the tests do), in which case paths are
    # reported relative to that root.
    if len(argv) > 1:
        root, prefix = Path(argv[1]), ""
    else:
        root, prefix = _SRC_ROOT, "server/src/"
    if not root.is_dir():
        print(f"egress-import-guard: source root not found: {root}",
              file=sys.stderr)
        return 2
    findings = scan_tree(root, _ALLOWLIST, prefix)
    if findings:
        for line in findings:
            print(line)
        print(f"egress-import-guard: FAIL — {len(findings)} cloud-LLM bypass(es)"
              " outside the sanctioned egress proxy; route through"
              " workspaces/lock/egress_proxy.py or allowlist with a reason")
        return 1
    print("egress-import-guard: clean (no cloud-LLM bypass outside the egress"
          " proxy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The egress import guard passes on the real tree and fails closed on a
synthetic bypass — an SDK import and a hardcoded provider URL."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GUARD = _REPO / "scripts" / "egress_import_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("egress_import_guard", _GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def test_current_tree_passes():
    findings = guard.scan_tree(_REPO / "server" / "src",
                               guard._ALLOWLIST, "server/src/")
    assert findings == [], "unexpected cloud-LLM bypass in server/src:\n" + \
        "\n".join(findings)


def test_guard_catches_sdk_import(tmp_path: Path):
    offender = tmp_path / "sneaky_client.py"
    offender.write_text(
        "import anthropic\n"
        "client = anthropic.Anthropic()\n"
    )
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert findings, "guard failed to catch a cloud-SDK import"
    assert any("sneaky_client.py:1" in f and "anthropic" in f for f in findings)
    assert any("sneaky_client.py:2" in f for f in findings)


def test_guard_catches_hardcoded_base_url(tmp_path: Path):
    offender = tmp_path / "direct_call.py"
    offender.write_text(
        'import httpx\n'
        'BASE = "https://api.openai.com/v1"\n'
    )
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert any("direct_call.py:2" in f and "api.openai.com" in f
               for f in findings), findings


def test_guard_exits_nonzero_on_violation(tmp_path: Path):
    (tmp_path / "bypass.py").write_text("from openai import OpenAI\n")
    assert guard.main(["egress_import_guard.py", str(tmp_path)]) == 1


def test_allowlisted_module_is_exempt(tmp_path: Path):
    # Same offending content, but at the sanctioned path, is not a violation.
    proxy = tmp_path / "workspaces" / "lock" / "egress_proxy.py"
    proxy.parent.mkdir(parents=True)
    proxy.write_text('HOSTS = ["api.anthropic.com"]\nimport anthropic\n')
    assert guard.scan_tree(tmp_path, guard._ALLOWLIST, "") == []


def test_onnx_genai_is_not_flagged(tmp_path: Path):
    # The local ONNX runtime is not Google's SDK and must not trip the guard.
    (tmp_path / "backend.py").write_text("import onnxruntime_genai\n")
    assert guard.scan_tree(tmp_path, guard._ALLOWLIST, "") == []


def test_guard_catches_google_dotted_importfrom(tmp_path: Path):
    (tmp_path / "gm.py").write_text(
        "from google.generativeai import GenerativeModel\n")
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert any("gm.py:1" in f and "google.generativeai" in f
               for f in findings), findings


def test_guard_catches_from_google_import_genai(tmp_path: Path):
    (tmp_path / "gg.py").write_text("from google import genai\n")
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert any("gg.py:1" in f and "google.genai" in f
               for f in findings), findings


def test_guard_catches_dunder_import(tmp_path: Path):
    (tmp_path / "dyn.py").write_text('mod = __import__("anthropic")\n')
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert any("dyn.py:1" in f and "anthropic" in f for f in findings), findings


def test_guard_catches_importlib_import_module(tmp_path: Path):
    (tmp_path / "dyn2.py").write_text(
        "import importlib\n"
        'mod = importlib.import_module("openai")\n')
    findings = guard.scan_tree(tmp_path, guard._ALLOWLIST, "")
    assert any("dyn2.py:2" in f and "openai" in f for f in findings), findings

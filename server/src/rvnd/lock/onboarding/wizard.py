# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""First-run onboarding wizard.

Stages:
  1. Welcome + environment detection (PyInstaller bundle? pip install? sandbox?)
  2. Model location detection (is a GGUF bundled? is one already downloaded?)
  3. Model installation choice (bundled / download / pick existing / skip → use mock)
  4. Backend smoke test (classify a known string; check the result)
  5. Persist config

The wizard is fully testable: every prompt can be auto-answered via auto_answers,
and stdin/stdout streams are injected.
"""

from __future__ import annotations

import io
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable

from ..backends import make_local_llm, BackendError
from ..tier_c import reset_backend_cache
from .config import Config, save_config, apply_config_to_env


# ASCII helpers
def _header(out: IO[str], title: str) -> None:
    out.write(f"\n━━ {title} ━━\n")


def _ok(out: IO[str], label: str, detail: str = "") -> None:
    out.write(f"  ✓ {label}{(' ' + detail) if detail else ''}\n")


def _warn(out: IO[str], label: str, detail: str = "") -> None:
    out.write(f"  ⚠ {label}{(' ' + detail) if detail else ''}\n")


def _info(out: IO[str], label: str, detail: str = "") -> None:
    out.write(f"  + {label}{(' ' + detail) if detail else ''}\n")


def _fail(out: IO[str], label: str, detail: str = "") -> None:
    out.write(f"  ✗ {label}{(' ' + detail) if detail else ''}\n")


def _prompt(stdin: IO[str], stdout: IO[str], q: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    stdout.write(f"  > {q}{suffix}: ")
    stdout.flush()
    raw = stdin.readline().strip()
    return raw if raw else default


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _detect_environment() -> dict:
    """Stage 1 — detect runtime environment."""
    is_pyinstaller = hasattr(sys, "frozen") and getattr(sys, "frozen", False)
    if is_pyinstaller:
        # _MEIPASS is where PyInstaller unpacks the bundle
        meipass = getattr(sys, "_MEIPASS", None)
        runtime_dir = Path(meipass) if meipass else Path(sys.executable).parent
    else:
        runtime_dir = Path(__file__).parent.parent.parent.parent

    return {
        "is_pyinstaller": is_pyinstaller,
        "runtime_dir": str(runtime_dir),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
    }


def _find_bundled_models(runtime_dir: Path) -> list[Path]:
    """Stage 2 — find GGUF models in the bundle's models/ dir."""
    models_dir = runtime_dir / "models"
    if not models_dir.exists():
        return []
    return sorted(models_dir.glob("*.gguf"))


def _find_existing_user_models() -> list[Path]:
    """Stage 2 — check user's standard cache locations."""
    candidates = [
        Path.home() / ".cache" / "agent-tool-lock" / "models",
        Path.home() / ".local" / "share" / "agent-tool-lock" / "models",
    ]
    results = []
    for d in candidates:
        if d.exists():
            results.extend(sorted(d.glob("*.gguf")))
    return results


def _build_spec_from_path(path: Path) -> str:
    return f"llama_cpp:{path}"


def _smoke_test_backend(spec: str) -> dict:
    """Stage 4 — run a few classifications + confirm sane behaviour."""
    cases = [
        ("Maria Schmidt approved the request", True, "name"),
        ("aggregate metrics for the team", False, "none"),
        ("patient was prescribed chemotherapy", True, "health"),
    ]
    try:
        backend = make_local_llm(spec)
    except BackendError as e:
        return {"ok": False, "reason": f"backend construction failed: {e}",
                "results": []}

    if not backend.is_available():
        return {"ok": False, "reason": f"backend not available: {backend.describe()}",
                "results": []}

    results = []
    for text, expected_pii, _expected_type in cases:
        out = backend.classify(text)
        matched_pii = out.get("contains_pii") == expected_pii
        results.append({
            "text": text[:50],
            "expected_pii": expected_pii,
            "actual_pii": out.get("contains_pii"),
            "type": out.get("type"),
            "confidence": out.get("confidence"),
            "pii_match": matched_pii,
        })

    pii_matches = sum(1 for r in results if r["pii_match"])
    ok = pii_matches >= 2  # tolerate one miss for non-deterministic backends
    return {
        "ok": ok,
        "reason": "" if ok else f"only {pii_matches}/3 cases matched expectations",
        "results": results,
        "describe": backend.describe(),
    }


# ---------------------------------------------------------------------------
# Wizard result
# ---------------------------------------------------------------------------


@dataclass
class WizardResult:
    completed: bool
    config: Config
    config_path: Path
    smoke_test_passed: bool
    smoke_test_results: list = None
    notes: list = None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def run_wizard(
    *,
    stdin: IO[str] = sys.stdin,
    stdout: IO[str] = sys.stdout,
    config_path: Path | str | None = None,
    auto_answers: Iterable[str] | None = None,
    skip_smoke_test: bool = False,
) -> WizardResult:
    """Run the onboarding wizard. Returns a structured result.

    Args:
        stdin / stdout: I/O streams. Inject for testability.
        config_path: where to persist config. Default ~/.config/agent-tool-lock/config.json.
        auto_answers: If provided, an iterable of strings supplied in place of prompts —
            stdin is ignored when this is set. Useful for non-interactive setup.
        skip_smoke_test: If True, write config without testing backend (for fast-path).
    """
    if auto_answers is not None:
        stdin = io.StringIO("\n".join(auto_answers) + "\n")

    notes: list[str] = []

    # Stage 1 — environment detection
    _header(stdout, "agent-tool-lock setup wizard")
    env = _detect_environment()
    _info(stdout, "python", env["python_version"])
    _info(stdout, "platform", env["platform"])
    if env["is_pyinstaller"]:
        _info(stdout, "runtime", "PyInstaller binary (single-file)")
    else:
        _info(stdout, "runtime", "pip-install (dev mode)")
    _info(stdout, "runtime_dir", env["runtime_dir"])

    # Stage 2 — find models
    _header(stdout, "Stage 2 — model discovery")
    bundled = _find_bundled_models(Path(env["runtime_dir"]))
    user_existing = _find_existing_user_models()

    if bundled:
        for m in bundled:
            _ok(stdout, "bundled model found", str(m))
    else:
        _warn(stdout, "no bundled GGUF in runtime_dir/models/")

    if user_existing:
        for m in user_existing:
            _ok(stdout, "user model found", str(m))
    else:
        _warn(stdout, "no GGUF in ~/.cache/agent-tool-lock/models/")

    # Stage 3 — backend choice
    _header(stdout, "Stage 3 — choose backend")
    if bundled:
        _info(stdout, "recommended", f"llama_cpp:{bundled[0]}")
        recommended_spec = _build_spec_from_path(bundled[0])
    elif user_existing:
        _info(stdout, "recommended", f"llama_cpp:{user_existing[0]}")
        recommended_spec = _build_spec_from_path(user_existing[0])
    else:
        _info(stdout, "recommended", "mock (no GGUF available; fall back to deterministic mock)")
        _info(stdout, "to install model",
              "point --local-url at your own OpenAI-compatible endpoint, or pass a "
              "llama_cpp:<path> GGUF spec")
        recommended_spec = "mock"

    spec = _prompt(
        stdin, stdout,
        "backend spec (Enter to accept recommended)",
        default=recommended_spec,
    )

    # Stage 4 — smoke test
    if skip_smoke_test:
        _header(stdout, "Stage 4 — smoke test")
        _warn(stdout, "skipped (skip_smoke_test=True)")
        smoke = {"ok": True, "results": [], "reason": "skipped"}
    else:
        _header(stdout, "Stage 4 — smoke test")
        smoke = _smoke_test_backend(spec)
        if smoke["ok"]:
            _ok(stdout, "backend describe", smoke.get("describe", ""))
            for r in smoke["results"]:
                marker = "✓" if r["pii_match"] else "✗"
                _info(stdout, f"  {marker}", f"{r['text'][:40]} → contains_pii={r['actual_pii']} type={r['type']}")
        else:
            _fail(stdout, "smoke test failed", smoke.get("reason", ""))
            notes.append(f"smoke test failed: {smoke.get('reason', 'unknown')}")
            spec = "mock"
            _info(stdout, "fallback", "using mock backend so the runtime stays usable")

    # Stage 5 — persist config
    _header(stdout, "Stage 5 — persist config")
    config = Config(
        backend_spec=spec,
        default_mode="standard",
        default_oversight="approve",
        model_dir=env["runtime_dir"] + "/models",
        setup_completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    audit_log = _prompt(
        stdin, stdout,
        "audit log path (Enter to skip)",
        default="",
    )
    if audit_log:
        config.audit_log_path = audit_log

    path = save_config(config, path=config_path)
    _ok(stdout, "config written", str(path))

    apply_config_to_env(config)
    reset_backend_cache()  # so the new backend_spec takes effect immediately

    _header(stdout, "setup complete")
    _ok(stdout, "backend", config.backend_spec)
    _ok(stdout, "default mode", config.default_mode)
    _ok(stdout, "default oversight", config.default_oversight)
    if config.audit_log_path:
        _ok(stdout, "audit log", config.audit_log_path)
    else:
        _info(stdout, "audit log", "(not configured — set AGENT_TOOL_LOCK_AUDIT_LOG env var to enable)")

    return WizardResult(
        completed=True,
        config=config,
        config_path=path,
        smoke_test_passed=smoke["ok"],
        smoke_test_results=smoke["results"],
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main():
    """Entry point for `agent-tool-lock setup` command."""
    result = run_wizard()
    return 0 if result.completed and result.smoke_test_passed else 1


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""`workspaces init` — the first-run setup wizard.

Runs against an isolated temp home (LOG_ROOT_DEFAULT monkeypatched) and a stubbed
registry, so the tests never touch the real ~/.workspace or ~/Documents/Workspaces.
"""
from __future__ import annotations

import argparse
import io

import rvnd.cli.impl as impl
import rvnd.workspace_registry as registry


def _run(monkeypatch, tmp_path, *, stdin: str = "", yes=False, dry=False):
    home_log = tmp_path / ".workspace" / "log"
    monkeypatch.setattr(impl, "LOG_ROOT_DEFAULT", home_log)
    calls: dict = {}
    monkeypatch.setattr(registry, "bootstrap_default_workspace",
                        lambda **k: calls.update(k) or {"ok": True})
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = impl.cmd_init(argparse.Namespace(yes=yes, dry_run=dry))
    return rc, out.getvalue(), calls, home_log.parent


def test_init_yes_writes_marker_and_sets_default(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert (home / "init.json").is_file()
    assert (home / "keys").is_dir() and (home / "log").is_dir()
    assert calls.get("target")           # the default workspace was set via the registry
    assert "Setup complete" in out
    assert "§5  Local model" in out              # the local-model step is present
    assert "connect-agent-hub.sh" in out         # the agent-hub step is present (now §7)


def test_init_dry_run_writes_nothing(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, yes=True, dry=True)
    assert rc == 0
    assert not (home / "init.json").exists()
    assert calls == {}                   # dry-run never calls the registry
    assert "dry-run" in out


def test_init_model_step_shows_paths_when_none_registered(monkeypatch, tmp_path):
    import rvnd.models_registry as mr
    monkeypatch.setattr(mr, "models_for_role", lambda role: [])
    rc, out, _c, _h = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert "§5  Local model" in out
    assert "workspaces models pull" in out       # the download path is offered
    assert "models register" in out              # register-your-own path
    assert "models config --local-url" in out    # BYOK endpoint path
    assert "local-models.md" in out


def test_init_model_step_confirms_when_registered(monkeypatch, tmp_path):
    import rvnd.models_registry as mr
    monkeypatch.setattr(mr, "models_for_role", lambda role: ["my-local-gguf"])
    rc, out, _c, _h = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert "a local model is registered: my-local-gguf" in out


def test_init_promise_decline_aborts(monkeypatch, tmp_path):
    rc, out, calls, home = _run(monkeypatch, tmp_path, stdin="n\n", yes=False)
    assert rc == 1
    assert not (home / "init.json").exists()   # declined before anything was written
    assert calls == {}
    assert "Not accepted" in out


def test_init_has_skills_section_and_pin_hint_under_yes(monkeypatch, tmp_path):
    rc, out, _c, _h = _run(monkeypatch, tmp_path, yes=True)
    assert rc == 0
    assert "§6  Skills" in out                       # the skills step exists
    assert "workspaces pin --interactive" in out     # non-interactive fallback offered


def test_init_interactive_offers_model_wizard_and_skills(monkeypatch, tmp_path):
    import rvnd.models_registry as mr
    monkeypatch.setattr(mr, "models_for_role", lambda role: [])
    # promise=y, ws folder=default, model wizard offer=n, skills offer=n
    rc, out, _c, _h = _run(monkeypatch, tmp_path, stdin="y\n\nn\nn\n", yes=False)
    assert rc == 0
    assert "guided model wizard" in out              # §5 offers the real wizard
    assert "§6  Skills" in out
    assert "Pick starter skills" in out              # §6 offers the real picker


def test_init_launches_real_model_wizard_when_accepted(monkeypatch, tmp_path):
    import rvnd.models_registry as mr
    import rvnd.lock as lock
    monkeypatch.setattr(mr, "models_for_role", lambda role: [])
    called: dict = {}

    class _Res:
        completed = True

    def _fake_wizard(**kw):
        called["ran"] = True
        return _Res()

    monkeypatch.setattr(lock, "run_wizard", _fake_wizard)
    # promise=y, ws=default, model wizard offer=y, skills offer=n
    rc, out, _c, _h = _run(monkeypatch, tmp_path, stdin="y\n\ny\nn\n", yes=False)
    assert rc == 0
    assert called.get("ran") is True                 # the BUILT wizard was invoked
    assert "model wizard finished" in out


def test_init_pins_skills_via_real_picker_when_accepted(monkeypatch, tmp_path):
    import rvnd.models_registry as mr
    import rvnd.pinned_skills as ps
    monkeypatch.setattr(mr, "models_for_role", lambda role: [])
    monkeypatch.setattr(ps, "load_companion_catalogue", lambda: {
        "families": {"ai-gov": {"label": "AI Gov", "skills": ["watch"]}}})
    # keep the picker deterministic: no host-installed plugins in scope
    monkeypatch.setenv("WORKSPACE_HOST_PLUGIN_DIRS", str(tmp_path / "no-host"))
    monkeypatch.setattr(ps, "list_pinned", lambda *a, **k: [])
    pinned: list = []
    monkeypatch.setattr(ps, "pin_skill",
                        lambda folder, sid, **k: pinned.append(sid))
    # promise=y, ws=default, model offer=n, skills offer=y, pick "1"
    rc, out, _c, _h = _run(monkeypatch, tmp_path, stdin="y\n\nn\ny\n1\n", yes=False)
    assert rc == 0
    assert pinned == ["watch"]                        # the real picker actually pinned

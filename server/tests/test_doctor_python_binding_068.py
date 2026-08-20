# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Python-binding doctor check + the standalone bootstrap.

These cover the install-class bug where a ``workspaces`` console
script lives on PATH but its shebang points at an interpreter that does not
have the ``workspaces`` package installed.  The user cannot even run
``workspaces doctor`` because the import fails at script entry — so we ship a
zero-dep standalone ``workspaces-doctor`` script alongside the in-package
``_doctor_check_python_binding`` so that, whichever surface the user reaches
first, they get an actionable answer.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path


from workspaces import cli as _cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspaces_script(directory: Path, shebang_python: str) -> Path:
    """Write a fake ``workspaces`` console-script with the given shebang."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "workspaces"
    script.write_text(f"#!{shebang_python}\n# stub workspaces entry-point\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _set_path(monkeypatch, *dirs: Path) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


# ---------------------------------------------------------------------------
# 1. GREEN: every `workspaces` on PATH points at a Python that imports workspaces
#    at the matching version.
# ---------------------------------------------------------------------------


def test_doctor_detects_python_binding_match(tmp_path, monkeypatch):
    """One script, shebang -> current Python -> workspaces importable, same version."""
    bindir = tmp_path / "bin"
    _make_workspaces_script(bindir, sys.executable)
    _set_path(monkeypatch, bindir)

    result = _cli._doctor_check_python_binding()

    assert result["name"] == "python_binding"
    assert result["level"] == _cli.DOCTOR_LEVEL_OK, result
    assert sys.executable in result["detail"]
    assert "ok" in result["detail"]


# ---------------------------------------------------------------------------
# 2. RED: shebang points at a Python that CANNOT import workspaces.
# ---------------------------------------------------------------------------


def test_doctor_detects_python_binding_mismatch_module_missing(
    tmp_path, monkeypatch
):
    """Build a fake Python wrapper whose sys.path excludes the workspaces install."""
    # Make a shim Python that always fails ``import workspaces``.
    fake_py = tmp_path / "fake-python"
    fake_py.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            # Minimal Python wrapper that scrubs site-packages so import fails.
            import os, sys
            os.execvpe(
                {sys.executable!r},
                [{sys.executable!r}, "-S",
                 "-c", "import sys; sys.path = [p for p in sys.path if 'workspaces' not in p and 'site-packages' not in p]; exec(sys.argv[1])"]
                 + sys.argv[1:],
                os.environ,
            )
            """
        )
    )
    fake_py.chmod(fake_py.stat().st_mode | stat.S_IEXEC)

    bindir = tmp_path / "bin"
    _make_workspaces_script(bindir, str(fake_py))
    _set_path(monkeypatch, bindir)

    result = _cli._doctor_check_python_binding()

    assert result["name"] == "python_binding"
    assert result["level"] == _cli.DOCTOR_LEVEL_ERROR, result
    assert "cannot import workspaces" in result["detail"]
    # Remediation must mention pip install.
    assert "pip install" in result["detail"]
    # And the offending script + python should both be named.
    assert str(bindir / "workspaces") in result["detail"]
    assert str(fake_py) in result["detail"]


# ---------------------------------------------------------------------------
# 3. YELLOW: shebang -> Python imports workspaces, but at a different version.
# ---------------------------------------------------------------------------


def test_doctor_detects_version_drift(tmp_path, monkeypatch):
    """Wrap the real Python so that ``import workspaces`` reports a different
    ``__version__``, simulating two parallel installs."""
    drift_py = tmp_path / "drift-python"
    # The wrapper imports the real workspaces, mutates __version__, then forwards
    # the user's -c command. Trick: we use a sitecustomize trick via PYTHONPATH.
    sitecustomize_dir = tmp_path / "drift-site"
    sitecustomize_dir.mkdir()
    (sitecustomize_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            """\
            try:
                import workspaces
                workspaces.__version__ = "9.9.9-DRIFT"
            except Exception:
                pass
            """
        )
    )
    drift_py.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import os, sys
            env = dict(os.environ)
            env["PYTHONPATH"] = {str(sitecustomize_dir)!r} + os.pathsep + env.get("PYTHONPATH", "")
            os.execvpe({sys.executable!r}, [{sys.executable!r}] + sys.argv[1:], env)
            """
        )
    )
    drift_py.chmod(drift_py.stat().st_mode | stat.S_IEXEC)

    bindir = tmp_path / "bin"
    _make_workspaces_script(bindir, str(drift_py))
    _set_path(monkeypatch, bindir)

    result = _cli._doctor_check_python_binding()

    assert result["name"] == "python_binding"
    assert result["level"] == _cli.DOCTOR_LEVEL_WARN, result
    assert "drift" in result["detail"].lower()
    assert "9.9.9-DRIFT" in result["detail"]


# ---------------------------------------------------------------------------
# 4. INFO: no `workspaces` script on PATH anywhere.
# ---------------------------------------------------------------------------


def test_doctor_handles_no_workspaces_on_path(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    _set_path(monkeypatch, empty)

    result = _cli._doctor_check_python_binding()

    assert result["name"] == "python_binding"
    assert result["level"] == _cli.DOCTOR_LEVEL_INFO
    assert "no 'workspaces' script" in result["detail"]


# ---------------------------------------------------------------------------
# 5. The standalone bootstrap runs WITHOUT the workspaces package importable.
#    We exercise this by invoking it via a fresh Python with PYTHONPATH set
#    to only the bootstrap's directory — workspaces itself stays unreachable.
# ---------------------------------------------------------------------------


def test_standalone_doctor_works_without_workspaces_package_importable(
    tmp_path, monkeypatch
):
    # Locate the bootstrap script that ships at src/workspaces_doctor_bootstrap.py.
    # `_cli.__file__` is src/workspaces/cli.py, so parents[1] is src/.
    bootstrap = (
        Path(_cli.__file__).resolve().parents[2] / "workspaces_doctor_bootstrap.py"
    )
    assert bootstrap.exists(), f"bootstrap missing at {bootstrap}"

    # Build a PATH that has one 'workspaces' script whose shebang points at a
    # Python that DEFINITELY can't import workspaces (a 'python -S' invocation
    # with PYTHONPATH=/dev/null).
    fake_py = tmp_path / "broken-python"
    fake_py.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import os, sys
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env["PYTHONPATH"] = "/nonexistent-on-purpose"
            os.execvpe({sys.executable!r}, [{sys.executable!r}, "-S"] + sys.argv[1:], env)
            """
        )
    )
    fake_py.chmod(fake_py.stat().st_mode | stat.S_IEXEC)

    bindir = tmp_path / "bin"
    _make_workspaces_script(bindir, str(fake_py))

    # Invoke the bootstrap as if it were `workspaces-doctor`. Crucially, we run
    # it via a Python that has NO PYTHONPATH pointing at the workspaces install
    # for the import inside the bootstrap itself — and it must still produce
    # a useful answer because it doesn't import workspaces at all.
    env = dict(os.environ)
    env["PATH"] = str(bindir)
    proc = subprocess.run(
        [sys.executable, str(bootstrap)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    out = proc.stdout
    # Exit code 20 = ERROR (the fake script can't import workspaces).
    assert proc.returncode == 20, (
        f"unexpected exit {proc.returncode}; out={out!r} err={proc.stderr!r}"
    )
    assert "workspaces-doctor" in out
    assert "cannot import workspaces" in out or "probe failed" in out
    assert str(bindir / "workspaces") in out
    # Remediation must surface.
    assert "pip install" in out or "venv" in out

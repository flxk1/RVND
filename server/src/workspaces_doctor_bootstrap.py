# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Standalone Python-binding doctor for the ``workspaces`` package.

This module is **deliberately self-contained**: it does NOT import ``workspaces``
itself, only the standard library. That is the entire point — when a user hits
``ModuleNotFoundError: No module named 'workspaces'`` because pip installed the
package for a different Python than the one their ``workspaces`` console-script's
shebang points at, the ordinary ``workspaces doctor`` cannot run.  This script
can. Run it as ``workspaces-doctor`` (a separate console-script entry in
``pyproject.toml``).

Output is intentionally plain-text and emoji-free so it works in every shell.

Exit codes mirror the main doctor where possible:

* 0  — all green (every ``workspaces`` script on PATH binds to a Python that can
       import ``workspaces``)
* 10 — warnings (version drift, unreadable shebang, missing interpreter)
* 20 — errors (at least one script's Python cannot import ``workspaces`` — the
       console-script binding failure)
* 30 — no ``workspaces`` script found on PATH or beside this interpreter
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Iterable


EXIT_OK = 0
EXIT_WARN = 10
EXIT_ERROR = 20
EXIT_NO_SCRIPT = 30


def _find_workspaces_scripts() -> list[str]:
    """Return every distinct ``workspaces`` file on the PATH, plus any beside
    this interpreter.

    The quick start invokes the virtual environment directly
    (``.venv/bin/python``) instead of activating it, so on a healthy install the
    console scripts sit next to ``sys.executable`` while PATH still resolves to
    the system Python. Searching PATH alone reports that install as missing.
    """
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    path_dirs.append(os.path.dirname(sys.executable))
    seen: set[str] = set()
    scripts: list[str] = []
    for d in path_dirs:
        if not d:
            continue
        candidate = os.path.join(d, "workspaces")
        if os.path.isfile(candidate) and candidate not in seen:
            seen.add(candidate)
            scripts.append(candidate)
    return scripts


def _read_shebang_interpreter(script: str) -> tuple[str | None, str | None]:
    """Return (interpreter_path, error_message). Both None if no shebang."""
    try:
        with open(script, "rb") as fh:
            first = fh.readline()
    except OSError as exc:
        return None, f"cannot read script ({exc})"
    if not first.startswith(b"#!"):
        return None, "no shebang line"
    shebang = first[2:].decode("utf-8", errors="replace").strip()
    parts = shebang.split()
    if not parts:
        return None, "empty shebang"
    # `#!/usr/bin/env python3` style: env + interpreter; last token is good
    # enough for the common cases.
    return parts[-1], None


def _probe_python_for_workspaces(python_path: str) -> tuple[int, str, str]:
    """Run ``python -c 'import rvnd; print(rvnd.__version__)'``."""
    try:
        proc = subprocess.run(
            [python_path, "-c", "import rvnd; print(rvnd.__version__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return 127, "", "interpreter not found"
    except Exception as exc:
        return 1, "", f"probe failed: {exc}"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _explain_remediation(python_path: str) -> str:
    return (
        f"  Fix:  {python_path} -m pip install -e /path/to/workspaces/workspace/runtime\n"
        f"  Or:   create a venv with that Python and install into it (recommended):\n"
        f"          {python_path} -m venv ~/.venvs/workspaces\n"
        f"          source ~/.venvs/workspaces/bin/activate\n"
        f"          pip install -e /path/to/workspaces/workspace/runtime"
    )


def diagnose(scripts: Iterable[str]) -> int:
    """Print diagnosis and return the appropriate exit code."""
    scripts = list(scripts)
    print("workspaces-doctor — Python-binding diagnostic (standalone)")
    print(f"  this interpreter: {sys.executable}")
    print(f"  this Python:      {sys.version.split()[0]}")
    print()

    if not scripts:
        print("  (info) no 'workspaces' script found on PATH or beside this")
        print("         interpreter. If you installed with pip, the install may")
        print("         have landed in a directory that's not on PATH.")
        print("         Check:  python -m pip show rvnd")
        return EXIT_NO_SCRIPT

    worst = EXIT_OK
    print(f"  found {len(scripts)} 'workspaces' script(s):")
    print()
    for script in scripts:
        py_path, shebang_err = _read_shebang_interpreter(script)
        if shebang_err and py_path is None:
            print(f"  [!] {script}")
            print(f"      shebang: {shebang_err}")
            if worst < EXIT_WARN:
                worst = EXIT_WARN
            continue

        if not py_path or not os.path.isfile(py_path):
            print(f"  [!] {script}")
            print(f"      shebang interpreter: {py_path!r}  (file not found)")
            if worst < EXIT_WARN:
                worst = EXIT_WARN
            continue

        rc, stdout, stderr = _probe_python_for_workspaces(py_path)
        if rc != 0:
            # Console-script binding failure: script's Python lacks the package.
            tail = (stderr or stdout or "no output").splitlines()[-1]
            print(f"  [x] {script}")
            print(f"      shebang Python: {py_path}")
            print(f"      probe failed:   {tail}")
            print(_explain_remediation(py_path))
            worst = EXIT_ERROR
        else:
            print(f"  [ok] {script}")
            print(f"       shebang Python: {py_path}")
            print(f"       workspaces version: {stdout}")
        print()

    print(f"  verdict: exit {worst}")
    if worst == EXIT_OK:
        print("    all green — every 'workspaces' on PATH can import the package.")
    elif worst == EXIT_WARN:
        print("    warnings — investigate the [!] entries above.")
    else:
        print(
            "    error — at least one 'workspaces' on PATH points at a Python\n"
            "    that does not have the 'workspaces' package installed.  Pick\n"
            "    one of the Fix lines above, or switch to a venv."
        )
    return worst


def main(argv: list[str] | None = None) -> int:
    # Trivial CLI: no flags today; the entire job is "diagnose and report".
    # `--help` is handled here without argparse to stay zero-dep.
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return EXIT_OK
    return diagnose(_find_workspaces_scripts())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

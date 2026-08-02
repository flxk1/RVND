# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""CLI smoke-tests for pinned-skills commands (#145, piece-CLI).

Exercises the four new subcommands by driving ``main(argv)`` directly and
capturing stdout. Uses tmp_path as both the workspace folder and the
log root, so tests are hermetic.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from workspaces.cli import main


def _run(argv, capture=True):
    if not capture:
        return main(argv), ""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_pin_and_list(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log = tmp_path / "log"

    rc, out = _run([
        "--log-root", str(log),
        "pin",
        "--folder", str(fc),
        "ai-governance-watch:newsletter-research",
        "--by", "alex", "--note", "AI gov work",
    ])
    assert rc == 0, out
    assert "pinned ai-governance-watch:newsletter-research" in out

    rc, out = _run([
        "--log-root", str(log),
        "list-pins", "--folder", str(fc),
    ])
    assert rc == 0, out
    assert "ai-governance-watch:newsletter-research" in out
    assert "by alex" in out
    assert "AI gov work" in out


def test_unpin_returns_message(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log = tmp_path / "log"
    _run(["--log-root", str(log), "pin", "--folder", str(fc), "p:s"])

    rc, out = _run([
        "--log-root", str(log),
        "unpin", "--folder", str(fc), "p:s",
    ])
    assert rc == 0, out
    assert "unpinned p:s" in out

    # Unpin again — surface that it wasn't there
    rc, out = _run([
        "--log-root", str(log),
        "unpin", "--folder", str(fc), "p:s",
    ])
    assert rc == 0, out
    assert "was not pinned" in out


def test_resolve_walks_ancestors(tmp_path):
    parent = tmp_path / "p"
    child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    _run(["--log-root", str(log), "pin", "--folder", str(parent), "from-parent"])
    _run(["--log-root", str(log), "pin", "--folder", str(child),  "from-child"])

    rc, out = _run([
        "--log-root", str(log),
        "resolve-skills", "--folder", str(child),
    ])
    assert rc == 0, out
    # Child sees both; parent contribution marked inherited
    assert "from-child" in out
    assert "[own]" in out
    assert "from-parent" in out
    assert "[inherited from" in out
    assert "chain walked" in out


def test_resolve_no_ancestors_flag(tmp_path):
    parent = tmp_path / "p"
    child = parent / "c"
    for d in (parent, child): d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "log"
    _run(["--log-root", str(log), "pin", "--folder", str(parent), "from-parent"])
    _run(["--log-root", str(log), "pin", "--folder", str(child),  "from-child"])

    rc, out = _run([
        "--log-root", str(log),
        "resolve-skills", "--folder", str(child), "--no-ancestors",
    ])
    assert rc == 0, out
    assert "from-child" in out
    # parent's pin must NOT leak when --no-ancestors is set
    assert "from-parent" not in out


def test_resolve_query_filter(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log = tmp_path / "log"
    _run(["--log-root", str(log), "pin", "--folder", str(fc),
          "ai-governance-watch:newsletter-research"])
    _run(["--log-root", str(log), "pin", "--folder", str(fc),
          "workspace:workspace-policy"])

    rc, out = _run([
        "--log-root", str(log),
        "resolve-skills", "--folder", str(fc), "--query", "policy",
    ])
    assert rc == 0, out
    assert "workspace:workspace-policy" in out
    assert "newsletter-research" not in out


def test_pin_empty_skill_id_returns_error(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log = tmp_path / "log"
    rc, out = _run([
        "--log-root", str(log),
        "pin", "--folder", str(fc), "   ",
    ])
    # ValueError → exit code 2 from cmd_pin's handler
    assert rc == 2


def test_list_pins_when_empty(tmp_path):
    fc = tmp_path / "wks"
    fc.mkdir()
    log = tmp_path / "log"
    rc, out = _run([
        "--log-root", str(log),
        "list-pins", "--folder", str(fc),
    ])
    assert rc == 0
    assert "no skills pinned" in out

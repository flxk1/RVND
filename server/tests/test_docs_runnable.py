# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Doc-verification test family for Workspace.

Every fenced ``bash`` / ``sh`` / ``python`` / ``py`` / ``yaml`` / ``yml`` block in
the documentation source tree is extracted and parameterised as one pytest case. Each case
runs the block in a sandboxed temp directory with ``WORKSPACE_FOLDER_CONTEXT``
pointed at that sandbox so the docs cannot mutate the user's real workspace.

Conventions for marking a block:

- ``# doctest: skip``           — never run (e.g. brew install, ollama pull)
- ``# doctest: xfail``          — known-broken; failure is the expected state
- ``# doctest: needs-network``  — only runs when ``WORKSPACES_DOCTEST_NETWORK=1``
- ``# doctest: needs-fixture <name>`` — only runs when fixture <name> is present
- ``# doctest: parse-only``     — YAML: validate as parseable YAML, don't schema-check
- ``# doctest: schema=<name>``  — YAML: validate against named schema (adapter, etc.)

The tag MUST appear on the line immediately preceding the opening ```` ``` ``` `` fence.

Running locally::

    pytest server/tests/test_docs_runnable.py -v

CI gates this via the full server suite on every PR.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - yaml is a soft dep of the harness
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# server/tests/test_docs_runnable.py → the repo root is two levels up
# (tests → server → repo). The verified trees are the PUBLISHED docs — the
# ones users copy-paste from: `docs/` (concepts, reference) and `deploy/`
# (quickstarts, firewall README). `_docs/` is gitignored session scratch and
# is deliberately NOT a target: a gate pointed at an untracked tree verifies
# nothing on a clean checkout.
_THIS = Path(__file__).resolve()
REPO_ROOT = _THIS.parents[2]
DOCS_ROOTS = [REPO_ROOT / "docs", REPO_ROOT / "deploy"]

# No named YAML schemas are registered in this layout; `schema=<name>` tags
# degrade to a skip with a precise reason (see _validate_yaml).
KNOWN_SCHEMAS: dict[str, Path] = {}


# ---------------------------------------------------------------------------
# Block extraction
# ---------------------------------------------------------------------------

RUNNABLE_LANGS = {"bash", "sh", "python", "py", "yaml", "yml"}

# Match a fence line: ```lang  (no extra info string). The tag line comes
# from the preceding non-blank line, evaluated by _parse_tags.
_FENCE_OPEN = re.compile(r"^```([a-zA-Z0-9_-]+)\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")
_TAG_LINE = re.compile(r"<!--\s*doctest:\s*(.+?)\s*-->|#\s*doctest:\s*(.+?)\s*$")


@dataclass
class CodeBlock:
    """One fenced runnable block."""

    doc_path: Path        # absolute path to the .md file
    rel_path: str         # path relative to DOCS_ROOT (for human ids)
    lang: str             # bash | sh | python | py | yaml | yml
    line_no: int          # 1-based line of the opening fence
    body: str             # block body (lines between the fences, joined)
    tags: set[str] = field(default_factory=set)
    needs_fixture: str | None = None
    schema_name: str | None = None

    @property
    def pid(self) -> str:
        """Pytest case id."""
        return f"{self.rel_path}:L{self.line_no}:{self.lang}"

    def preview(self, n: int = 5) -> str:
        head = self.body.splitlines()[:n]
        return "\n".join(head)


def _parse_tags(tag_line: str | None) -> tuple[set[str], str | None, str | None]:
    """Return (tags, fixture_name, schema_name) from a doctest tag line.

    Accepts both ``# doctest: ...`` (bare) and HTML comment forms.
    """
    if not tag_line:
        return set(), None, None
    m = _TAG_LINE.search(tag_line)
    if not m:
        return set(), None, None
    payload = (m.group(1) or m.group(2) or "").strip()
    # Allow multiple flags space- or comma-separated:
    # "skip", "needs-network", "needs-fixture foo", "schema=adapter"
    tokens = [t.strip() for t in re.split(r"[\s,]+", payload) if t.strip()]
    tags: set[str] = set()
    fixture: str | None = None
    schema: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "needs-fixture" and i + 1 < len(tokens):
            fixture = tokens[i + 1]
            tags.add("needs-fixture")
            i += 2
            continue
        if tok.startswith("schema="):
            schema = tok.split("=", 1)[1]
            tags.add("schema")
        else:
            tags.add(tok)
        i += 1
    return tags, fixture, schema


def _extract_blocks(doc_path: Path) -> list[CodeBlock]:
    """Walk one markdown file and return its runnable fenced blocks."""
    blocks: list[CodeBlock] = []
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    rel = str(doc_path.relative_to(REPO_ROOT))

    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1).lower()
        if lang not in RUNNABLE_LANGS:
            i += 1
            continue
        open_line = i + 1  # 1-based
        # Find the closing fence
        body_lines: list[str] = []
        j = i + 1
        while j < len(lines) and not _FENCE_CLOSE.match(lines[j]):
            body_lines.append(lines[j])
            j += 1
        # Look back to find the nearest non-blank line above the fence —
        # that's the tag carrier.
        k = i - 1
        while k >= 0 and lines[k].strip() == "":
            k -= 1
        tag_src = lines[k] if k >= 0 else None
        tags, fixture, schema = _parse_tags(tag_src)

        blocks.append(
            CodeBlock(
                doc_path=doc_path,
                rel_path=rel,
                lang=lang,
                line_no=open_line,
                body="\n".join(body_lines) + "\n",
                tags=tags,
                needs_fixture=fixture,
                schema_name=schema,
            )
        )
        i = j + 1  # skip past closing fence
    return blocks


# Session-scaffolding docs that are NOT self-contained runnable examples:
# interactive operator runbooks (real venv paths, `source ...`, model
# downloads) and point-in-time session records. They are guides/records,
# not doc-verification targets, so the harness must not execute their
# fenced blocks.
_NON_RUNNABLE_DOCS = {
    "test-doc-verification.md",     # the meta-doc describing this harness
    "PHASE1_MAC_RUNBOOK.md",
    "PHASE1_GATE4_RUNBOOK.md",
    "DEFINITION_OF_DONE.md",
    "VERIFICATION_RESULTS.md",
}


def _collect_all_blocks() -> list[CodeBlock]:
    out: list[CodeBlock] = []
    for root in DOCS_ROOTS:
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            if md.name in _NON_RUNNABLE_DOCS:
                continue
            out.extend(_extract_blocks(md))
    return out


def _docs_ship_runnable_fences() -> bool:
    """Extractor-agnostic check: do the shipped docs contain ANY fenced block in
    a runnable language? Deliberately independent of ``_extract_blocks`` so a
    broken extractor regex cannot also silence this probe. Used by the harness
    self-check to tell two cases apart:

    - docs DO ship runnable fences but the walker found none → a real harness
      bug (regex broke / mis-tagging) → the self-check FAILS;
    - the repo legitimately ships no runnable doc examples (e.g. a vendored
      layout whose docs hold only audit/handoff records) → nothing to
      verify → the self-check SKIPS.
    """
    for root in DOCS_ROOTS:
        if not root.is_dir():
            continue
        for md in root.rglob("*.md"):
            if md.name in _NON_RUNNABLE_DOCS:
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                m = _FENCE_OPEN.match(line)
                if m and m.group(1).lower() in RUNNABLE_LANGS:
                    return True
    return False


_ALL_BLOCKS: list[CodeBlock] = _collect_all_blocks()


# ---------------------------------------------------------------------------
# Fixture registry — extend as docs grow
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Repo-bound CLI provisioning
#
# The harness shells out to the `workspaces` command, but its job is to verify
# the shipped docs against THIS checkout — not against whatever `workspaces`
# sits on the ambient PATH. A stale global install can report the same
# `--version` string as the source yet expose a different command surface (lack
# a subcommand the current docs use, or still carry one the source dropped), so
# trusting PATH silently verifies the wrong code — a false red, or worse a false
# green. Version parity cannot tell them apart; provenance can.
#
# So we do not trust PATH. When the `rvnd` importable in THIS interpreter is the
# one under REPO_ROOT (CI's editable install, or a PYTHONPATH source layout), we
# synthesise a throwaway `workspaces` shim that runs `<this interpreter> -m
# rvnd.cli` — the same entry point as the console script — and put its directory
# FIRST on every sandbox's PATH, so no ambient binary can shadow the source. If
# the importable `rvnd` is foreign or absent we cannot vouch for provenance, and
# CLI-backed blocks skip with a precise reason rather than run an unverified one.
# ---------------------------------------------------------------------------

def _repo_rvnd() -> Path | None:
    """The `rvnd` package dir if the one importable here lives under REPO_ROOT,
    else None (a foreign/stale install, or rvnd not importable in this env)."""
    try:
        import rvnd
    except Exception:
        return None
    location = getattr(rvnd, "__file__", None)
    if not location:
        return None
    pkg = Path(location).resolve().parent
    try:
        pkg.relative_to(REPO_ROOT)
    except ValueError:
        return None
    return pkg


def _provision_repo_cli() -> Path | None:
    """Create a temp bin/ holding a `workspaces` shim bound to the repo CLI in
    this interpreter. Returns the bin dir, or None when provenance can't be
    vouched for (foreign rvnd, no interpreter path, or a non-POSIX host that
    can't run the shell shim)."""
    if os.name != "posix" or not sys.executable:
        return None
    if _repo_rvnd() is None:
        return None
    bin_dir = Path(tempfile.mkdtemp(prefix="rvnd-doctest-cli-"))
    atexit.register(shutil.rmtree, bin_dir, ignore_errors=True)
    shim = bin_dir / "workspaces"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m rvnd.cli "$@"\n')
    shim.chmod(0o755)
    return bin_dir


_CLI_SHIM_DIR: Path | None = _provision_repo_cli()


_FIXTURES_AVAILABLE: set[str] = set()
# Detect environmental fixtures up-front so per-block skip decisions are cheap.
# `workspaces-cli` is available only when we could bind a shim to THIS checkout's
# CLI (see _provision_repo_cli) — never on the strength of an ambient
# `workspaces`, which may be a stale install masquerading as the source.
if _CLI_SHIM_DIR is not None:
    _FIXTURES_AVAILABLE.add("workspaces-cli")
if shutil.which("ollama"):
    _FIXTURES_AVAILABLE.add("ollama")
if os.environ.get("WORKSPACES_DOCTEST_NETWORK") == "1":
    _FIXTURES_AVAILABLE.add("network")


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------

def _make_sandbox(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Return (workdir, env) for a per-block sandbox.

    workdir is a fresh tmpdir; the env masks the user's real Workspace context
    by pointing WORKSPACE_FOLDER_CONTEXT into the sandbox and dropping
    network-implying vars.
    """
    workdir = tmp_path / "sandbox"
    folder = workdir / "test"
    (folder / "Inbox").mkdir(parents=True, exist_ok=True)
    # Keep the ambient PATH so subprocesses can find sh/bash/python, but put the
    # repo-bound `workspaces` shim FIRST (when provisioned) so a stale ambient
    # `workspaces` can never shadow the source under test.
    ambient_path = os.environ.get("PATH", "")
    path = (
        f"{_CLI_SHIM_DIR}{os.pathsep}{ambient_path}"
        if _CLI_SHIM_DIR is not None
        else ambient_path
    )
    env = {
        "PATH": path,
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "WORKSPACE_FOLDER_CONTEXT": str(folder),
        # Make sure the doc can't accidentally write into the real log root
        "WORKSPACE_LOG_ROOT": str(workdir / "log"),
        # Defensive: blank out anything that would talk to a real network
        # service unless the block explicitly opted into needs-network.
        "OLLAMA_HOST": "http://127.0.0.1:0",
        "NO_PROXY": "*",
    }
    # Preserve PYTHONPATH so `import rvnd` works in python blocks.
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return workdir, env


# ---------------------------------------------------------------------------
# Per-language runners
# ---------------------------------------------------------------------------

def _run_shell(block: CodeBlock, workdir: Path, env: dict[str, str]) -> None:
    shell = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    # `set -e` so any failing line aborts; `-u` is too strict for example
    # scripts that reference $HOME etc.
    script = "set -e\n" + block.body
    proc = subprocess.run(
        [shell, "-c", script],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            _format_failure(block, proc.returncode, proc.stdout, proc.stderr)
        )


def _run_python(block: CodeBlock, workdir: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", block.body],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            _format_failure(block, proc.returncode, proc.stdout, proc.stderr)
        )


def _validate_yaml(block: CodeBlock) -> None:
    if yaml is None:
        pytest.skip("PyYAML not installed; yaml validation degraded to skip")
    try:
        doc = yaml.safe_load(block.body)
    except yaml.YAMLError as e:
        raise AssertionError(
            f"YAML parse failed in {block.pid}: {e}\n\n"
            f"First lines:\n{textwrap.indent(block.preview(), '    ')}"
        )
    if "parse-only" in block.tags or not block.schema_name:
        return  # parse OK and no schema requested
    schema_path = KNOWN_SCHEMAS.get(block.schema_name)
    if not schema_path or not schema_path.is_file():
        pytest.skip(
            f"schema={block.schema_name!r} requested but {schema_path} not found"
        )
    # Lightweight schema check: load the schema YAML and ensure every
    # required top-level key in the schema (commented `# required` lines
    # name the keys) is present in the document. This is intentionally a
    # weak check until a proper JSON-Schema sidecar exists — escalate to
    # `jsonschema` if/when the schema check becomes load-bearing.
    schema_text = schema_path.read_text(encoding="utf-8")
    required_keys = re.findall(
        r"^#\s*([a-zA-Z_]+):\s*[^\n]*required",
        schema_text,
        flags=re.MULTILINE,
    )
    if isinstance(doc, dict):
        missing = [k for k in required_keys if k not in doc]
        if missing:
            raise AssertionError(
                f"YAML in {block.pid} missing required keys for schema "
                f"{block.schema_name!r}: {missing}"
            )


def _format_failure(
    block: CodeBlock, rc: int, stdout: str, stderr: str
) -> str:
    return (
        f"\nDoc block failed:\n"
        f"  file:     {block.doc_path}\n"
        f"  line:     {block.line_no}  (```{block.lang})\n"
        f"  tags:     {sorted(block.tags) or '-'}\n"
        f"  exit:     {rc}\n"
        f"  preview:\n{textwrap.indent(block.preview(), '    ')}\n"
        f"  stdout:\n{textwrap.indent(stdout[:1200], '    ')}\n"
        f"  stderr:\n{textwrap.indent(stderr[:1200], '    ')}\n"
    )


# ---------------------------------------------------------------------------
# Skip / xfail evaluation
# ---------------------------------------------------------------------------

def _eval_skip(block: CodeBlock) -> str | None:
    """Return a skip reason if the block should not run, else None."""
    if "skip" in block.tags:
        return "tagged # doctest: skip"
    if "needs-network" in block.tags and "network" not in _FIXTURES_AVAILABLE:
        return "needs-network (set WORKSPACES_DOCTEST_NETWORK=1 to opt in)"
    if block.needs_fixture and block.needs_fixture not in _FIXTURES_AVAILABLE:
        return f"needs-fixture {block.needs_fixture!r} (not available)"
    # Soft-skip every block that calls the `workspaces` CLI when it isn't
    # installed in the test env. The doc still gets the case in the report;
    # it just skips with a precise reason rather than hard-failing every PR
    # run on a dev box without workspaces on PATH.
    if block.lang in {"bash", "sh"}:
        # Illustrative command examples carry placeholders (e.g. ``<id>``,
        # ``<path>``, ``/path/to/folder``) that are NOT literally executable —
        # bash treats ``<id>`` as a redirection and errors. These blocks
        # document syntax, not a runnable recipe, so skip them regardless of
        # whether the CLI is installed. (Without this, a block like
        # ``workspaces purge --folder /path/to/folder --pair-id <id>`` fails on a
        # box that HAS workspaces installed, while soft-skipping on one that
        # doesn't — an environment-dependent false failure.)
        if re.search(r"<[A-Za-z][\w-]*>", block.body) or "/path/to/" in block.body:
            return "illustrative example with placeholders (not runnable)"
        # Blocks that start a long-running process (background worker, MCP
        # stdio servers, polling watch) never return, so the harness's timeout
        # would kill them and report a false failure. These document how to
        # run a daemon, not a runnable-to-completion recipe.
        _starts_daemon = (
            re.search(r"\b(run-worker|workspaces-mcp|workspace-lock-mcp|lock-beta-mcp)\b",
                      block.body)
            or "--interval" in block.body
            or (re.search(r"\bworkspaces\s+watch\b", block.body)
                and "--once" not in block.body)
        )
        if _starts_daemon:
            return "block starts a long-running process (auto-skip)"
        if re.search(r"\bworkspaces\b", block.body) and "workspaces-cli" not in _FIXTURES_AVAILABLE:
            return ("repo `workspaces` CLI not importable in this interpreter "
                    "(refusing to bind doc blocks to an ambient install)")
        if re.search(r"\b(ollama|brew|apt|yum|pip install|sudo)\b", block.body):
            return "block performs install-time side effects (auto-skip)"
        # Network-cloning blocks (git clone / huggingface-cli download / wget /
        # curl-to-file) reach external services and can leave large artefacts in
        # the sandbox even when they succeed. Treat as install-time side effects.
        if re.search(
            r"\b(git clone|huggingface-cli download|wget|curl -[A-Za-z]*[oO])\b",
            block.body,
        ):
            return "block clones/downloads external artefacts (auto-skip)"
        # Blocks that invoke `pytest` directly are documentation of how to run
        # the suite, not something this harness should execute (would recurse,
        # or fail because `pytest` isn't on PATH in a stripped env). Skip them.
        if re.search(r"(^|\s)(python\s+-m\s+pytest|pytest)\b", block.body):
            return "block invokes pytest (documentation demo, auto-skip)"
    return None


# ---------------------------------------------------------------------------
# The parameterised test
# ---------------------------------------------------------------------------

# Skip the whole module gracefully if no docs tree exists (e.g. someone runs
# the server tests from a stripped-down install). On a full checkout `docs/`
# and `deploy/` are tracked, so this never skips in CI.
pytestmark = pytest.mark.skipif(
    not any(r.is_dir() for r in DOCS_ROOTS),
    reason=f"no docs trees present: {[str(r) for r in DOCS_ROOTS]}",
)


@pytest.mark.parametrize(
    "block",
    _ALL_BLOCKS,
    ids=[b.pid for b in _ALL_BLOCKS] or ["__no_blocks__"],
)
def test_doc_block_runs(block: CodeBlock, tmp_path: Path) -> None:
    """Each fenced runnable documentation block must execute cleanly."""
    if not _ALL_BLOCKS:
        pytest.skip("no runnable documentation blocks found")

    skip_reason = _eval_skip(block)
    if skip_reason:
        pytest.skip(skip_reason)

    if "xfail" in block.tags:
        pytest.xfail("tagged # doctest: xfail")

    workdir, env = _make_sandbox(tmp_path)

    if block.lang in {"bash", "sh"}:
        _run_shell(block, workdir, env)
    elif block.lang in {"python", "py"}:
        _run_python(block, workdir, env)
    elif block.lang in {"yaml", "yml"}:
        _validate_yaml(block)
    else:  # pragma: no cover - filtered earlier
        pytest.fail(f"unhandled lang {block.lang!r}")


# ---------------------------------------------------------------------------
# Self-check: the harness itself must find at least one block, otherwise the
# CI gate is silently doing nothing.
# ---------------------------------------------------------------------------

def test_harness_discovered_blocks() -> None:
    """Sanity: when the shipped docs contain runnable fenced blocks, the walker
    must find at least one — otherwise the CI gate is silently doing nothing
    (the fence regex broke, or every doc got tagged skip).

    In a vendored layout whose docs ship only non-runnable guides and
    audit/handoff records, there is genuinely nothing for the doc harness to
    verify, so the self-check skips rather than failing (the module docstring's
    "skips gracefully" promise, extended to the no-runnable-docs case)."""
    if not any(r.is_dir() for r in DOCS_ROOTS):
        pytest.skip(f"no docs trees shipped at {[str(r) for r in DOCS_ROOTS]}")
    if not _docs_ship_runnable_fences():
        pytest.skip(
            "published docs trees ship no runnable fenced blocks "
            "(audit/handoff/guide docs only) — nothing for the doc harness to "
            "verify in this layout"
        )
    assert _ALL_BLOCKS, (
        "docs ship runnable fenced blocks but the walker found none — did the "
        "fence regex break, or did all docs get tagged skip?"
    )


# ---------------------------------------------------------------------------
# Provenance: doc blocks must bind to THIS checkout's CLI, never a stale
# `workspaces` that happens to be on the ambient PATH.
# ---------------------------------------------------------------------------

def test_cli_blocks_bind_to_repo_source(tmp_path: Path) -> None:
    """A stale `workspaces` on the ambient PATH must not shadow the source under
    test. When the repo CLI is provisioned, every sandbox resolves `workspaces`
    into the repo-bound shim dir (ahead of any ambient install), and that shim
    runs the repo CLI cleanly.

    Skips when the repo CLI is not importable here (e.g. tests run against an
    installed package without source) — there is then no source to bind to, and
    `_eval_skip` already refuses to run CLI blocks rather than trusting an
    ambient binary."""
    if _CLI_SHIM_DIR is None:
        pytest.skip("repo CLI not importable in this interpreter; nothing to bind")

    # The importable rvnd is the one under this checkout.
    assert _repo_rvnd() is not None

    # Plant a decoy `workspaces` LATER on the ambient PATH; the sandbox must
    # still resolve to the shim, proving the shim wins regardless of ambient.
    decoy_dir = tmp_path / "stale-bin"
    decoy_dir.mkdir()
    decoy = decoy_dir / "workspaces"
    decoy.write_text("#!/bin/sh\necho 'stale global' >&2\nexit 2\n")
    decoy.chmod(0o755)

    _workdir, env = _make_sandbox(tmp_path)
    env["PATH"] = f"{env['PATH']}{os.pathsep}{decoy_dir}"

    resolved = shutil.which("workspaces", path=env["PATH"])
    assert resolved is not None
    assert Path(resolved).resolve().parent == _CLI_SHIM_DIR.resolve(), (
        f"sandbox bound `workspaces` to {resolved!r}, not the repo shim"
    )

    # And the shim actually drives the repo CLI to a clean exit.
    proc = subprocess.run(
        [str(_CLI_SHIM_DIR / "workspaces"), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "workspaces" in proc.stdout.lower()

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Discipline gate — the third per-folder dial beside Lock and Oversight.

Lock governs what leaves a folder; Oversight governs what needs approval;
Discipline governs what *conforms* — code/text hygiene rules a workspaced folder is
held to. The engine lives here once, in the substrate; a folder never carries a
copy. Drawing the dial is a policy act (``policy.discipline_enabled`` +
``discipline_manifest``); running it is this module, called by the
``workspace-discipline`` skill.

Rules come from a manifest (the folder's ``discipline_manifest``, else the
built-in :data:`DEFAULT_MANIFEST`). Config is per-folder; the engine is shared —
the same DRY split Lock and Oversight already follow.

Modes:
  audit  — every file under the folder (existing code)
  diff   — only changed + new files vs HEAD (incoming work)
  check  — an explicit list of files

A run appends a summary to the folder's mutation log (the audit chain), so the
record of what passed, when, and against which rules is durable across sessions
— which is what makes the gate consistent rather than advisory.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


# Built-in default rules. A folder may override by naming its own manifest in
# policy.discipline_manifest. Mirrors the standalone discipline.json.
DEFAULT_MANIFEST: dict[str, Any] = {
    "exclude_dirs": [".git", "_archive", "memory", "node_modules", ".venv",
                     "dist", "build", ".workspace"],
    "exclude_files": ["discipline.json", "check.sh"],
    "stale_terms": ["Workspaceversum", "mini_workspace", "mini-workspace", "The Signal"],
    "scopes": {
        "skill": ["SKILL.md"],
        "code":  ["*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.sh"],
        "text":  ["*.md", "*.txt", "*.json", "*.yaml", "*.yml"],
        "py":    ["*.py"],
    },
    "rules": [
        {"id": "skill-description", "section": "G5/skill", "scope": "skill",
         "severity": "fail", "kind": "skill_desc",
         "description": "SKILL.md description <=1024 chars, no angle brackets"},
        {"id": "stale-terms", "section": "wiring", "scope": "text",
         "severity": "warn", "kind": "stale_terms",
         "description": "Retired names must not appear in live text"},
        {"id": "unresolved-marker", "section": "done", "scope": "code",
         "severity": "warn", "kind": "regex", "pattern": r"\b(TODO|FIXME|XXX|HACK)\b",
         "description": "No unresolved markers in code reported as done"},
        {"id": "swallowed-exception", "section": "craft", "scope": "py",
         "severity": "warn", "kind": "regex_multiline",
         "pattern": r"except[^\n]*:\s*\n\s*pass\b",
         "description": "Exceptions handled, not silently swallowed"},
        {"id": "float-for-money", "section": "craft", "scope": "code",
         "severity": "warn", "kind": "regex",
         "pattern": r"float\s*\([^)]*(price|amount|total|cost|money|royalt|fee)",
         "description": "No float for currency"},
    ],
}


# --- file selection ---------------------------------------------------------
def _walk(root: Path, exclude_dirs: list[str]) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for f in filenames:
            out.append(os.path.join(dirpath, f))
    return out


def _git_changed(root: Path) -> Optional[list[str]]:
    def run(args):
        try:
            r = subprocess.run(["git", "-C", str(root)] + args,
                               capture_output=True, text=True, check=True)
            return [l for l in r.stdout.splitlines() if l.strip()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    tracked = run(["diff", "--name-only", "HEAD"])
    untracked = run(["ls-files", "--others", "--exclude-standard"])
    if tracked is None or untracked is None:
        return None
    files = {str(root / p) for p in (tracked + untracked)}
    return [f for f in files if os.path.isfile(f)]


def _matches(path: str, patterns: list[str]) -> bool:
    base = os.path.basename(path)
    return any(fnmatch.fnmatch(base, pat) for pat in patterns)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (OSError, IsADirectoryError):
        return ""


# --- rule kinds -------------------------------------------------------------
def _check_skill_desc(text: str) -> list[str]:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return ["no YAML frontmatter"]
    desc, capturing = [], False
    for line in m.group(1).splitlines():
        if not capturing:
            km = re.match(r"\s*description\s*:\s*(.*)$", line)
            if km:
                desc.append(km.group(1)); capturing = True
        elif re.match(r"\s+\S", line) and not re.match(r"\s*[A-Za-z0-9_-]+\s*:", line):
            desc.append(line.strip())
        else:
            break
    d = " ".join(desc).strip().strip('"').strip("'")
    if not d:
        return ["description field empty or unparsed"]
    problems = []
    if len(d) > 1024:
        problems.append(f"description {len(d)} chars (>1024)")
    if "<" in d or ">" in d:
        problems.append("angle bracket(s) in description")
    return problems


def skill_description_problems(text: str) -> list[str]:
    """Public: problems with a SKILL.md description (>1024 chars / angle
    brackets / unparsed). Empty list = clean. Used by the Forge gate so the
    rule lives in one engine, not two."""
    return _check_skill_desc(text)


# Cues that mark a line as a *mention* (documentation naming a retired term as
# retired) rather than a live *usage*. Ported from the registry gate's
# classifier so the gate does not warn on docs whose purpose is to record what
# was removed — that was the dominant false positive on the first self-host run.
_STALE_MENTION_CUES = (
    "replace", "replaced", "no longer", "do not", "don't", "dont",
    "forbidden", "dead", "stale", "instead", "deprecat", "remove", "removed",
    "must not", "retired", "renamed", "formerly", "former", "legacy",
    "back-compat", "backcompat", "superseded", "->", "→", "not exist",
    "does not exist", "history", "historical",
)


def _stale_is_mention(line: str, terms_on_line: list[str]) -> bool:
    """A line is a mention (allowed) rather than a usage (warned) when it names
    two or more retired terms together, or carries a retired-context cue."""
    if len(terms_on_line) >= 2:
        return True
    low = line.lower()
    return any(cue in low for cue in _STALE_MENTION_CUES)


def _check_terms(text: str, terms: list[str]) -> list[str]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        present = [t for t in terms if t in line]
        if not present or _stale_is_mention(line, present):
            continue
        for t in present:
            hits.append(f"line {i}: '{t}'  {line.strip()[:80]}")
    return hits


def _check_regex(text: str, pattern: str, multiline: bool) -> list[str]:
    flags = re.MULTILINE | (re.DOTALL if multiline else 0)
    rx = re.compile(pattern, flags)
    if multiline:
        return [f"match: {m.group(0).splitlines()[0][:80]}" for m in rx.finditer(text)]
    return [f"line {i}: {line.strip()[:80]}"
            for i, line in enumerate(text.splitlines(), 1) if rx.search(line)]


def _run_rule(rule: dict, files: list[str], manifest: dict) -> list[tuple]:
    pats = manifest.get("scopes", {}).get(rule["scope"], [])
    findings = []
    for path in files:
        if not _matches(path, pats):
            continue
        text = _read(path)
        kind = rule["kind"]
        if kind == "skill_desc":
            probs = _check_skill_desc(text)
        elif kind == "stale_terms":
            probs = _check_terms(text, manifest.get("stale_terms", []))
        elif kind == "regex":
            probs = _check_regex(text, rule["pattern"], multiline=False)
        elif kind == "regex_multiline":
            probs = _check_regex(text, rule["pattern"], multiline=True)
        else:
            probs = [f"unknown rule kind '{kind}'"]
        findings.extend((path, p) for p in probs)
    return findings


# --- manifest resolution ----------------------------------------------------
def resolve_manifest(folder: str | Path, manifest: Optional[str] = None) -> dict:
    """Load the rule manifest: explicit path, else the folder's policy
    ``discipline_manifest``, else the built-in default."""
    folder = Path(folder).expanduser().resolve()
    path = manifest
    if path is None:
        try:
            from .policy import load_policy
            path = load_policy(folder).discipline_manifest or None
        except Exception:
            path = None
    if not path:
        return DEFAULT_MANIFEST
    p = Path(path)
    if not p.is_absolute():
        p = folder / p
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_MANIFEST


# --- main entry -------------------------------------------------------------
def run_discipline(folder: str | Path, *, mode: str = "audit",
                   files: Optional[list[str]] = None,
                   manifest: Optional[str] = None,
                   write_audit: bool = True,
                   log_root: str | Path | None = None,
                   strict: bool = False) -> dict[str, Any]:
    """Run the discipline gate over a folder.

    Returns ``{mode, scanned, failures, warnings, findings, clean}``. When
    ``write_audit`` is set, appends a summary to the folder's mutation log so
    the run is part of the audit chain (durable, cross-session).
    """
    folder = Path(folder).expanduser().resolve()
    man = resolve_manifest(folder, manifest)
    excl_dirs = man.get("exclude_dirs", [])
    excl_files = set(man.get("exclude_files", []))

    if mode == "audit":
        target = _walk(folder, excl_dirs)
    elif mode == "diff":
        target = _git_changed(folder)
        if target is None:
            return {"mode": mode, "error": "not a git repo (use audit/check)",
                    "scanned": 0, "failures": 0, "warnings": 0,
                    "findings": [], "clean": False}
    elif mode == "check":
        target = [f for f in (files or []) if os.path.isfile(f)]
    else:
        return {"mode": mode, "error": f"unknown mode {mode!r}",
                "scanned": 0, "failures": 0, "warnings": 0,
                "findings": [], "clean": False}

    target = [f for f in target if os.path.basename(f) not in excl_files]

    findings: list[dict[str, Any]] = []
    n_fail = n_warn = 0
    for rule in man.get("rules", []):
        for path, msg in _run_rule(rule, target, man):
            sev = rule.get("severity", "warn")
            findings.append({"severity": sev, "rule": rule["id"],
                             "section": rule.get("section", ""),
                             "file": os.path.relpath(path, folder), "detail": msg})
            if sev == "fail":
                n_fail += 1
            else:
                n_warn += 1

    clean = n_fail == 0 and not (strict and n_warn)
    result = {"mode": mode, "scanned": len(target), "failures": n_fail,
              "warnings": n_warn, "findings": findings, "clean": clean}

    if write_audit:
        result["audit"] = _append_audit(folder, result, log_root)
    return result


def _append_audit(folder: Path, result: dict, log_root) -> dict[str, Any]:
    """Append a discipline-run summary to the folder's mutation log. Never
    crashes the gate — a missing runtime/log is reported, not raised."""
    try:
        from .mutation_log import LogEvent, MutationLog
        log = MutationLog(folder, log_root=log_root)
        audit_id = log.append(LogEvent(
            event="system",
            folder_path=str(folder),
            pair_id="discipline-run",
            lifecycle_state="",
            channel="system",
            actor="agent:workspace-discipline",
            extra={"discipline_run": result["mode"],
                   "scanned": result["scanned"],
                   "failures": result["failures"],
                   "warnings": result["warnings"],
                   "clean": result["clean"]},
        ))
        return {"recorded": True, "audit_id": audit_id}
    except Exception as exc:  # report, never crash the gate
        return {"recorded": False, "reason": str(exc)}


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the discipline gate over a folder.")
    ap.add_argument("mode", choices=["audit", "diff", "check"])
    ap.add_argument("targets", nargs="*", help="folder (audit/diff) or files (check)")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--no-audit", action="store_true", help="do not write to the audit chain")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    if args.mode == "check":
        folder = "."
        files = args.targets
    else:
        folder = args.targets[0] if args.targets else "."
        files = None

    res = run_discipline(folder, mode=args.mode, files=files,
                         manifest=args.manifest, write_audit=not args.no_audit,
                         strict=args.strict)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("clean") else 1


if __name__ == "__main__":
    raise SystemExit(_main())

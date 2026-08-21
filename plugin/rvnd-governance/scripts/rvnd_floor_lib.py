#!/usr/bin/env python3
"""Shared helpers for the RVND offline floor tools (bin/rvnd-*).

The floor tools run with NOTHING installed — standard library only. Their whole
job is to be useful before the engine exists and to get out of the way honestly
once it does. Two rules govern every tool here and must never be broken:

  1. Never silently downgrade. If a check could not run, the tool says so; it
     does not pretend the check passed.
  2. Never silently upgrade. The floor is ADVISORY. It never emits an enforced
     verdict, never signs, never grants. When the real engine is present the
     tool routes to the governed surface rather than re-deciding.

`probe_engine()` is how a tool learns whether real RVND is installed and
compatible; the floor's behaviour adapts on that answer, and the answer is
always printed as a mode line so an advisory result is never mistaken for a
binding one.
"""
from __future__ import annotations

import json
import sys
from typing import Any

# The compatible engine range, kept in step with the plugin's declared
# runtime.requires ("rvnd>=0.6.8.4,<0.7" in package.json). If those diverge the
# op-drift test in tests/ fails, which is the intended tripwire.
ENGINE_MIN = (0, 6, 8, 4)
ENGINE_MAX_EXCL = (0, 7)


def _parse_version(text: str) -> tuple[int, ...]:
    """Best-effort dotted-numeric parse. Non-numeric tails (rc, dev) stop the
    parse rather than guessing an ordering we do not own."""
    parts: list[int] = []
    for chunk in text.strip().split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts)


def _ge(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return _pad(a, b) >= _pad(b, a)


def _lt(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return _pad(a, b) < _pad(b, a)


def _pad(a: tuple[int, ...], ref: tuple[int, ...]) -> tuple[int, ...]:
    return a + (0,) * (len(ref) - len(a)) if len(a) < len(ref) else a


def probe_engine() -> dict[str, Any]:
    """Report whether the real RVND engine is importable and in range.

    Returns a dict: present (bool), version (str|None), compatible (bool),
    reason (str). Import failure is the normal offline case, not an error.
    """
    try:
        import importlib.metadata as md
    except Exception:  # pragma: no cover - importlib.metadata is stdlib >=3.8
        md = None  # type: ignore[assignment]

    version: str | None = None
    if md is not None:
        try:
            version = md.version("rvnd")
        except Exception:
            version = None

    if version is None:
        # Fall back to an import probe: the package may be present without
        # dist metadata (e.g. a raw checkout on sys.path).
        try:
            import rvnd  # noqa: F401  (import for presence only)

            version = getattr(rvnd, "__version__", None)
        except Exception:
            return {
                "present": False,
                "version": None,
                "compatible": False,
                "reason": "rvnd not importable (offline / advisory mode)",
            }

    if version is None:
        return {
            "present": True,
            "version": None,
            "compatible": False,
            "reason": "rvnd importable but version unresolved; treating as unverified",
        }

    parsed = _parse_version(version)
    compatible = bool(parsed) and _ge(parsed, ENGINE_MIN) and _lt(parsed, ENGINE_MAX_EXCL)
    return {
        "present": True,
        "version": version,
        "compatible": compatible,
        "reason": (
            "engine present and in the plugin's compatible range"
            if compatible
            else f"engine {version} outside the compatible range "
            f">={_dot(ENGINE_MIN)},<{_dot(ENGINE_MAX_EXCL)}"
        ),
    }


def _dot(t: tuple[int, ...]) -> str:
    return ".".join(str(n) for n in t)


def mode_line(engine: dict[str, Any], *, verb: str) -> str:
    """The always-printed provenance line. It states, in words a reader cannot
    misread, whether this run is the advisory floor or a routed engine call."""
    if engine.get("present") and engine.get("compatible"):
        return (
            f"mode: advisory ({verb}) — real RVND {engine.get('version')} is installed; "
            f"the BINDING decision comes from the governed cycle, not this tool"
        )
    if engine.get("present"):
        return (
            f"mode: advisory ({verb}) — an RVND install was found but is not in the "
            f"compatible range ({engine.get('reason')}); floor only"
        )
    return f"mode: advisory ({verb}) — engine absent, offline floor only"


def read_json_arg(argv: list[str]) -> Any:
    """Read one JSON object from a file-path argument, or from stdin when the
    argument is '-' or absent. Malformed input is fail-closed by the caller."""
    if argv and argv[0] not in ("-",):
        with open(argv[0], "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def die(msg: str, code: int = 2) -> "int":
    """Fail closed: print to stderr and return a non-zero code for the caller
    to exit with. Used for malformed input and broken invariants."""
    print(f"error: {msg}", file=sys.stderr)
    return code


def emit(payload: dict[str, Any], mode: str) -> None:
    """Print the mode line (stderr, so JSON on stdout stays machine-clean) then
    the JSON result on stdout."""
    print(mode, file=sys.stderr)
    print(json.dumps(payload, indent=2, sort_keys=True))

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND PreToolUse enforcement hook — the teeth.

This is the ONE mechanism that makes the governance language *binding* on an
agent's actions rather than merely *declared*. Claude Code invokes it before
**every** tool call — native ``Bash``/``Edit``/``Write``/``Read`` and every
``mcp__*`` tool alike — with the proposed call as JSON on stdin. The hook
resolves that call through RVND's one oversight chokepoint
(:func:`workspaces.governance.decide_action` — gate × matrix × oversight ×
privacy, recorded on the signed chain) and returns a **binding** verdict:

    permit / go    → exit 0                    (the call proceeds)
    hold   / ask   → permissionDecision "ask"  (routed to the human for sign-off)
    deny   / block → exit 2                    (the call is BLOCKED; reason → stderr)

**Fail-closed by construction.** Claude Code fails *open* on a hook timeout
(default 600 s), a missing script, or malformed output — so an honest gate must:
  (a) map every block to ``exit 2`` (the only exit the host *always* honours —
      it overrides JSON and cannot be bypassed by permission mode);
  (b) ``exit 2`` on ANY internal error rather than letting the call through; and
  (c) self-bound its own runtime (SIGALRM deadline) well under the configured
      host timeout, so a slow evaluation fails *closed* here instead of *open*
      at the host's timeout.

Modes — env ``RVND_HOOK_MODE`` (default ``enforce``):
    enforce   block on deny, ask on hold, fail closed on error   (teeth)
    monitor   evaluate + log the verdict, but NEVER block         (dry-run rollout)
    off       no-op                                               (disabled)

The action CLASSIFIER (:func:`classify`) that turns a tool call into a governed
action is deliberately minimal and conservative — *benign by default*, flagging
only a small set of high-signal danger patterns. It is the policy **seam**, not
a security boundary against an adversary that obfuscates its commands: the
*mechanism* (every call gated, verdict binding, fail-closed) is the invariant;
the classifier's *coverage* is a separate, improvable concern. Extend it, or
drive it from the workspace policy.

This module is ALSO its own installer: ``rvnd-hook --install`` merges the
PreToolUse entry into a ``.claude/settings.json`` (idempotent, backed up), and
``rvnd-hook --uninstall`` removes it. With no arguments it runs as the hook.

Internal by design: run by the host as a PreToolUse/PostToolUse hook (and as its
own ``rvnd-hook`` installer CLI); it is not part of the MCP tool surface that
``verify_surface`` tracks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ._storage_paths import LOG_ROOT_DEFAULT


# ── verdict vocabulary the classifier emits ────────────────────────────────
# Footprint tags MUST come from action_gate._RISK_MIN_GRADE or the gate ignores
# them. The starter set uses only the two that never require extra context:
#   irreversible     (min grade 3) → NO-GO below L3   → hard block
#   security-control (min grade 2) → sign-off at L2   → ask
# We deliberately do NOT emit ``external-publish`` here: the gate demands named
# affected_parties for it (Art. 50) and would auto-NO-GO an outbound read the
# hook cannot attribute. ``personal-data``/``financial`` are left as extension
# points — they need content inspection the starter ruleset doesn't attempt.
IRREVERSIBLE = "irreversible"
SECURITY_CONTROL = "security-control"

# High-signal danger patterns over a normalised command string. Conservative on
# purpose: a miss falls through to *benign*, which the gate (given the same empty
# footprint) would also wave through — so short-circuiting benign loses nothing a
# deep evaluation would have caught, except a painted workspace's matrix (opt in
# to that with RVND_HOOK_STRICT=1).
_IRREVERSIBLE_PATTERNS = (
    r"\brm\s+-[a-z]*r[a-z]*f",       # rm -rf / -fr / -Rf …
    r"\brm\s+-[a-z]*f[a-z]*r",
    r"\bgit\s+.*\b(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*(--force|-f)\b)",
    r"\bmkfs\b", r"\bshred\b", r"\bdd\s+if=", r">\s*/dev/(sd|disk|nvme)",
    r"\b(truncate|fdisk|parted)\b", r":\(\)\s*\{\s*:\|:", # fork bomb
)
_SECURITY_PATTERNS = (
    r"\bsudo\b", r"\bdoas\b", r"\bchmod\s+-?R?\s*777\b", r"\bchown\b",
    r"\b(launchctl|systemctl|service)\b", r"\bcsrutil\b", r"\bspctl\b",
    r"curl\s+[^|]*\|\s*(sudo\s+)?(ba)?sh", r"wget\s+[^|]*\|\s*(ba)?sh",
    r"\bpip\s+install\b.*--break-system-packages",
)
# Sensitive path prefixes: a write/edit whose target lives here is a
# security-control action regardless of how it's phrased.
_SENSITIVE_PATHS = (
    "/etc/", "/usr/", "/bin/", "/sbin/", "/System/", "/Library/LaunchDaemons",
)
_SENSITIVE_HOME = (".ssh", ".aws", ".gnupg", ".claude", ".config/gcloud")


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _touches_sensitive(path: str) -> bool:
    if not path:
        return False
    p = path.strip()
    if any(p.startswith(pre) or f" {pre}" in f" {p}" for pre in _SENSITIVE_PATHS):
        return True
    home = str(Path.home())
    try:
        rel = os.path.relpath(os.path.abspath(os.path.expanduser(p)), home)
    except Exception:
        rel = ""
    first = rel.split(os.sep)[0] if rel and not rel.startswith("..") else ""
    second = os.sep.join(rel.split(os.sep)[:2]) if rel else ""
    return first in _SENSITIVE_HOME or second in _SENSITIVE_HOME


def classify(tool_name: str, tool_input: dict[str, Any], cwd: str = "") -> tuple[
        str, tuple[str, ...], tuple[str, ...], list[dict[str, Any]]]:
    """Map a proposed tool call → ``(action_class, footprint, affected_parties,
    evidence)``.

    The policy seam. Deliberately minimal and conservative; benign by default.
    Footprint tags are drawn from the gate's risk vocabulary so the verdict is
    driven by the SAME substrate every other RVND caller uses. ``evidence`` is
    the GROUNDING for each footprint claim — the exact fragment of the action
    that triggered the tag, so a flagged verdict rests on a cited span rather than
    a bare assertion (the ``grounded`` pillar of a certification).
    """
    name = tool_name or ""
    ti = tool_input if isinstance(tool_input, dict) else {}
    foot: set[str] = set()
    evidence: list[dict[str, Any]] = []

    def _flag(tag: str, matched: str, start: int = -1, end: int = -1) -> None:
        foot.add(tag)
        evidence.append({"tag": tag, "matched": matched, "start": start, "end": end})

    # action_class: a stable, greppable identity per tool family.
    if name == "Bash":
        action_class = "shell.exec"
        cmd = _norm(str(ti.get("command", "")))
        low = cmd.lower()
        for p in _IRREVERSIBLE_PATTERNS:
            m = re.search(p, low)
            if m:
                _flag(IRREVERSIBLE, m.group(0), m.start(), m.end())
        for p in _SECURITY_PATTERNS:
            m = re.search(p, low)
            if m:
                _flag(SECURITY_CONTROL, m.group(0), m.start(), m.end())
        # A shell write into a sensitive path (cat > /etc/…, tee ~/.ssh/…).
        for m in re.finditer(r"(?:>>?|\btee\s+|\binto\s+)\s*([^\s;|&]+)", low):
            if _touches_sensitive(m.group(1)):
                _flag(SECURITY_CONTROL, m.group(1), m.start(1), m.end(1))
    elif name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        action_class = "fs.write"
        target = str(ti.get("file_path", "") or ti.get("notebook_path", ""))
        if _touches_sensitive(target):
            _flag(SECURITY_CONTROL, target)
    elif name in ("Read", "Glob", "Grep"):
        action_class = "fs.read"
    elif name == "WebFetch":
        action_class = "net.fetch"
    elif name == "WebSearch":
        action_class = "net.search"
    elif name.startswith("mcp__"):
        parts = name.split("__", 2)
        server = parts[1] if len(parts) > 1 else "?"
        tool = parts[2] if len(parts) > 2 else "?"
        action_class = f"mcp.{server}.{tool}"
    else:
        action_class = f"tool.{name.lower() or 'unknown'}"

    return action_class, tuple(sorted(foot)), (), evidence


# ── verdict → decision ──────────────────────────────────────────────────────
Decision = namedtuple("Decision", "kind reason detail")
# kind ∈ {"allow", "ask", "deny", "fail"}


def _grade() -> str:
    g = os.environ.get("RVND_AUTONOMY_GRADE", "L2").strip().upper()
    return g if re.fullmatch(r"L[0-4]", g) else "L2"


def _agent(evt: dict[str, Any]) -> str:
    return (os.environ.get("RVND_AGENT")
            or evt.get("agent_id") or evt.get("session_id") or "claude-code")


def _log_root() -> Optional[Path]:
    v = os.environ.get("RVND_HOOK_LOG_ROOT")
    return Path(v) if v else None


def _unblock_hint(footprint: tuple[str, ...]) -> str:
    """A short, honest 'what would let this through' for a blocked action, so the
    gate reads as a door with a key, not a dead end."""
    if not footprint:
        return ""
    return ("to proceed: raise the autonomy grade (RVND_AUTONOMY_GRADE), add a "
            "standing approval for this action class, or run it yourself")


def evaluate(evt: dict[str, Any],
             *, decide: Optional[Callable[..., dict[str, Any]]] = None) -> Decision:
    """Classify the call and resolve it to a :class:`Decision`.

    Never raises: any failure to classify or evaluate becomes ``Decision("fail",
    …)`` so the caller can fail closed. ``decide`` is injectable for tests;
    it defaults to the real chokepoint.
    """
    try:
        tool_name = str(evt.get("tool_name", ""))
        tool_input = evt.get("tool_input") or {}
        cwd = str(evt.get("cwd") or os.getcwd())
        action_class, footprint, affected, evidence = classify(tool_name, tool_input, cwd)

        # Fast benign path: no risk footprint AND not asked to be strict → allow
        # without the heavy chokepoint (and without a chain write per Read/Grep).
        # A flagged action NEVER takes this path — it is always fully evaluated
        # and recorded. In a painted workspace that wants even benign actions
        # matrixed, set RVND_HOOK_STRICT=1.
        strict = os.environ.get("RVND_HOOK_STRICT", "").strip() in ("1", "true", "yes")
        if not footprint and not strict:
            return Decision("allow", f"benign ({action_class}); no risk footprint", {})

        if decide is None:
            from .governance import decide_action as decide  # type: ignore

        gov = decide(cwd, action_class=action_class, grade=_grade(),
                     footprint=footprint, affected_parties=affected,
                     actor=_agent(evt), log_root=_log_root())
        light = str(gov.get("light") or "")
        reason = str(gov.get("reason") or "")
        # The STRUCTURAL reason (grade/footprint) is the actionable one — "grade L2
        # below required for irreversible (needs grade ≥ 3)" beats "gate NO-GO".
        why = str(gov.get("gate_reason") or "") or reason
        detail = {"action_class": action_class, "footprint": list(footprint),
                  "evidence": evidence, "verdict": gov.get("verdict"),
                  "audit_id": gov.get("audit_id"),
                  # policy-side grounding for the certification's `legitimate` pillar
                  "oversight_level": gov.get("oversight_level"),
                  "grade": gov.get("grade"),
                  "gate_verdict": gov.get("gate_verdict"),
                  "obligation_pairs": gov.get("obligation_pairs") or [],
                  "policy_digest": gov.get("policy_digest", ""),
                  # grounding SIGNAL + risk traffic light (green/amber/red)
                  "grounded": bool(gov.get("grounded")),
                  "traffic_light": gov.get("traffic_light") or "amber"}
        if light == "go":
            return Decision("allow", why or "permitted", detail)
        if light == "ask":
            return Decision("ask", (why or "requires human sign-off")
                            + " — approve to proceed, or decline", detail)
        if light == "block":
            hint = _unblock_hint(footprint)
            return Decision("deny", f"{why or 'blocked by policy'}"
                            + (f". {hint}" if hint else ""), detail)
        # Unrecognised verdict → fail closed, never guess "allow".
        return Decision("fail", f"unrecognised verdict {light!r}", detail)
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 — ANY failure must fail closed
        return Decision("fail", f"{type(e).__name__}: {e}", {})


# ── emit: turn a Decision into the exit code / output the host honours ───────
def _mode() -> str:
    return os.environ.get("RVND_HOOK_MODE", "enforce").strip().lower() or "enforce"


def emit(decision: Decision, mode: Optional[str] = None) -> None:
    """Exit the process with the code/output Claude Code interprets.

    ``deny`` and ``fail`` both hard-block via ``exit 2`` in ``enforce`` mode —
    the only exit the host always honours. In ``monitor`` mode nothing blocks;
    the would-be verdict is written to stderr for the operator and the call is
    allowed to proceed.
    """
    mode = mode or _mode()
    kind = decision.kind
    if kind == "allow":
        sys.exit(0)  # no output → normal permission flow; RVND does not rubber-stamp

    if kind == "ask":
        if mode == "monitor":
            print(f"[rvnd:monitor] would ASK (sign-off): {decision.reason}", file=sys.stderr)
            sys.exit(0)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"[rvnd] {decision.reason}",
        }}))
        sys.exit(0)

    if kind == "deny":
        if mode == "monitor":
            print(f"[rvnd:monitor] would BLOCK: {decision.reason}", file=sys.stderr)
            sys.exit(0)
        print(f"[rvnd] blocked by governance: {decision.reason}", file=sys.stderr)
        sys.exit(2)

    # kind == "fail" → fail CLOSED (block) in enforce; log-only in monitor.
    if mode == "monitor":
        print(f"[rvnd:monitor] evaluation error (would fail closed): {decision.reason}",
              file=sys.stderr)
        sys.exit(0)
    print(f"[rvnd] failing closed — governance unavailable: {decision.reason}. "
          f"Remove the RVND PreToolUse hook from .claude/settings.json to disable.",
          file=sys.stderr)
    sys.exit(2)


def _arm_deadline(mode: str) -> None:
    """Self-imposed deadline so a slow evaluation fails CLOSED here, before the
    host's fail-OPEN timeout. No-op where SIGALRM is unavailable (non-Unix)."""
    if not hasattr(signal, "SIGALRM"):
        return
    try:
        seconds = int(os.environ.get("RVND_HOOK_DEADLINE", "15"))
    except ValueError:
        seconds = 15

    def _on_timeout(signum, frame):  # noqa: ANN001
        emit(Decision("fail", "governance evaluation exceeded deadline", {}), mode)

    signal.signal(signal.SIGALRM, _on_timeout)
    signal.alarm(max(1, seconds))


# ── cert-loop: mark a HELD action, mint a certification on approval ─────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pending_dir() -> Path:
    base = _log_root() or LOG_ROOT_DEFAULT
    d = Path(base) / "hook-pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker_path(tool_use_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(tool_use_id))[:120] or "unknown"
    return _pending_dir() / f"{safe}.json"


def _mark_held(evt: dict[str, Any], decision: Decision) -> None:
    """Record a HELD action keyed by ``tool_use_id`` so the PostToolUse companion
    can mint a certification IF the human approves (i.e. the tool then runs).
    Best-effort — a witness must never break the decision it records."""
    try:
        tuid = str(evt.get("tool_use_id") or "")
        if not tuid:
            return
        detail = decision.detail or {}
        ti = evt.get("tool_input") or {}
        action_digest = _sha256_hex(json.dumps(
            {"tool_name": evt.get("tool_name"), "tool_input": ti},
            sort_keys=True, separators=(",", ":")).encode("utf-8"))
        marker = {
            "at": _now_iso(),
            "agent": _agent(evt),
            "folder": str(evt.get("cwd") or ""),
            "action_class": detail.get("action_class", ""),
            "audit_id": detail.get("audit_id", "") or "",
            "action_digest": action_digest,
            "evidence": detail.get("evidence") or [],
            "reason": decision.reason,
            "mechanism": "claude-code:PreToolUse",
            # policy-side grounding → the certification's `legitimate` pillar
            "oversight_level": detail.get("oversight_level") or "",
            "grade": detail.get("grade") or "",
            "gate_verdict": detail.get("gate_verdict") or "",
            "obligation_pairs": detail.get("obligation_pairs") or [],
            "policy_digest": detail.get("policy_digest") or "",
            "grounded": bool(detail.get("grounded")),
            "traffic_light": detail.get("traffic_light") or "amber",
        }
        _marker_path(tuid).write_text(json.dumps(marker), encoding="utf-8")
    except Exception:
        pass


def _run_posttooluse(evt: dict[str, Any]) -> None:
    """After a tool runs: if it was a HELD action the human approved, mint a
    GovernanceCertification from the recorded gate event. Best-effort; never
    blocks (the tool already ran); the caller always exits 0."""
    try:
        tuid = str(evt.get("tool_use_id") or "")
        path = _marker_path(tuid) if tuid else None
        if not path or not path.exists():
            return
        marker = json.loads(path.read_text(encoding="utf-8"))
        try:
            path.unlink()          # consume once
        except Exception:
            pass
        from .governance_cert import emit_governance_certification
        folder = marker.get("folder") or str(evt.get("cwd") or os.getcwd())
        env = emit_governance_certification(folder, marker=marker, log_root=_log_root())
        if env is not None:
            print(f"[rvnd] GovernanceCertification minted for "
                  f"{marker.get('action_class', 'action')} "
                  f"(audit {marker.get('audit_id', '')})", file=sys.stderr)
    except Exception:
        pass


def run_hook(stdin_text: Optional[str] = None) -> None:
    """Read a hook event, dispatch by kind, and emit.

    PreToolUse — evaluate → allow / ask / deny (fail-closed); a HELD (ask) action
    is marked so its approval can be certified. PostToolUse — mint a
    GovernanceCertification for a held action the human just approved.
    """
    mode = _mode()
    if mode == "off":
        sys.exit(0)
    raw = sys.stdin.read() if stdin_text is None else stdin_text
    try:
        evt = json.loads(raw) if raw and raw.strip() else {}
        if not isinstance(evt, dict):
            raise ValueError("hook input is not a JSON object")
    except Exception as e:
        # Unparseable: we cannot tell events apart, so fail CLOSED (the safe
        # default for the enforcement event); a spurious exit 2 on PostToolUse is
        # harmless because the tool has already run.
        emit(Decision("fail", f"unparseable hook input: {e}", {}), mode)
        return

    # Both paths touch the workspace store — PreToolUse records the gate decision,
    # PostToolUse PERSISTS the certificate — and the hook governs AMBIENT folders,
    # so an un-painted folder is evaluated/recorded under the GLOBAL DEFAULT rather
    # than refused by the known-workspaces allowlist (the MCP server's data-boundary
    # control, not the hook's). An operator who set this to "0" is respected —
    # setdefault never overrides. (Set before dispatch: without it the PostToolUse
    # persist is silently refused and no certificate reaches disk.)
    os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

    if str(evt.get("hook_event_name") or "PreToolUse") == "PostToolUse":
        _run_posttooluse(evt)
        sys.exit(0)

    # PreToolUse — the enforcement path.
    _arm_deadline(mode)
    decision = evaluate(evt)
    if decision.kind == "ask" and mode == "enforce":
        _mark_held(evt, decision)   # a real prompt (enforce) is a real hold
    emit(decision, mode)


# ── installer: manage the .claude/settings.json hook entries ─────────────────
# Two events: PreToolUse is the enforcement gate; PostToolUse is the companion
# that mints a GovernanceCertification when a HELD action is approved. Same
# command serves both (it dispatches on hook_event_name).
_MATCHER_ALL = "*"
_MARKER = "workspaces.hook"   # how we recognise OUR entry for idempotency/removal
_EVENTS = ("PreToolUse", "PostToolUse")


def _settings_path(scope: str, base_dir: Optional[str]) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path(base_dir or os.getcwd()) / ".claude" / "settings.json"


def _hook_command() -> str:
    """The command Claude Code should run. Prefer an absolute path to the
    installed ``rvnd-hook`` console script; fall back to ``python -m``."""
    import shutil
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name.startswith("rvnd-hook") and argv0.exists():
        return str(argv0.resolve())
    which = shutil.which("rvnd-hook")
    if which:
        return which
    return f"{sys.executable} -m workspaces.hook"


def _is_ours(entry: dict[str, Any]) -> bool:
    for h in entry.get("hooks", []) or []:
        if _MARKER in str(h.get("command", "")) or "rvnd-hook" in str(h.get("command", "")):
            return True
    return False


def _install(scope: str, base_dir: Optional[str], timeout: int,
             command: Optional[str] = None) -> Path:
    path = _settings_path(scope, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception as e:
            raise SystemExit(f"refusing to edit unparseable {path}: {e}")
        # one-time backup before we touch a pre-existing file
        bak = path.with_suffix(".json.rvnd-bak")
        if not bak.exists():
            bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    cmd = command or _hook_command()
    hooks = data.setdefault("hooks", {})
    for event in _EVENTS:
        arr = hooks.setdefault(event, [])
        if any(e.get("matcher") in (_MATCHER_ALL, "", None) and _is_ours(e) for e in arr):
            continue  # already installed for this event → idempotent
        arr.append({
            "matcher": _MATCHER_ALL,
            "hooks": [{"type": "command", "command": cmd, "timeout": timeout}],
        })
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _uninstall(scope: str, base_dir: Optional[str]) -> tuple[Path, int]:
    path = _settings_path(scope, base_dir)
    if not path.exists():
        return path, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        raise SystemExit(f"refusing to edit unparseable {path}: {e}")
    hooks = data.get("hooks") or {}
    removed = 0
    for event in _EVENTS:
        arr = hooks.get(event) or []
        kept = [e for e in arr if not _is_ours(e)]
        removed += len(arr) - len(kept)
        if len(kept) != len(arr):
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
    if removed:
        if not hooks:
            data.pop("hooks", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, removed


def _installed_at(path: Path) -> bool:
    """True if OUR entry is present under any managed event in ``path``."""
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return False
    hooks = data.get("hooks") or {}
    return any(_is_ours(e) for event in _EVENTS for e in (hooks.get(event) or []))


def _scan(base_dir: Optional[str]) -> list[tuple[str, Path, bool]]:
    """Both scopes RVND ever writes to, each with its install state."""
    return [(scope, p, _installed_at(p)) for scope, p in (
        ("project", _settings_path("project", base_dir)),
        ("user", _settings_path("user", None)),
    )]


def _confirm(prompt: str, *, default: bool = True) -> bool:
    """Yes/no prompt. Non-interactive (no TTY / EOF) → the default, so the
    wizard never hangs in a pipe or CI."""
    if not sys.stdin.isatty():
        return default
    try:
        ans = input(f"{prompt}{' [Y/n] ' if default else ' [y/N] '}").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans in ("y", "yes")


def _uninstall_wizard(base_dir: Optional[str], *, assume_yes: bool,
                      confirm: Optional[Callable[[str, Path], bool]] = None) -> int:
    """Interactive deinstall: find the hook across scopes, show what's there,
    confirm each removal. ``confirm`` is injectable for tests; ``assume_yes``
    (or a non-TTY) removes every found entry without prompting."""
    scanned = _scan(base_dir)
    found = [(scope, path) for scope, path, ok in scanned if ok]
    if not found:
        print("· No RVND enforcement hook is installed.")
        for scope, path, _ in scanned:
            print(f"    checked {scope}: {path}")
        if os.environ.get("RVND_HOOK_MODE"):
            print(f"    (note: RVND_HOOK_MODE={os.environ['RVND_HOOK_MODE']} is still set "
                  f"in this shell — that's an env var, not a settings file.)")
        return 0

    print(f"Found the RVND enforcement hook in {len(found)} place(s):")
    for scope, path in found:
        print(f"  • {scope}: {path}")
    print("  (Softer than removing: export RVND_HOOK_MODE=off — disables it, keeps it wired.)\n")

    if confirm is None:
        confirm = ((lambda s, p: True) if assume_yes
                   else (lambda s, p: _confirm(f"  Remove the hook from {s} ({p})?")))
    removed = 0
    backup = False
    for scope, path in found:
        if confirm(scope, path):
            _, n = _uninstall(scope, base_dir)
            removed += n
            backup = backup or path.with_suffix(".json.rvnd-bak").exists()
            print(f"  ✓ removed from {path}")
        else:
            print(f"  · kept {path}")
    print(f"\nDone — removed the RVND hook from {removed} location(s).")
    if backup:
        print("  A settings.json.rvnd-bak backup is beside any file that had one.")
    return 0


def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="rvnd-hook",
        description="Manage the RVND PreToolUse enforcement hook. "
                    "With no arguments, runs AS the hook (reads a PreToolUse "
                    "event on stdin). `--uninstall` with no --scope is an "
                    "interactive wizard that scans both scopes.")
    ap.add_argument("--install", action="store_true", help="add the hook to settings.json")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the hook (wizard when --scope is omitted)")
    ap.add_argument("--status", action="store_true", help="report where the hook is installed")
    ap.add_argument("--scope", choices=("project", "user"), default=None,
                    help="project = ./.claude/settings.json; user = ~/.claude/settings.json. "
                         "Omit on --uninstall/--status to cover BOTH.")
    ap.add_argument("--dir", default=None, help="base dir for project scope (default: cwd)")
    ap.add_argument("--timeout", type=int, default=30,
                    help="host hook timeout in seconds (must exceed RVND_HOOK_DEADLINE)")
    ap.add_argument("--command", default=None,
                    help="override the command string written to settings.json "
                         "(default: absolute rvnd-hook, else 'python -m workspaces.hook')")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="don't prompt; assume yes (for the uninstall wizard)")
    args = ap.parse_args(argv)

    if args.install:
        scope = args.scope or "project"
        path = _install(scope, args.dir, args.timeout, args.command)
        mode = _mode()
        print(f"✓ RVND enforcement hook installed ({scope}): {path}")
        print(f"  mode={mode}  command={_hook_command()}")
        if mode != "enforce":
            print(f"  NOTE: RVND_HOOK_MODE={mode} — not blocking. Set enforce for teeth.")
        print("  Uninstall anytime with the wizard:  rvnd-hook --uninstall")
        return 0

    if args.uninstall:
        if args.scope:                       # explicit scope → scriptable, no prompt
            path, n = _uninstall(args.scope, args.dir)
            print(f"✓ removed {n} RVND hook entr{'y' if n == 1 else 'ies'} from {path}"
                  if n else f"· no RVND hook entry found in {path}")
            return 0
        return _uninstall_wizard(args.dir, assume_yes=args.yes)  # the wizard

    if args.status:
        scopes = [args.scope] if args.scope else ["project", "user"]
        for sc in scopes:
            p = _settings_path(sc, args.dir)
            print(f"{'installed' if _installed_at(p) else 'not installed'} ({sc}): {p}")
        print(f"mode={_mode()}  command={_hook_command()}")
        return 0

    ap.print_help()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:                       # admin CLI (--install/--uninstall/--status)
        return _cli(argv)
    run_hook()                     # no args → run AS the hook (stdin → verdict)
    return 0                        # unreachable (run_hook calls sys.exit)


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Interactive review — offer every PII finding to the user for case-by-case decision.

Implements the SUPERVISED (level 5) oversight mode for both individual findings
and orchestrator runs. The UX pattern:
ASCII headers, +/✓/- markers, JSON-by-default with --human pretty mode.

Two entry points:

- `review_findings()` — for a list of Finding objects (from egress/ingress),
  prompts the user per-finding and returns the user's decisions.

- `interactive_cli()` — full CLI: reads stdin JSON, runs egress/ingress, presents
  findings, captures decisions, writes a record to stdout.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import asdict
from typing import IO, Callable

from .core import (
    Finding,
    EgressDecision,
    IngressDecision,
    Mode,
    ToolCall,
    ToolResponse,
    CapabilityToken,
    egress,
    ingress,
)
from .oversight import (
    OversightLevel,
    OversightDecision,
    asks_user_per_finding,
)


# ---------------------------------------------------------------------------
# ASCII output helpers
# ---------------------------------------------------------------------------


def render_header(title: str, out: IO[str]) -> None:
    """━━ HEADER ━━."""
    out.write(f"━━ {title} ━━\n")


def render_loaded(label: str, detail: str, out: IO[str]) -> None:
    """+ loaded thing (n items)"""
    out.write(f"  + {label} {detail}\n")


def render_ok(label: str, detail: str, out: IO[str]) -> None:
    """✓ ok in 2.3 ms"""
    out.write(f"  ✓ {label} {detail}\n")


def render_finding(idx: int, total: int, finding: Finding, out: IO[str]) -> None:
    """Pretty-print one finding in human-readable form."""
    sev = finding.severity.upper()
    severity_marker = {"HIGH": "●", "MEDIUM": "◐", "LOW": "○"}.get(sev, "·")
    out.write(f"\n  [{idx}/{total}] {severity_marker} {sev}  {finding.type}\n")
    if finding.field:
        out.write(f"        field:      {finding.field}\n")
    out.write(f"        tier:       {finding.tier}\n")
    out.write(f"        detail:     {finding.detail}\n")
    out.write(f"        confidence: {finding.confidence:.2f}\n")


# ---------------------------------------------------------------------------
# Per-finding review prompt
# ---------------------------------------------------------------------------


def _prompt(
    finding: Finding,
    *,
    stdin: IO[str],
    stdout: IO[str],
    auto_decision: str | None = None,
) -> tuple[str, str]:
    """Prompt the user for one finding's decision. Returns (action, reason)."""
    if auto_decision is not None:
        return (auto_decision, "auto-decided (non-interactive run)")

    stdout.write("\n        action? [a]ccept  [r]eject  [w]aive  [s]kip  [e]xplain\n")
    stdout.write("        > ")
    stdout.flush()
    raw = stdin.readline().strip().lower()
    if not raw:
        return ("skip", "no input")
    char = raw[0]
    if char == "a":
        return ("accept", "")
    elif char == "r":
        return ("reject", _ask_reason("Reject reason", stdin, stdout))
    elif char == "w":
        return ("waive", _ask_reason("Waiver justification (recorded in audit)", stdin, stdout))
    elif char == "e":
        # Show more context, then re-prompt
        stdout.write(f"\n        recommendation:\n          {_get_recommendation(finding)}\n")
        return _prompt(finding, stdin=stdin, stdout=stdout, auto_decision=auto_decision)
    else:
        return ("skip", f"unrecognised input: {raw[:20]}")


def _ask_reason(prompt: str, stdin: IO[str], stdout: IO[str]) -> str:
    stdout.write(f"        {prompt}: ")
    stdout.flush()
    return stdin.readline().strip()


def _get_recommendation(finding: Finding) -> str:
    """Map finding type → recommendation string."""
    if finding.type == "over_collection":
        return "Remove the over-collected field from the tool call; or expand task scope if legitimately needed."
    if finding.type == "pii_in_argument":
        return "Pseudonymise the argument before sending; or remove if not strictly necessary."
    if finding.type == "pii_in_response":
        return "Redact the response field before it enters the agent's context window."
    if finding.type == "token_invalid":
        return "Refresh or correct the capability token; if missing, the runtime falls back to inferred scope."
    return "(no specific recommendation)"


# ---------------------------------------------------------------------------
# Public API — review a list of findings
# ---------------------------------------------------------------------------


def review_findings(
    findings: list[Finding],
    *,
    oversight: OversightLevel = OversightLevel.SUPERVISED,
    stdin: IO[str] = sys.stdin,
    stdout: IO[str] = sys.stdout,
    auto_decision: str | None = None,
) -> list[OversightDecision]:
    """Walk through findings and capture per-finding user decisions.

    Args:
        findings: List of Finding objects to review.
        oversight: Oversight level — only SUPERVISED and MANUAL trigger per-finding prompts.
            APPROVE shows the full list and asks a single accept/reject.
            Lower levels skip review entirely (return empty decisions).
        stdin / stdout: I/O streams. Inject for testability.
        auto_decision: If set, every finding gets this action (used in non-interactive runs).

    Returns:
        List of OversightDecision matching the findings (one per finding, same order).
    """
    if not asks_user_per_finding(oversight) and oversight != OversightLevel.APPROVE:
        # autonomous / notify / review — no per-finding interaction
        return [
            OversightDecision(
                finding_id=str(uuid.uuid4()),
                user_action="auto-accepted",
                reason=f"oversight={oversight.label}",
            )
            for _ in findings
        ]

    render_header(f"finding review — oversight: {oversight.label} (level {oversight.value})", stdout)
    stdout.write(f"  {len(findings)} finding(s) to review.\n")

    decisions: list[OversightDecision] = []

    if oversight == OversightLevel.APPROVE:
        # Show all, ask one accept/reject
        for i, f in enumerate(findings, 1):
            render_finding(i, len(findings), f, stdout)
        stdout.write("\n        Accept ALL findings as-is? [y/n] > ")
        stdout.flush()
        raw = stdin.readline().strip().lower() if auto_decision is None else "y"
        action = "accept" if raw.startswith("y") else "reject"
        for f in findings:
            decisions.append(OversightDecision(
                finding_id=str(uuid.uuid4()),
                user_action=action,
                reason="batch decision in approve mode",
            ))
        return decisions

    # SUPERVISED or MANUAL — per-finding
    for i, f in enumerate(findings, 1):
        t0 = time.time()
        render_finding(i, len(findings), f, stdout)
        action, reason = _prompt(f, stdin=stdin, stdout=stdout, auto_decision=auto_decision)
        elapsed_ms = int((time.time() - t0) * 1000)
        decisions.append(OversightDecision(
            finding_id=str(uuid.uuid4()),
            user_action=action,
            reason=reason,
            elapsed_ms=elapsed_ms,
        ))
        stdout.write(f"        → {action}\n")

    render_ok("review complete",
              f"({sum(1 for d in decisions if d.user_action == 'accept')} accepted, "
              f"{sum(1 for d in decisions if d.user_action == 'reject')} rejected, "
              f"{sum(1 for d in decisions if d.user_action == 'waive')} waived)",
              stdout)
    return decisions


# ---------------------------------------------------------------------------
# CLI — `python -m workspaces.lock.interactive`
# ---------------------------------------------------------------------------


def interactive_cli(
    *,
    argv: list[str] | None = None,
    stdin: IO[str] = sys.stdin,
    stdout: IO[str] = sys.stdout,
    stderr: IO[str] = sys.stderr,
) -> int:
    """CLI: read a tool-call spec from stdin, run egress, present findings for review.

    Input format on stdin (one JSON object):
        {
            "tool": "hr.get_employee",
            "arguments": {"employee_id": "E-1", "include_salary_band": true},
            "task_scope": ["employee_id"],
            "capability_token": null
        }

    Args from argv:
        --oversight {autonomous|notify|review|approve|supervised|manual}  (default: supervised)
        --auto {accept|reject|waive}  (non-interactive; auto-decides every finding)

    Output (to stdout):
        Human-readable findings + decisions section, then a JSON record on the final line.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="agent-tool-lock review")
    parser.add_argument(
        "--oversight",
        default="supervised",
        choices=["autonomous", "notify", "review", "approve", "supervised", "manual"],
    )
    parser.add_argument("--auto", default=None, choices=["accept", "reject", "waive"])
    parser.add_argument("--mode", default="permissive", choices=["standard", "strict", "permissive", "audit_only"])
    args = parser.parse_args(argv)

    oversight = OversightLevel[args.oversight.upper()]
    mode = Mode(args.mode)

    # Read the call spec from stdin
    render_header("agent-tool-lock review", stdout)
    raw = stdin.read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        stderr.write(f"error: invalid JSON on stdin: {e}\n")
        return 1

    tool = spec.get("tool", "")
    arguments = spec.get("arguments", {})
    task_scope = set(spec.get("task_scope", []))
    token_dict = spec.get("capability_token")
    token = CapabilityToken.from_dict(token_dict) if token_dict else None

    render_loaded("tool", f"`{tool}`", stdout)
    render_loaded("task scope", f"({len(task_scope)} field(s): {sorted(task_scope)})", stdout)
    render_loaded("oversight", f"{oversight.label} (level {oversight.value})", stdout)

    # Egress
    call = ToolCall(tool=tool, arguments=arguments, capability_token=token)
    t0 = time.time()
    decision = egress(call, task_scope=task_scope, mode=mode)
    elapsed = (time.time() - t0) * 1000
    render_ok("egress", f"in {elapsed:.1f} ms — {decision.action}", stdout)

    # Review findings
    decisions = review_findings(
        decision.findings,
        oversight=oversight,
        stdin=stdin,
        stdout=stdout,
        auto_decision=args.auto,
    )

    # Final JSON record
    record = {
        "tool": tool,
        "oversight": oversight.label,
        "egress_action": decision.action,
        "findings_count": len(decision.findings),
        "findings": [
            {**asdict(f), "tier": f.tier, "type": f.type, "severity": f.severity}
            for f in decision.findings
        ],
        "decisions": [asdict(d) for d in decisions],
    }

    stdout.write("\n━━ RECORD (JSONL) ━━\n")
    stdout.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(interactive_cli())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Cleartext / ScannedResponse — type-disciplined egress invariant.

Implements the MCP-security design (gate A17):

    def workspace_query(req: QueryRequest) -> ScannedResponse:
        raw: Cleartext[QueryResult] = frontalkg.query(req)
        return privacy_lock.scan(raw)   # Cleartext → ScannedResponse

The MCP transport handler **only accepts ``ScannedResponse``** as a
return type. A function returning raw ``Cleartext[T]`` fails type-check
at build, and the runtime guard at the egress boundary refuses anything
else.

Two wrapper classes:

- ``Cleartext[T]``         — pre-lock data. The type-checker (mypy)
                              and the runtime guard both refuse this at
                              egress. Carrying ``Cleartext[T]`` across a
                              trust boundary is a build error.
- ``ScannedResponse[T]``   — post-lock data. Carries the payload + a
                              short audit record describing what lock
                              did. Has ``to_mcp_payload()`` which yields
                              the dict FastMCP serialises. The audit
                              record is included so the caller (artifact,
                              human reviewer) can see what lock ran.

Usage at the MCP tool boundary:

    @mcp.tool()
    def pairs_safe_context_for_query(...) -> dict[str, Any]:
        ...
        views = [_safe_view(p, folder, mode) for p in pairs]
        # Wrap before egress. Runtime guard refuses a missing wrap.
        scanned = ScannedResponse({
            "folder_context": ...,
            "views": views,
            ...
        }, audit={"lock_pass": True, "total_findings": ...})
        return scanned.to_mcp_payload()

The ``to_mcp_payload()`` call is the only sanctioned exit. Direct returns
of a raw dict from an MCP tool that needs lock protection should fail
mypy if the tool is annotated as returning ``ScannedResponse``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


class CleartextEgressError(RuntimeError):
    """Raised when something other than a ScannedResponse reaches an
    egress boundary. The runtime guard's failure mode."""


@dataclass(frozen=True)
class Cleartext(Generic[T]):
    """Wraps data that has NOT yet been through the lock.

    Carrying a Cleartext across an egress boundary is a build error:
    egress functions annotate their return as ``ScannedResponse[T]``,
    and mypy will reject a Cleartext at the return. The runtime guard
    in ``assert_scanned()`` is the belt-and-braces second check.

    To produce a ScannedResponse from a Cleartext, run it through
    the lock (typically ``lock_text`` or a Tier B/C cascade) and
    construct the ScannedResponse from the result.
    """

    value: T

    def unwrap_for_lock(self) -> T:
        """Explicit unwrap for code that's specifically operating
        pre-lock (e.g. the lock itself). Never use this from an
        egress path."""
        return self.value


@dataclass(frozen=True)
class LockAudit:
    """Minimal audit record carried by every ScannedResponse.

    Records what lock did: which tier ran, how many findings, whether
    any spans were refused. Persisted on the response so the artifact /
    HITL UI / log can show the user what protection fired.
    """

    tier: str = "A"                # "A" | "B" | "C" | "ingest-cached"
    total_findings: int = 0
    refused: int = 0
    minimised: int = 0
    notes: str = ""


@dataclass(frozen=True)
class ScannedResponse(Generic[T]):
    """Wraps data that HAS been through the lock.

    Only acceptable type to cross an egress boundary (MCP return, BYOK
    cloud LLM call, marketplace publication). Type-checked at build by
    mypy; runtime-checked by ``assert_scanned()`` at the boundary.

    Use ``to_mcp_payload()`` to render to the dict shape FastMCP
    transports. The dict carries the data plus a ``lock`` audit block
    so the caller can see what protection ran.
    """

    value: T
    audit: LockAudit = field(default_factory=LockAudit)

    def to_mcp_payload(self) -> dict[str, Any]:
        """Render to the transport dict. FastMCP serialises this.

        The returned dict contains the original value's fields plus a
        ``_lock`` key with the audit record. If ``value`` is not a
        dict, it's wrapped under a ``value`` key.
        """
        if isinstance(self.value, dict):
            out: dict[str, Any] = dict(self.value)
        else:
            out = {"value": self.value}
        # Don't clobber an explicit lock block the caller already set;
        # merge if present, otherwise add.
        existing = out.get("lock")
        if isinstance(existing, dict):
            existing.setdefault("egress_tier", self.audit.tier)
            existing.setdefault("egress_total_findings", self.audit.total_findings)
        else:
            out["_lock_egress"] = {
                "tier":            self.audit.tier,
                "total_findings":  self.audit.total_findings,
                "refused":         self.audit.refused,
                "minimised":       self.audit.minimised,
                "notes":           self.audit.notes,
            }
        return out


def assert_scanned(obj: Any) -> None:
    """Runtime guard. Call at the egress boundary just before serialising.

    Refuses anything that isn't a ``ScannedResponse``. The type system
    catches this case at build; this runtime check is the second layer.
    """
    if isinstance(obj, Cleartext):
        raise CleartextEgressError(
            "Cleartext crossed an egress boundary. Wrap in ScannedResponse "
            "after running lock, or use scanned.to_mcp_payload() to "
            "exit the trust boundary."
        )
    if not isinstance(obj, ScannedResponse):
        raise CleartextEgressError(
            f"egress boundary expected ScannedResponse, got {type(obj).__name__}. "
            "Wrap your response in ScannedResponse and call to_mcp_payload()."
        )


def scan_payload(value: T, audit: LockAudit | None = None) -> ScannedResponse[T]:
    """Convenience factory. Wraps a value as a ScannedResponse.

    Use this when you've already run the lock over the value and want
    a clean construction site. The audit defaults to "no findings"; pass
    a real LockAudit when lock actually ran and produced numbers.
    """
    return ScannedResponse(value=value, audit=audit or LockAudit())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""mcp_tool adapter — wraps another MCP tool as a pinnable Workspace skill.

This is the safest of the four adapter kinds because the host already audits
MCP tool calls. Workspace just adds the folder-scope wrap on top (which the
orchestrator's dispatch_skill flow handles outside the adapter).

The adapter receives a dispatch payload, applies optional arg-mapping, and
returns a callable description of the MCP tool call. Actual invocation is
delegated to the host's MCP client — the adapter does not invoke the tool
itself (that would bypass the host's tool-call audit).

Design note: returning a "call descriptor" rather than directly invoking
keeps the adapter testable without a live MCP host, and lets the
orchestrator decide how to surface the tool call to the user (visible tool
use, hidden invocation, batched dispatch, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adapter_loader import AdapterDeclaration


@dataclass
class McpToolAdapter:
    """Adapter that maps a Workspace dispatch into a host MCP tool call."""

    kind: str = "mcp_tool"
    decl: AdapterDeclaration = None  # type: ignore[assignment]

    def dispatch(self, payload: dict[str, Any], *,
                 folder_context: str | None = None) -> dict[str, Any]:
        """Translate the dispatch payload into an MCP tool-call descriptor.

        Returns a dict the orchestrator hands to the host's MCP client. The
        host invokes the tool; the response feeds back through the
        orchestrator's capture + audit wrap.
        """
        cfg = self.decl.kind_config
        tool_name = cfg.get("tool_name")
        if not tool_name:
            raise ValueError(
                f"adapter {self.decl.name!r} (kind=mcp_tool) missing "
                f"required 'tool_name' in kind_config"
            )

        # Apply arg_mapping if declared. arg_mapping translates dispatch
        # payload keys into the underlying tool's argument names.
        arg_mapping = cfg.get("arg_mapping") or {}
        if arg_mapping:
            mapped: dict[str, Any] = {}
            for src_key, value in payload.items():
                target_key = arg_mapping.get(src_key, src_key)
                mapped[target_key] = value
            tool_args = mapped
        else:
            tool_args = dict(payload)

        # Add folder_context if the underlying tool accepts it. Most Workspace
        # tools do; some third-party tools won't. The adapter is conservative
        # — it only adds it when the input_schema names it explicitly.
        schema_props = (self.decl.input_schema or {}).get("properties", {})
        if "folder_context" in schema_props and folder_context is not None:
            tool_args.setdefault("folder_context", folder_context)

        return {
            "kind": "mcp_tool_call_descriptor",
            "tool_name": tool_name,
            "args": tool_args,
            # Tag the call with adapter provenance so the audit chain can
            # link the tool invocation to the dispatch envelope.
            "adapter_provenance": {
                "adapter_name": self.decl.name,
                "adapter_kind": "mcp_tool",
                "folder_context": folder_context,
            },
        }


def build(decl: AdapterDeclaration) -> McpToolAdapter:
    """Factory the loader calls to instantiate this adapter kind."""
    return McpToolAdapter(decl=decl)

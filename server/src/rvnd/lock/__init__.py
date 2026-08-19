# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""agent-tool-lock runtime — pre-call and post-call middleware for AI-agent tool invocations.

Boundary contract: this package is the server's enforcement core. Imports
crossing the package boundary in either direction are a ratchet gated by
scripts/lock_boundary_check.py against the committed baseline — new inbound
uses go through this module's re-exports, new outbound needs are injected,
and the baseline only shrinks.
"""

from .core import (
    Mode,
    ToolCall,
    ToolResponse,
    CapabilityToken,
    EgressDecision,
    IngressDecision,
    TextDecision,
    Finding,
    egress,
    ingress,
    lock_text,
    tier_b_scan_text,
    redact_for_capture,
    AuditLog,
)
from .broker_probe import probe_broker
from .credential_resolver import describe, is_valid_ref
from .injection_scan import scan_document, scan_text
from .scanned_response import ScannedResponse, LockAudit, assert_scanned
from .onboarding.config import apply_config_to_env, default_config_path
from .oversight import (
    OversightLevel,
    OversightDecision,
    PRIVACY_CLASS_DEFAULTS,
    effective_level,
)
from .interactive import review_findings, interactive_cli
from .tier_c import (
    tier_c_check_semantic,
    is_tier_c_available,
    describe_tier_c,
    tier_c_requires_real_backend,
    reset_backend_cache,
)
from .backends import make_local_llm, BackendError
from .onboarding import run_wizard, Config, load_config, save_config
from .obsidian_kg import kg_context_for_vault
from .decisions import DecisionsStore, StoredDecision
from .gate import GateDecision, gate_for_cloud
from .gate_and_capture import (
    GateAndCaptureResult,
    gate_and_capture_llm,
    gate_and_capture_web,
)
from .l0_bridge import (
    BridgeCaptureResult,
    PolicySnapshot,
    try_capture_llm,
    try_capture_web,
    try_load_policy,
)

__all__ = [
    "Mode",
    "ToolCall",
    "ToolResponse",
    "CapabilityToken",
    "EgressDecision",
    "IngressDecision",
    "TextDecision",
    "Finding",
    "egress",
    "ingress",
    "lock_text",
    "AuditLog",
    "OversightLevel",
    "OversightDecision",
    "PRIVACY_CLASS_DEFAULTS",
    "effective_level",
    "review_findings",
    "interactive_cli",
    "tier_c_check_semantic",
    "is_tier_c_available",
    "describe_tier_c",
    "reset_backend_cache",
    "make_local_llm",
    "BackendError",
    "run_wizard",
    "Config",
    "load_config",
    "save_config",
    "kg_context_for_vault",
    "DecisionsStore",
    "StoredDecision",
    "GateDecision",
    "gate_for_cloud",
    "GateAndCaptureResult",
    "gate_and_capture_llm",
    "gate_and_capture_web",
    "BridgeCaptureResult",
    "PolicySnapshot",
    "try_capture_llm",
    "try_capture_web",
    "try_load_policy",
]


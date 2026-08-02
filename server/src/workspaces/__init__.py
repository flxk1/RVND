# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace L0 memory layer.

This package implements the append-only JSONL log keyed by folder and exposes
the workspace memory interface used by the runtime.
"""

# Run namespace back-compat shim before anything else imports module-level constants.
# This copies any legacy ``WORKSPACEVERSUM_*`` env vars to ``WORKSPACE_*`` so
# downstream modules see the modern names on first lookup. Idempotent.
from . import _namespace  # noqa: F401

from .mutation_log import (
    LogEvent,
    MutationLog,
    LOG_ROOT_DEFAULT,
    folder_hash,
)
from .memory import (
    WorkspaceMemory,
    WebResult,
    discover_folders,
    discover_descendants,
    discover_ancestors,
)
from .folder_context import (
    NoFolderContextError,
    UNSCOPED_SENTINEL,
    current_folder,
    folder_context,
    reset_folder,
    resolve_folder_context,
    set_folder,
    with_folder_context,
)
from .inbox_watcher import (
    DefaultExtractor,
    ExtractedFile,
    Extractor,
    INBOX_SUBDIR,
    InboxWatcher,
    ingest_file,
)
from .nd_routing import (
    BaseNDDispatcher,
    Classification,
    Classifier,
    DefaultClassifier,
    DispatchResult,
    NDDispatcher,
    NDRouter,
    RoutingExtractor,
)
from workspaces.adapters.solver.dimensions import (
    COMPOSITION_TABLE,
    DEFAULT_DIMENSION,
    Dimension,
    classify_predicate,
    classify_query_dimension,
    compose,
    compose_weights,
)
from .policy import (
    Acknowledgement,
    CURRENT_DISCLAIMER_VERSION,
    FolderPolicy,
    InvalidPolicy,
    LocalLlmPolicy,
    LOCAL_LLM_MODE_CLOUD_ALLOWED,
    LOCAL_LLM_MODE_CLOUD_FALLBACK,
    LOCAL_LLM_MODE_LOCAL_ONLY,
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD,
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN,
    LOCAL_LLM_ON_INSUFFICIENT_REFUSE,
    OVERSIGHT_DISCLAIMER,
    POLICY_FILENAME,
    LEGACY_POLICY_FILENAME,
    LOCK_DISCLAIMER,
    VALID_LOCAL_LLM_MODES,
    VALID_LOCAL_LLM_ON_INSUFFICIENT,
    disable_discipline,
    disable_oversight,
    disable_lock,
    enable_discipline,
    enable_oversight,
    enable_lock,
    load_policy,
    policy_path,
    save_policy,
)
from .published_policy_pack import (
    ImportedPolicyPack,
    PolicyPackDenied,
    REQUIRED_REVIEWS,
    import_published_policy_pack,
    import_published_policy_pack_into_versum,
)
from .llm_capture import (
    CaptureResult,
    IngestMode,
    LLMExchange,
    OversightLevel,
    VerbosityLevel,
    capture_llm_exchange,
    decide_verbosity,
)
from .web_capture import (
    WebSearchExchange,
    WebSearchResult,
    capture_web_search,
)
from .rule_extractor import (
    RuleFacet,
    extract_rules,
)
from .oversight_extractor import (
    OversightFacet,
    extract_oversight,
)
from .oversight_emit import (
    GroundsBundle,
    DoubtDossier,
    build_grounds_bundle,
    build_dossier,
    needs_dossier,
)
from .breaker import (
    Breaker,
    BreakerState,
    BreakerStatus,
    Lease,
    Tripwire,
    cap_grade,
    default_tripwires,
)
from .attestation.core import (
    AttestationResult,
    GreenChecks,
    Probe,
    attest,
    breaker_metrics,
    green_checks,
    signature,
)
from .lens import (
    Admission,
    AdmissionVerdict,
    LearningObject,
    LearningScope,
    Precedent,
    UpdateBudget,
    classify_admission,
    select_precedent,
)
from .oversight import (
    OversightOutcome,
    assess,
)
from .oversight_compose import (
    ComposedOversight,
    ControlChange,
    compose_facets,
    binds_grade,
    check_separation,
    approves_clean,
)
from .oversight_log import (
    TaintFinding,
    record_admission,
    taint_walk,
    mark_tainted,
)
from .oversight_drift import (
    DriftSignal,
    drift_tripwire,
    raise_floor,
)
from .oversight_drift import evaluate as evaluate_drift
from .oversight_dispatch import (
    DispatchResult,
    dispatch,
    record_decision_return,
)
from .domain_nds import (
    AIActRuleND,
    ContractRuleND,
    GDPRRuleND,
    MusicRightsRuleND,
    OversightND,
    register_default_domain_nds,
)
from .deontic import (
    DeonticFormula,
    DeonticFormulaND,
    detect_conflicts,
    extract_formulae,
    formula_from_rule,
    register_deontic_nd,
)
from .crossref_extractor import (
    CrossReference,
    CrossReferenceExtractor,
    extract_cross_references,
    infer_host_instrument,
    register_crossref_nd,
)
from .decisions.extractor import (
    DecisionExtractor,
    DecisionPoint,
    Option,
    extract_decisions,
    register_decision_nd,
)
from .instrument_obligation_extractor import (
    RequiredArtifact,
    RequiredArtifactExtractor,
    extract_required_artifacts,
    register_required_artifact_nd,
)
from .xml_legal import (
    CrossRef,
    DocumentTree,
    ProvisionNode,
    all_cross_refs,
    document_tree_to_text,
    parse_akoma_ntoso,
    parse_formex,
    parse_legal_xml,
)
from .llm_extract import (
    DomainProfile,
    ExtractionResult,
    extract as llm_extract,
    extract_obligations_hybrid,
)
from .workflows import (
    Workflow,
    WorkflowStep,
    active_workflows,
    define_workflow,
    delete_workflow,
    list_workflows,
    list_workflows_for_folder,
    load_workflow,
    recent_dispatches,
    run_workflow,
)
from .workspace_registry import (
    DEFAULT_WORKSPACE_DIR,
    add_known_workspace,
    bootstrap_default_workspace,
    list_known_workspaces,
    load_registry,
    remove_known_workspace,
)
from .queue import (
    Lease,
    LeaseStolen,
    QueueEntry,
    cancel_run,
    enqueue_run,
    get_run,
    inspect_stuck_runs,
    list_queue,
    mark_done,
    mark_failed,
    mark_run_done,
    mark_run_failed,
    renew_lease,
    resume_run,
    take_next_run,
)
from .worker import (
    WorkerConfig,
    run_forever as worker_run_forever,
    run_once as worker_run_once,
    stop_worker,
    worker_status,
)
from .pinned_skills import (
    PinnedSkill,
    PinnedSkillStore,
    list_pinned,
    load_companion_catalogue,
    load_pinned_skills,
    pin_skill,
    record_dispatch,
    resolve_skills_for_query,
    save_pinned_skills,
    suggest_companions,
    unpin_skill,
)

__all__ = [
    # Mutation log (A1)
    "LogEvent",
    "MutationLog",
    "LOG_ROOT_DEFAULT",
    "folder_hash",
    # WorkspaceMemory (A2)
    "WorkspaceMemory",
    "WebResult",
    "discover_folders",
    "discover_descendants",
    "discover_ancestors",
    # Folder context (A3)
    "current_folder",
    "set_folder",
    "reset_folder",
    "folder_context",
    "with_folder_context",
    "resolve_folder_context",
    "NoFolderContextError",
    "UNSCOPED_SENTINEL",
    # Inbox watcher (B1)
    "InboxWatcher",
    "DefaultExtractor",
    "ExtractedFile",
    "Extractor",
    "ingest_file",
    "INBOX_SUBDIR",
    # ND routing (B2)
    "Classification",
    "Classifier",
    "DefaultClassifier",
    "NDDispatcher",
    "BaseNDDispatcher",
    "NDRouter",
    "DispatchResult",
    "RoutingExtractor",
    # Five-dimensional edge model (Federation adapter)
    "Dimension",
    "DEFAULT_DIMENSION",
    "COMPOSITION_TABLE",
    "compose",
    "compose_weights",
    "classify_predicate",
    "classify_query_dimension",
    # Folder policy (B6)
    "FolderPolicy",
    "Acknowledgement",
    "InvalidPolicy",
    "LocalLlmPolicy",
    "LOCAL_LLM_MODE_CLOUD_ALLOWED",
    "LOCAL_LLM_MODE_CLOUD_FALLBACK",
    "LOCAL_LLM_MODE_LOCAL_ONLY",
    "LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD",
    "LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN",
    "LOCAL_LLM_ON_INSUFFICIENT_REFUSE",
    "VALID_LOCAL_LLM_MODES",
    "VALID_LOCAL_LLM_ON_INSUFFICIENT",
    "POLICY_FILENAME",
    "LEGACY_POLICY_FILENAME",
    "LOCK_DISCLAIMER",
    "OVERSIGHT_DISCLAIMER",
    "CURRENT_DISCLAIMER_VERSION",
    "load_policy",
    "save_policy",
    "policy_path",
    "disable_lock",
    "enable_lock",
    "disable_oversight",
    "enable_oversight",
    "enable_discipline",
    "disable_discipline",
    # Published policy-pack import
    "ImportedPolicyPack",
    "PolicyPackDenied",
    "REQUIRED_REVIEWS",
    "import_published_policy_pack",
    "import_published_policy_pack_into_versum",
    # LLM capture (B4)
    "LLMExchange",
    "IngestMode",
    "OversightLevel",
    "VerbosityLevel",
    "CaptureResult",
    "capture_llm_exchange",
    "decide_verbosity",
    # Web capture (B3)
    "WebSearchExchange",
    "WebSearchResult",
    "capture_web_search",
    # Rule extractor (B2 — operative structure)
    "RuleFacet",
    "extract_rules",
    # Oversight extractor (Oversight ND IN face)
    "OversightFacet",
    "extract_oversight",
    # Oversight emitter (Oversight ND OUT face)
    "GroundsBundle",
    "DoubtDossier",
    "build_grounds_bundle",
    "build_dossier",
    "needs_dossier",
    # The Breaker (USP 3 — interdiction at machine tempo)
    "Breaker",
    "BreakerState",
    "BreakerStatus",
    "Lease",
    "Tripwire",
    "cap_grade",
    "default_tripwires",
    # Behavioral attestation (producer of the Breaker's integrity flag)
    "AttestationResult",
    "GreenChecks",
    "Probe",
    "attest",
    "breaker_metrics",
    "green_checks",
    "signature",
    # The Lens (USP 2 — in vivo oversight of learning)
    "Admission",
    "AdmissionVerdict",
    "LearningObject",
    "LearningScope",
    "Precedent",
    "UpdateBudget",
    "classify_admission",
    "select_precedent",
    # Oversight orchestrator (embedded-engine entry point)
    "OversightOutcome",
    "assess",
    # Fingerprint composition + separation of duties (L4)
    "ComposedOversight",
    "ControlChange",
    "compose_facets",
    "binds_grade",
    "check_separation",
    "approves_clean",
    # Log glue + ground-id taint
    "TaintFinding",
    "record_admission",
    "taint_walk",
    "mark_tainted",
    # Drift → Breaker (L2 evaluator)
    "DriftSignal",
    "drift_tripwire",
    "raise_floor",
    "evaluate_drift",
    # Dispatch-record writer (connector Workspace-side)
    "DispatchResult",
    "dispatch",
    "record_decision_return",
    # Domain NDs (B2 — concrete dispatchers)
    "GDPRRuleND",
    "AIActRuleND",
    "MusicRightsRuleND",
    "ContractRuleND",
    "OversightND",
    "register_default_domain_nds",
    # NotebookLM-grade legal analysis layer
    "DeonticFormula",
    "DeonticFormulaND",
    "extract_formulae",
    "formula_from_rule",
    "detect_conflicts",
    "register_deontic_nd",
    "CrossReference",
    "CrossReferenceExtractor",
    "extract_cross_references",
    "infer_host_instrument",
    "register_crossref_nd",
    "DecisionPoint",
    "Option",
    "DecisionExtractor",
    "extract_decisions",
    "register_decision_nd",
    "RequiredArtifact",
    "RequiredArtifactExtractor",
    "extract_required_artifacts",
    "register_required_artifact_nd",
    # Structure-aware legal XML (Layer-1: Akoma Ntoso / Formex)
    "DocumentTree",
    "ProvisionNode",
    "CrossRef",
    "parse_akoma_ntoso",
    "parse_formex",
    "parse_legal_xml",
    "document_tree_to_text",
    "all_cross_refs",
    # LLM extractor (Workspace operation, Lock-gated)
    "DomainProfile",
    "ExtractionResult",
    "llm_extract",
    "extract_obligations_hybrid",
    # Folder-scoped pinned skills (#145)
    "PinnedSkill",
    "PinnedSkillStore",
    "load_pinned_skills",
    "save_pinned_skills",
    "pin_skill",
    "unpin_skill",
    "list_pinned",
    "resolve_skills_for_query",
    "record_dispatch",
    "load_companion_catalogue",
    "suggest_companions",
    # Workflows
    "Workflow",
    "WorkflowStep",
    "define_workflow",
    "delete_workflow",
    "load_workflow",
    "list_workflows",
    "list_workflows_for_folder",
    "run_workflow",
    "recent_dispatches",
    "active_workflows",
    # Background runner queue
    "Lease",
    "QueueEntry",
    "enqueue_run",
    "list_queue",
    "take_next_run",
    "renew_lease",
    "mark_done",
    "mark_failed",
    "mark_run_done",
    "mark_run_failed",
    "LeaseStolen",
    "cancel_run",
    "get_run",
    "inspect_stuck_runs",
    "resume_run",
    # Background runner worker
    "WorkerConfig",
    "worker_run_forever",
    "worker_run_once",
    "stop_worker",
    "worker_status",
    # Workspace registry (#134, #154)
    "DEFAULT_WORKSPACE_DIR",
    "add_known_workspace",
    "bootstrap_default_workspace",
    "list_known_workspaces",
    "load_registry",
    "remove_known_workspace",
]

from ._version import __version__  # noqa: E402

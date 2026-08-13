# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace CLI implementation — the cmd_* handlers + dispatch tables.

Split from cli.py so that file is the CLI SURFACE (build_parser + main +
arg helpers) and this is the command implementation. main imports _DISPATCH
back. No argparse construction here.
"""

from __future__ import annotations
import argparse
from datetime import datetime, time as datetime_time, timezone
import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Any
from ..folder_context import (
    ALLOW_UNREGISTERED_ENV,
    NoFolderContextError,
    resolve_folder_context,
)
from ..inbox_watcher import InboxWatcher, ingest_file
from ..memory import WorkspaceMemory, discover_folders
from ..mutation_log import LOG_ROOT_DEFAULT, MutationLog
from ..policy import (
    OVERSIGHT_DISCLAIMER,
    LOCK_DISCLAIMER,
    disable_discipline,
    disable_oversight,
    disable_lock,
    enable_discipline,
    enable_oversight,
    enable_lock,
    load_policy,
    policy_path,
    set_oversight_level,
    OVERSIGHT_LEVELS,
)


def _confirm(prompt: str, *, stream: IO[str] | None = None) -> bool:
    """Read a y/N from stdin. Anything other than 'y'/'yes' returns False.

    ``stream`` defaults to ``sys.stdin`` lazily so monkeypatched stdin in tests
    is respected (don't bind ``sys.stdin`` at function-definition time).
    """
    if stream is None:
        stream = sys.stdin
    print(prompt, end=" ", flush=True)
    try:
        line = stream.readline()
    except (OSError, KeyboardInterrupt):
        return False
    return line.strip().lower() in ("y", "yes")

def cmd_list(args: argparse.Namespace) -> int:
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pairs = mem.all_pairs()
    if args.state:
        # Filter on top of all_pairs (which already excludes deleted/purged/rejected).
        # If user wants those, they'd need a separate flag.
        pass

    if args.limit and args.limit > 0:
        pairs = pairs[: args.limit]

    if not pairs:
        print(f"(no pairs in scope of {mem.folder_context})")
        return 0

    for p in pairs:
        pid = p.get("id", "<no-id>")
        problem = p.get("problem", {})
        summary = problem.get("summary", "")[:80]
        scope = problem.get("scope", "")
        problem_type = problem.get("type", "")
        print(f"{pid}  [{scope}/{problem_type}]  {summary}")

    return 0

def cmd_show(args: argparse.Namespace) -> int:
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pair = mem.by_id(args.pair_id)
    if pair is None:
        print(f"error: pair '{args.pair_id}' not found in scope of {mem.folder_context}",
              file=sys.stderr)
        return 1

    print(json.dumps(pair, indent=2, ensure_ascii=False))
    return 0

def cmd_delete(args: argparse.Namespace) -> int:
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pair = mem.by_id(args.pair_id)
    if pair is None:
        print(f"error: pair '{args.pair_id}' not found in scope", file=sys.stderr)
        return 1

    summary = pair.get("problem", {}).get("summary", "")[:80]
    print(f"About to logically DELETE: {args.pair_id}")
    print(f"  summary: {summary}")
    print(f"  folder:  {mem.folder_context}")
    print(f"  (recoverable via the mutation log audit trail)")

    if not args.yes and not _confirm("Proceed? [y/N]:"):
        print("aborted.")
        return 2

    ok = mem.delete(args.pair_id)
    if not ok:
        print(f"error: pair not found at delete time (race?)", file=sys.stderr)
        return 1
    print(f"deleted {args.pair_id}")
    return 0

def cmd_delete_document(args: argparse.Namespace) -> int:
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Count affected first (dry-run) so the user sees the blast radius.
    affected = 0
    for p in mem.all_pairs():
        problem = p.get("problem", {})
        if isinstance(problem, dict) and problem.get("source_document") == args.document_path:
            affected += 1

    if affected == 0:
        print(f"no pairs derived from {args.document_path} in scope of {mem.folder_context}")
        return 1

    print(f"About to logically DELETE {affected} pair(s) derived from:")
    print(f"  document: {args.document_path}")
    print(f"  folder:   {mem.folder_context}")
    print(f"  (recoverable via the mutation log audit trail)")

    if not args.yes and not _confirm("Proceed? [y/N]:"):
        print("aborted.")
        return 2

    n = mem.delete_document(args.document_path)
    print(f"deleted {n} pair(s)")
    return 0

def cmd_purge(args: argparse.Namespace) -> int:
    # 0.6.8 (B1): purge now requires legal_basis + requester_ref + reason.
    # All three are recorded in the on-chain tombstone so the audit trail
    # explains who authorised the erasure and on what legal ground.
    if not getattr(args, "legal_basis", "") or not getattr(args, "requester_ref", "") \
            or not getattr(args, "reason", ""):
        print(
            "error: purge requires --legal-basis, --requester-ref, and "
            "--reason. See `workspaces purge --help` for valid legal bases "
            "(GDPR Art. 17(1)(a-f)).",
            file=sys.stderr,
        )
        return 3

    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pair = mem.by_id(args.pair_id)
    preview_summary = pair.get("problem", {}).get("summary", "")[:80] if pair else "<unknown>"

    print(f"About to PHYSICALLY PURGE pair '{args.pair_id}'.")
    print(f"  summary:       {preview_summary}")
    print(f"  folder:        {mem.folder_context}")
    print(f"  legal basis:   {args.legal_basis}")
    print(f"  requester ref: {args.requester_ref}")
    print(f"  reason:        {args.reason}")
    print(f"  *** THIS IS IRREVERSIBLE. ***")
    print(f"  A tombstone will be written; surviving events will be re-linked + re-signed.")
    print(f"  Use this only for GDPR Art. 17 erasure-from-everything requests.")

    if not args.yes_i_mean_it:
        if not _confirm("Type 'PURGE' to confirm, or anything else to abort:"):
            # _confirm only matches y/yes. The user typed something else.
            print("aborted.")
            return 2
        # If they typed yes/y, that's NOT enough for purge. Demand the word.
        print("error: purge requires explicit confirmation. Type 'PURGE' or pass --yes-i-mean-it.",
              file=sys.stderr)
        return 2

    try:
        n = mem.purge_pair(
            args.pair_id,
            legal_basis=args.legal_basis,
            requester_ref=args.requester_ref,
            reason=args.reason,
        )
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if n == 0:
        print(f"error: pair '{args.pair_id}' not found", file=sys.stderr)
        return 1
    print(f"purged {n} event(s) referencing {args.pair_id} (tombstone written)")
    return 0

def cmd_purge_document(args: argparse.Namespace) -> int:
    # 0.6.8 (B1): same required legal-basis triple as cmd_purge.
    if not getattr(args, "legal_basis", "") or not getattr(args, "requester_ref", "") \
            or not getattr(args, "reason", ""):
        print(
            "error: purge-document requires --legal-basis, --requester-ref, "
            "and --reason.",
            file=sys.stderr,
        )
        return 3

    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Count affected first.
    affected_pairs: set[str] = set()
    for p in mem.all_pairs():
        problem = p.get("problem", {})
        if isinstance(problem, dict) and problem.get("source_document") == args.document_path:
            affected_pairs.add(p.get("id", ""))

    if not affected_pairs:
        print(f"no pairs derived from {args.document_path} in scope of {mem.folder_context}")
        return 1

    print(f"About to PHYSICALLY PURGE {len(affected_pairs)} pair(s) derived from:")
    print(f"  document:      {args.document_path}")
    print(f"  folder:        {mem.folder_context}")
    print(f"  legal basis:   {args.legal_basis}")
    print(f"  requester ref: {args.requester_ref}")
    print(f"  reason:        {args.reason}")
    print(f"  *** THIS IS IRREVERSIBLE. ***")
    print(f"  One tombstone written per pair; surviving events re-linked + re-signed.")
    print(f"  Use this only for GDPR Art. 17 erasure-from-everything requests.")

    if not args.yes_i_mean_it:
        print("error: purge-document requires --yes-i-mean-it.", file=sys.stderr)
        return 2

    try:
        n = mem.purge_document(
            args.document_path,
            legal_basis=args.legal_basis,
            requester_ref=args.requester_ref,
            reason=args.reason,
        )
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(f"purged {n} event(s) across {len(affected_pairs)} pair(s) (tombstones written)")
    return 0

def cmd_audit_tail(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    log = MutationLog(folder, log_root=_log_root(args))
    events = list(log.replay())
    if args.limit and args.limit > 0:
        events = events[-args.limit:]

    if not events:
        print(f"(no events in {folder})")
        return 0

    for evt in events:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(evt.ts))
        print(
            f"{ts}  {evt.event:9}  {evt.lifecycle_state or '-':10}  "
            f"{evt.channel:10}  {evt.pair_id}  actor={evt.actor}"
        )
    return 0

def cmd_folders(args: argparse.Namespace) -> int:
    log_root = _log_root(args)
    folders = discover_folders(log_root=log_root)
    if not folders:
        print(f"(no folders have logs under {log_root})")
        return 0
    for fp in sorted(folders):
        print(fp)
    return 0


def _licence_date(value: str, *, end: bool = False) -> float | None:
    if not value:
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc
    boundary = datetime_time.max if end else datetime_time.min
    return datetime.combine(day, boundary, tzinfo=timezone.utc).timestamp()


def cmd_licence(args: argparse.Namespace) -> int:
    if args.licence_command != "usage":
        print(f"unknown licence command: {args.licence_command}", file=sys.stderr)
        return 3
    from ..licence_usage import capacity_report
    try:
        report = capacity_report(
            log_root=_log_root(args),
            from_epoch=_licence_date(args.from_date),
            to_epoch=_licence_date(args.to_date, end=True),
            licensed_capacity=args.capacity,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        capacity = report.get("licensed_capacity")
        print(f"Declared capacity:       {capacity if capacity is not None else 'not supplied'}")
        print(f"Current enabled agents:  {report['current_enabled_agents']}")
        print(f"Peak enabled agents:     {report['peak_enabled_agents']}")
        print(f"Workspaces:              {report['workspace_count']}")
        print(f"Audit chains:            {'verified' if report['verified'] else 'incomplete or invalid'}")
        print(f"Identity basis:          {report['identity_basis']}")
        if "within_capacity" in report:
            print(f"Within capacity:         {'yes' if report['within_capacity'] else 'no'}")
    return 0 if report["verified"] else 2

def cmd_watch(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Build the list of folders to watch. If --recursive, walk sub-folders and
    # process each as ITS OWN workspace (asymmetric hierarchical rule).
    folders_to_watch = [folder]
    if getattr(args, "recursive", False):
        from ..inbox_watcher import RECURSIVE_SKIP_DIRS
        from pathlib import Path as _Path
        for child_root, dirs, _ in os.walk(folder):
            # Filter sub-dirs in place so os.walk skips them.
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d not in RECURSIVE_SKIP_DIRS]
            for d in dirs:
                folders_to_watch.append(str(_Path(child_root) / d))

    watcher = InboxWatcher(folder, log_root=_log_root(args))
    if watcher.uses_inbox:
        print(f"watching: {watcher.inbox_path} (Inbox/ subfolder)")
    else:
        scope = "recursive — each sub-folder is its own workspace" if len(folders_to_watch) > 1 else "root"
        print(f"watching: {watcher.folder_context} ({scope}; skipping project metadata)")

    if args.once:
        new_ids = watcher.run_once()
        if new_ids:
            print(f"ingested {len(new_ids)} new pair(s):")
            for pid in new_ids:
                print(f"  {pid}")
        else:
            print("(no new files to ingest)")
        return 0

    # Loop mode.
    def report(new_ids: list[str]) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"[{ts}] ingested {len(new_ids)} new pair(s):")
        for pid in new_ids:
            print(f"  {pid}")

    print(f"polling every {args.interval}s — Ctrl+C to stop.")
    try:
        watcher.run_forever(poll_interval=args.interval, on_ingested=report)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0

def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    try:
        # Use the full extractor stack (FormatAwareExtractor + ND routing) so the
        # CLI extracts real document text and fans out to NDs — matching the MCP
        # ingest_path / scan_folder tools. Without this the CLI fell back to the
        # metadata-only DefaultExtractor, storing filename stubs instead of content.
        from ..nd_routing import make_full_extractor
        pair_ids = ingest_file(
            args.file_path, folder, log_root=_log_root(args),
            extractor=make_full_extractor(),
        )
        from ..ingest.versum import ingest_into_versum
        graph_ingest = ingest_into_versum(args.file_path, folder)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not pair_ids:
        print(f"(already ingested — no new pair created)")
        return 0

    print(f"ingested {len(pair_ids)} new pair(s):")
    for pid in pair_ids:
        print(f"  {pid}")
    receipt = graph_ingest.get("write", graph_ingest)
    print(f"versum: {receipt.get('status', 'unknown')}")
    return 0

def _log_root(args: argparse.Namespace) -> Path:
    return Path(args.log_root) if args.log_root else LOG_ROOT_DEFAULT

def cmd_policy_show(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pol = load_policy(folder)
    print(f"folder: {folder}")
    print(f"policy file: {policy_path(folder)}")
    print(f"  privacy_lock_enabled:   {pol.privacy_lock_enabled}")
    print(f"  lock_is_active:         {pol.lock_is_active}")
    print(f"  oversight_enabled:        {pol.oversight_enabled}")
    print(f"  oversight_is_active:      {pol.oversight_is_active}")
    print(f"  oversight_default_level:  {pol.oversight_default_level}")
    print(f"  discipline_enabled:       {pol.discipline_enabled}")
    print(f"  discipline_is_active:     {pol.discipline_is_active}")
    if pol.discipline_manifest:
        print(f"  discipline_manifest:      {pol.discipline_manifest}")
    if pol.acknowledgements:
        print(f"  acknowledgements:")
        for k, ack in pol.acknowledgements.items():
            print(f"    {k}:")
            print(f"      accepted_at:         {ack.accepted_at}")
            print(f"      accepted_by:         {ack.accepted_by}")
            print(f"      disclaimer_version:  {ack.disclaimer_version}")
            if ack.reason:
                print(f"      reason:              {ack.reason}")
    return 0

def cmd_policy_disable_lock(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    if not args.i_accept_the_risk:
        print(LOCK_DISCLAIMER)
        print()
        print("To proceed, re-run with --i-accept-the-risk.")
        print(f"  workspaces policy disable-lock --folder {folder} "
              f"--i-accept-the-risk --reason 'why'")
        return 2

    disable_lock(folder, accepted_by=args.accepted_by,
                   reason=args.reason or "",
                   log_root=_log_root(args))
    print(f"Privacy Lock DISABLED for {folder}")
    print(f"  audit-logged. Re-enable with: workspaces policy enable-lock --folder {folder}")
    return 0

def cmd_policy_enable_lock(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    enable_lock(folder, actor=args.actor or "user",
                  log_root=_log_root(args))
    print(f"Privacy Lock ENABLED for {folder}")
    return 0

def cmd_policy_disable_oversight(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    if not args.i_accept_the_risk:
        print(OVERSIGHT_DISCLAIMER)
        print()
        print("To proceed, re-run with --i-accept-the-risk.")
        return 2

    disable_oversight(folder, accepted_by=args.accepted_by,
                      reason=args.reason or "",
                      log_root=_log_root(args))
    print(f"Oversight DISABLED for {folder}")
    print(f"  audit-logged. Re-enable with: workspaces policy enable-oversight --folder {folder}")
    return 0

def cmd_policy_enable_oversight(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    enable_oversight(folder, actor=args.actor or "user",
                     log_root=_log_root(args))
    print(f"Oversight ENABLED for {folder}")
    return 0

def cmd_publish(args: argparse.Namespace) -> int:
    """Re-emit an existing private pair as DISTRIBUTED to descendants (B5)."""
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    pair = mem.by_id(args.pair_id)
    if pair is None:
        print(f"error: pair '{args.pair_id}' not found in scope", file=sys.stderr)
        return 1

    summary = pair.get("problem", {}).get("summary", "")[:80]
    print(f"About to PUBLISH pair {args.pair_id} downward to descendants of:")
    print(f"  folder:  {mem.folder_context}")
    print(f"  summary: {summary}")
    print(f"  Descendants will see this pair via their own WorkspaceMemory views.")

    if not args.yes and not _confirm("Proceed? [y/N]:"):
        print("aborted.")
        return 2

    mem.publish(pair, scope="descendants")
    print(f"published {args.pair_id} to descendants of {mem.folder_context}")
    return 0

def cmd_unpublish(args: argparse.Namespace) -> int:
    """Revoke a published pair from descendants (B5)."""
    try:
        mem = WorkspaceMemory(args.folder, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    print(f"About to UNPUBLISH pair {args.pair_id} from descendants of:")
    print(f"  folder: {mem.folder_context}")
    print(f"  (audit trail of the original publish survives)")

    if not args.yes and not _confirm("Proceed? [y/N]:"):
        print("aborted.")
        return 2

    ok = mem.unpublish(args.pair_id)
    if not ok:
        print(f"error: no published pair {args.pair_id} found in scope of "
              f"{mem.folder_context}", file=sys.stderr)
        return 1
    print(f"unpublished {args.pair_id}")
    return 0

def cmd_policy_enable_discipline(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    enable_discipline(folder, manifest=args.manifest or "",
                      actor=args.actor or "user", log_root=_log_root(args))
    print(f"Discipline ENABLED for {folder}")
    if args.manifest:
        print(f"  manifest: {args.manifest}")
    print(f"  run it with: workspaces discipline audit --folder {folder}")
    return 0

def cmd_policy_disable_discipline(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    disable_discipline(folder, actor=args.actor or "user",
                       log_root=_log_root(args))
    print(f"Discipline DISABLED for {folder}")
    return 0

_POLICY_DISPATCH = {
    "show": cmd_policy_show,
    "disable-lock": cmd_policy_disable_lock,
    "enable-lock": cmd_policy_enable_lock,
    "disable-oversight": cmd_policy_disable_oversight,
    "enable-oversight": cmd_policy_enable_oversight,
    "enable-discipline": cmd_policy_enable_discipline,
    "disable-discipline": cmd_policy_disable_discipline,
}

def cmd_policy(args: argparse.Namespace) -> int:
    handler = _POLICY_DISPATCH.get(args.policy_command)
    if handler is None:
        print(f"error: unknown policy subcommand: {args.policy_command}",
              file=sys.stderr)
        return 3
    return handler(args)

def cmd_pin(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Interactive mode: browse the catalogue + multi-select.
    if args.interactive:
        return _cmd_pin_interactive(folder, args)

    # Direct mode: skill_id required.
    if not args.skill_id:
        print("error: skill_id is required (or use --interactive)", file=sys.stderr)
        return 2

    from ..pinned_skills import pin_skill
    try:
        store = pin_skill(
            folder, args.skill_id,
            pinned_by=args.by, note=args.note,
            log_root=_log_root(args),
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"pinned {args.skill_id} to {folder}")
    print(f"  total pinned on this folder: {len(store.skills)}")
    return 0

def _cmd_pin_interactive(folder: Path, args: argparse.Namespace) -> int:
    """Browse the skill catalogue, let user multi-select, pin each pick.

    Terminal-native (no TUI library): numbered list, comma-separated input,
    optional `all` keyword for an entire family.
    """
    from ..pinned_skills import discover_pinnable_families, pin_skill, list_pinned

    # Live source: skills the host (Claude Code / Codex) has actually installed,
    # unioned with any static catalogue that ships. Nothing installed → nothing
    # to pin, so point the user at the install path instead of a dead catalogue.
    families = discover_pinnable_families()
    if not families:
        print("No installed skills found. Install a marketplace plugin first "
              "(e.g. ./scripts/connect-agent-hub.sh), then re-run — or pin one "
              "directly:", file=sys.stderr)
        print("  workspaces pin --folder <folder> <plugin>:<skill>", file=sys.stderr)
        return 2

    # Build the flat list of (index, plugin, skill_id, label) for selection.
    filt = (args.filter or "").lower()
    flat: list[tuple[int, str, str, str]] = []
    grouped: dict[str, list[tuple[int, str]]] = {}
    already_pinned = {s.id for s in list_pinned(folder, log_root=_log_root(args))}

    idx = 1
    for plugin, info in sorted(families.items()):
        if not isinstance(info, dict):
            continue
        label = info.get("label", plugin)
        skills = info.get("skills", []) or []
        rows: list[tuple[int, str]] = []
        for skill_id in skills:
            if filt and filt not in plugin.lower() and filt not in skill_id.lower():
                continue
            rows.append((idx, skill_id))
            flat.append((idx, plugin, skill_id, label))
            idx += 1
        if rows:
            grouped[plugin] = rows

    if not flat:
        print(f"No skills matched filter {args.filter!r}.", file=sys.stderr)
        return 2

    # Render the catalogue.
    print(f"\nFolder: {folder}\n")
    print(f"Currently pinned ({len(already_pinned)}): "
          + (", ".join(sorted(already_pinned)) if already_pinned else "(none)"))
    print()
    print(f"Available skills ({len(flat)} total"
          + (f", filter: {args.filter!r}" if args.filter else "") + "):\n")
    for plugin, rows in grouped.items():
        label = families[plugin].get("label", plugin)
        print(f"  [{plugin}] {label}")
        for n, sid in rows:
            marker = "★" if sid in already_pinned else " "
            print(f"    {marker} {n:3}. {sid}")
        print()

    # Prompt for selection.
    print("Pick: numbers separated by commas (e.g. 3,7,12),")
    print("      'all' for everything, or 'all:<plugin>' for a whole family,")
    print("      or 'q' / empty to cancel.")
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled.", file=sys.stderr)
        return 2

    if not choice or choice.lower() == "q":
        print("cancelled.")
        return 0

    # Parse selection.
    to_pin: list[str] = []
    for token in (t.strip() for t in choice.split(",")):
        if not token:
            continue
        if token.lower() == "all":
            for rows in grouped.values():
                for _, sid in rows:
                    to_pin.append(sid)
        elif token.startswith("all:"):
            plugin = token[4:]
            if plugin not in grouped:
                print(f"  ! 'all:{plugin}' — no such plugin in catalogue, skipping",
                      file=sys.stderr)
                continue
            for _, sid in grouped[plugin]:
                to_pin.append(sid)
        else:
            try:
                n = int(token)
            except ValueError:
                print(f"  ! {token!r} is not a number or 'all:<plugin>', skipping",
                      file=sys.stderr)
                continue
            match = next((row for row in flat if row[0] == n), None)
            if match is None:
                print(f"  ! {n} is out of range, skipping", file=sys.stderr)
                continue
            to_pin.append(match[2])

    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for sid in to_pin:
        if sid in seen:
            continue
        seen.add(sid)
        deduped.append(sid)
    to_pin = deduped

    if not to_pin:
        print("No valid picks. Nothing pinned.", file=sys.stderr)
        return 2

    # Commit the pins.
    print()
    pinned_count = 0
    skipped_count = 0
    for sid in to_pin:
        if sid in already_pinned:
            print(f"  · {sid} (already pinned, skipping)")
            skipped_count += 1
            continue
        try:
            pin_skill(folder, sid, pinned_by=args.by, note=args.note,
                       log_root=_log_root(args))
            print(f"  ✓ pinned {sid}")
            pinned_count += 1
        except ValueError as e:
            print(f"  ✗ {sid}: {e}", file=sys.stderr)

    print(f"\n{pinned_count} pinned, {skipped_count} skipped (already pinned).")
    return 0

def cmd_unpin(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..pinned_skills import unpin_skill
    store, removed = unpin_skill(folder, args.skill_id, log_root=_log_root(args))
    if removed:
        print(f"unpinned {args.skill_id} from {folder}")
    else:
        print(f"{args.skill_id} was not pinned on {folder}")
    print(f"  total pinned on this folder: {len(store.skills)}")
    return 0

def cmd_list_pins(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..pinned_skills import list_pinned
    pinned = list_pinned(folder, log_root=_log_root(args))
    if not pinned:
        print(f"no skills pinned to {folder}")
        return 0
    print(f"pinned skills on {folder} ({len(pinned)}):")
    for s in pinned:
        note = f"  — {s.note}" if s.note else ""
        print(f"  {s.id}  [by {s.pinned_by} at {s.pinned_at}]{note}")
    return 0

def cmd_resolve_skills(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..pinned_skills import resolve_skills_for_query
    out = resolve_skills_for_query(
        folder, args.query,
        log_root=_log_root(args),
        include_ancestors=not args.no_ancestors,
    )
    skills = out["skills"]
    if not skills:
        if args.query:
            print(f"no pinned skills on {folder} or ancestors match '{args.query}'")
        else:
            print(f"no pinned skills on {folder} or its ancestors")
        return 0
    print(f"resolved {len(skills)} pinned skill(s) for {folder}"
          + (f" (query='{args.query}')" if args.query else "")
          + ":")
    for s in skills:
        prov = f"  [inherited from {s['inherited_from']}]" if s["inherited_from"] else "  [own]"
        note = f"  — {s['note']}" if s["note"] else ""
        print(f"  {s['id']}{prov}{note}")
    if not args.no_ancestors:
        print(f"  chain walked: {len(out['chain'])} ancestor(s) inspected")
    return 0

def cmd_run_worker(args: argparse.Namespace) -> int:
    import logging as _logging
    _logging.basicConfig(
        level=_logging.DEBUG if args.verbose else _logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from ..worker import (
        WorkerConfig,
        run_forever,
        run_once,
        worker_status,
    )
    log_root = _log_root(args)
    if args.status:
        snap = worker_status(log_root=log_root)
        print(json.dumps(snap, indent=2, sort_keys=True))
        return 0
    cfg = WorkerConfig(
        worker_id=args.worker_id or "",
        lease_seconds=int(args.lease_seconds),
        interval_seconds=float(args.interval),
        log_root=log_root,
        once=bool(args.once),
        max_iterations=int(args.max_iterations),
        verbose=bool(args.verbose),
    )
    if args.once:
        out = run_once(cfg)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("state") != "failed" else 4
    summary = run_forever(cfg)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0

def _seal_passphrase(args: argparse.Namespace, *, confirm: bool) -> str:
    import os as _os
    import getpass
    pw = getattr(args, "passphrase", None) or _os.environ.get("WORKSPACE_SEAL_PASSPHRASE")
    if pw:
        return pw
    pw = getpass.getpass("Passphrase: ")
    if confirm and pw != getpass.getpass("Confirm passphrase: "):
        raise SystemExit("passphrases do not match")
    return pw

def cmd_seal(args: argparse.Namespace) -> int:
    from ..seal import SealError, seal_folder
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        pw = _seal_passphrase(args, confirm=True)
        out = seal_folder(folder, passphrase=pw, log_root=_log_root(args))
    except SealError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"sealed {out['files_sealed']} file(s) → {out['path']}")
    print("Keep the passphrase safe — sealed memory cannot be recovered without it.")
    return 0

def cmd_unseal(args: argparse.Namespace) -> int:
    from ..seal import SealError, unseal_folder
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        pw = _seal_passphrase(args, confirm=False)
        out = unseal_folder(folder, passphrase=pw, log_root=_log_root(args))
    except SealError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"unsealed {out['files_restored']} file(s).")
    return 0

def cmd_status(args: argparse.Namespace) -> int:
    """Folder-aware overview. The 'what's happening here' entry point."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    from ..mutation_log import MutationLog
    from ..policy import load_policy
    from ..pinned_skills import list_pinned

    # Gather
    log = MutationLog(folder, log_root=_log_root(args))
    chain = log.verify_chain()
    try:
        policy = load_policy(folder)
    except Exception:
        policy = None
    try:
        pinned = list_pinned(folder, log_root=_log_root(args))
    except Exception:
        pinned = []
    events = list(log.replay())
    recent = events[-args.events:] if events else []

    def _skill_id(p: Any) -> str:
        if isinstance(p, dict):
            return p.get("skill_id", str(p))
        return getattr(p, "skill_id", str(p))

    def _policy_to_dict(pol: Any) -> Any:
        if pol is None:
            return None
        if hasattr(pol, "__dict__"):
            return {k: v for k, v in pol.__dict__.items()
                    if not k.startswith("_")}
        return pol

    # Host + key fingerprints (0.6.8 B4). Best-effort: any failure surfaces
    # as the literal "(unavailable)" rather than killing the status command.
    try:
        from .. import signing
        host_id = signing._host_id()
    except Exception:
        host_id = "(unavailable)"
    try:
        from .. import signing
        identity_fp = signing.public_key_fingerprint()
    except Exception:
        identity_fp = "(unavailable)"
    try:
        from .. import signing
        controller_fp = signing.public_controller_key_fingerprint() or "(none)"
    except Exception:
        controller_fp = "(unavailable)"

    # B6.3 (0.6.8): surface the active symlink mode so users see whether
    # they're in default (follow → merge symlink-linked paths into one
    # workspace) or isolate (each symlink path is its own workspace).
    try:
        from ..folder_context import symlink_mode
        sym_mode = symlink_mode()
    except Exception:
        sym_mode = "follow"

    snap = {
        "folder": str(folder),
        "folder_hash": log.folder_id,
        "host_id": host_id,
        "identity_pubkey_fingerprint": identity_fp,
        "controller_pubkey_fingerprint": controller_fp,
        "symlink_mode": sym_mode,
        "policy": _policy_to_dict(policy),
        "pinned_skills": [_skill_id(p) for p in pinned],
        "chain": {
            "ok": chain.ok,
            "total_events": chain.total_events,
            "legacy_events": chain.legacy_events,
            "unsigned_events": chain.unsigned_events,
            "broken_links": len(chain.broken_links),
            "signature_failures": len(chain.signature_failures),
            "malformed_lines": chain.malformed_lines,
            "purged_with_tombstone": getattr(chain, "purged_with_tombstone", 0),
            "key_pin": getattr(chain, "key_pin", None),
        },
        "recent_events": [
            {"event": e.event, "pair_id": e.pair_id, "actor": e.actor,
             "ts": e.ts, "audit_id": e.audit_id[:12]}
            for e in recent
        ],
    }

    if args.json:
        print(json.dumps(snap, indent=2, sort_keys=True, default=str))
        return 0

    # Human-readable
    print(f"Folder:        {snap['folder']}")
    print(f"Folder hash:   {snap['folder_hash']}")
    print(f"Host id:       {snap['host_id']}")
    print(f"Identity key:  {snap['identity_pubkey_fingerprint']}")
    print(f"Controller:    {snap['controller_pubkey_fingerprint']}")
    print(f"Symlink mode:  {snap['symlink_mode']}  (WORKSPACE_SYMLINK_MODE)")
    print()
    if snap["policy"]:
        print("Policy:")
        for k, v in snap["policy"].items():
            print(f"  {k}: {v}")
    else:
        print("Policy:        (none — default behaviour)")
    print()
    print(f"Pinned skills ({len(snap['pinned_skills'])}):")
    for s in snap["pinned_skills"]:
        print(f"  - {s}")
    if not snap["pinned_skills"]:
        print("  (none pinned at this folder — check ancestors with `resolve-skills`)")
    print()
    c = snap["chain"]
    chain_glyph = "✓" if c["ok"] else "✗"
    print(f"Audit chain:   {chain_glyph} ok={c['ok']}  events={c['total_events']}  "
          f"legacy={c['legacy_events']}  unsigned={c['unsigned_events']}  "
          f"purged_with_tombstone={c['purged_with_tombstone']}")
    if not c["ok"]:
        print(f"               broken_links={c['broken_links']}  "
              f"sig_failures={c['signature_failures']}  "
              f"malformed={c['malformed_lines']}")
    print()
    if snap["recent_events"]:
        print(f"Recent events ({len(snap['recent_events'])} of {c['total_events']}):")
        for e in snap["recent_events"]:
            print(f"  {e['event']:12}  pair={e['pair_id'][:24]:24}  "
                  f"actor={e['actor']:16}  audit={e['audit_id']}")
    else:
        print("Recent events: (none)")
    return 0

def cmd_tools(args: argparse.Namespace) -> int:
    """List MCP tools grouped by domain prefix. Discoverability for the surface."""
    # Source of truth: the hand-maintained _DECLARED_TOOLS in mcp_server.
    try:
        from ..mcp_server import _DECLARED_TOOLS
    except Exception as e:
        print(f"ERROR: cannot import MCP server: {e}", file=sys.stderr)
        return 2

    # --describe mode: print one tool's docstring
    if args.describe:
        try:
            import workspaces.mcp_server as ms
            fn = getattr(ms, args.describe, None)
            if fn is None or not callable(fn):
                print(f"ERROR: tool '{args.describe}' not found in workspaces-mcp",
                      file=sys.stderr)
                return 2
            print(f"# {args.describe}\n")
            print(fn.__doc__ or "(no docstring)")
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    # Group by domain prefix (everything before the first underscore, with
    # a few cleanup buckets for unprefixed legacy tools).
    groups: dict[str, list[str]] = {}
    for name in _DECLARED_TOOLS:
        if args.filter and args.filter.lower() not in name.lower():
            continue
        # Pick the prefix
        if "_" in name:
            prefix = name.split("_", 1)[0]
        else:
            prefix = "(unprefixed)"
        # Bucket commonly-grouped prefixes
        if prefix in ("by", "fetch", "list", "create", "scan", "reextract",
                       "ingest", "write", "recent", "search"):
            prefix = "(unprefixed-legacy)"
        groups.setdefault(prefix, []).append(name)

    total = sum(len(v) for v in groups.values())
    print(f"Workspace MCP tools: {total} total"
          + (f"  (filter: {args.filter!r})" if args.filter else "")
          + "\n")
    for prefix in sorted(groups.keys()):
        tools = sorted(groups[prefix])
        print(f"  {prefix}* ({len(tools)})")
        for t in tools:
            print(f"    - {t}")
        print()
    print("Tip: `workspaces tools --describe <tool_name>` prints the tool's full docstring.")
    return 0

def cmd_mirror_generate(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..mirrors import generate_lock_mirror
    try:
        rec = generate_lock_mirror(
            folder, args.source_path,
            log_root=_log_root(args),
            actor=args.actor,
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"lock mirror generated:")
    print(f"  source:    {rec.source_path}")
    print(f"  mirror:    {rec.mirror_path}")
    print(f"  spans:     {rec.spans_path} ({rec.span_count} span(s))")
    print(f"  audit_id:  {rec.audit_id}")
    return 0

def cmd_mirror_approve(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..mirrors import approve_lock_mirror
    try:
        rec = approve_lock_mirror(
            folder, args.mirror_path, args.approver,
            log_root=_log_root(args),
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(f"oversight mirror written:")
    print(f"  source:    {rec.source_path}")
    print(f"  mirror:    {rec.mirror_path}")
    print(f"  spans:     {rec.spans_path}")
    print(f"  approver:  {args.approver}")
    print(f"  audit_id:  {rec.audit_id}")
    return 0

def cmd_mirror_list(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..mirrors import list_mirrors
    records = list_mirrors(folder, kind=args.kind or "")
    if not records:
        print(f"(no mirrors in {folder}/mirrors/)")
        return 0
    for r in records:
        print(f"[{r.kind}]  {r.mirror_path}")
        print(f"         source: {r.source_path}")
        print(f"         spans:  {r.span_count}")
    return 0

def cmd_mirror_edit(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    from .. import mirror_editor
    try:
        rd = mirror_editor.open_revision(
            folder, args.mirror_path,
            actor=args.actor, log_root=_log_root(args),
        )
    except (FileNotFoundError, mirror_editor.LockHeldError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    print(f"opened revision draft:")
    print(f"  draft:     {rd.draft_path}")
    print(f"  spans:     {rd.spans_path} ({len(rd.spans)} span(s))")
    print(f"  revision:  {rd.revision}")
    print(f"  audit_id:  {rd.audit_id}")
    print(f"  lock:      {rd.lock_holder}")
    return 0

def cmd_mirror_revisions(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    from .. import mirror_editor
    revs = mirror_editor.revisions_list(
        folder, args.mirror_path, log_root=_log_root(args),
    )
    if not revs:
        print("(no revisions)"); return 0
    for r in revs:
        print(f"  r{r.revision:<3}  {r.operation:<22}  span={r.span_id:<20}  "
              f"actor={r.actor:<20}  audit={r.audit_id[:12]}")
    return 0

def cmd_mirror_diff(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    from .. import mirror_editor
    out = mirror_editor.revisions_diff(
        folder, args.mirror_path, args.from_rev, args.to_rev,
        log_root=_log_root(args),
    )
    sys.stdout.write(out or "(no differences)\n")
    return 0

def cmd_mirror_discard(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    from .. import mirror_editor
    audit_id = mirror_editor.discard_revision(
        folder, args.mirror_path,
        actor=args.actor, reason=args.reason,
        log_root=_log_root(args),
    )
    print(f"discarded; audit_id={audit_id}")
    return 0

_MIRROR_DISPATCH = {
    "generate":  cmd_mirror_generate,
    "approve":   cmd_mirror_approve,
    "list":      cmd_mirror_list,
    "edit":      cmd_mirror_edit,
    "revisions": cmd_mirror_revisions,
    "diff":      cmd_mirror_diff,
    "discard":   cmd_mirror_discard,
}

def cmd_mirror(args: argparse.Namespace) -> int:
    handler = _MIRROR_DISPATCH.get(args.mirror_command)
    if handler is None:
        print(f"error: unknown mirror subcommand: {args.mirror_command}",
              file=sys.stderr)
        return 3
    return handler(args)

def cmd_keys_init_controller(args: argparse.Namespace) -> int:
    """Initialise the controller co-signing keypair (D2). Idempotent.

    The controller key is workspace-scoped (NOT per-host) — the same legal
    controller signs from every host they touch. Generated under the key
    root (override via WORKSPACE_KEY_DIR) at ``controller.{priv,pub}``.
    """
    from .. import signing
    already_existed = signing._controller_private_key_path().exists()
    signing.ensure_controller_keypair()
    fp = signing.public_controller_key_fingerprint()
    state = "already initialised" if already_existed else "initialised"
    print(f"controller keypair {state}")
    print(f"  path:        {signing._controller_private_key_path()}")
    print(f"  fingerprint: {fp}")
    return 0

def cmd_keys(args: argparse.Namespace) -> int:
    handler = _KEYS_DISPATCH.get(args.keys_command)
    if handler is None:
        print(f"error: unknown keys subcommand: {args.keys_command}",
              file=sys.stderr)
        return 3
    return handler(args)

_KEYS_DISPATCH = {
    "init-controller": cmd_keys_init_controller,
}

def cmd_erase(args: argparse.Namespace) -> int:
    """Run the erasure workflow.

    Two routes through the same parser:

    - ``workspaces erase request --subject ... --requester-ref ... --reason ...``
      writes an ERASURE_REQUESTED event and exits. Use when an intake
      ticket fires and a human review must come before the sweep.
    - ``workspaces erase --subject ... --legal-basis ... --requester-ref ...
      --reason ...`` runs the full sweep + execute pipeline.

    ``--dry-run`` short-circuits writes; useful for previewing what the
    sweep would touch.
    """
    from .. import erasure

    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    sub_cmd = getattr(args, "erase_command", None)
    if sub_cmd == "request":
        try:
            res = erasure.request(
                folder, args.subject,
                requester_ref=args.requester_ref,
                reason=args.reason,
                log_root=_log_root(args),
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(f"erasure request opened")
        print(f"  request_id: {res['request_id']}")
        print(f"  audit_id:   {res['audit_id']}")
        print(f"  folder:     {res['folder']}")
        return 0

    # Default form: full execute (or dry-run).
    if not args.subject:
        print("error: --subject is required", file=sys.stderr)
        return 3
    if not getattr(args, "dry_run", False):
        # Hard requirements for actual execution.
        if not args.legal_basis or not args.requester_ref or not args.reason:
            print(
                "error: erase execute requires --legal-basis, "
                "--requester-ref, and --reason. Use --dry-run for preview.",
                file=sys.stderr,
            )
            return 3
    try:
        report = erasure.execute(
            folder, args.subject,
            legal_basis=args.legal_basis,
            requester_ref=args.requester_ref,
            reason=args.reason,
            cascade=bool(getattr(args, "cascade", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            log_root=_log_root(args),
        )
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    print(json.dumps(report.to_dict(), indent=2, default=str, sort_keys=True))
    return 0

def cmd_erase_status(args: argparse.Namespace) -> int:
    """Print the cascade manifest for an existing erase request."""
    from .. import erasure
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    manifest = erasure.status(folder, args.request_id, log_root=_log_root(args))
    print(json.dumps(manifest, indent=2, default=str, sort_keys=True))
    return 0

def cmd_workspace(args: argparse.Namespace) -> int:
    """Dispatch ``workspaces workspace <subcommand>`` (B7)."""
    sub = getattr(args, "workspace_command", None)
    if sub == "add":
        return _cmd_workspace_add(args)
    if sub == "remove":
        return _cmd_workspace_remove(args)
    if sub == "list":
        return _cmd_workspace_list(args)
    if sub == "migrate":
        return _cmd_workspace_migrate(args)
    if sub == "gc":
        return _cmd_workspace_gc(args)
    print(f"unknown workspace subcommand: {sub!r}", file=sys.stderr)
    return 2


def _cmd_workspace_add(args: argparse.Namespace) -> int:
    """Register a folder in the known-workspaces allowlist (doctor's hint)."""
    from ..workspace_registry import add_known_workspace
    res = add_known_workspace(args.folder_path, label=args.label,
                              log_root=_log_root(args))
    print(f"registered workspace: {res['path']}  (total: {res['total']})")
    return 0


def _cmd_workspace_remove(args: argparse.Namespace) -> int:
    from ..workspace_registry import remove_known_workspace
    if remove_known_workspace(args.folder_path, log_root=_log_root(args)):
        print(f"removed workspace: {args.folder_path}")
        return 0
    print(f"not registered: {args.folder_path}", file=sys.stderr)
    return 1


def _cmd_workspace_list(args: argparse.Namespace) -> int:
    from ..workspace_registry import list_known_workspaces
    ws = list_known_workspaces(log_root=_log_root(args))
    if not ws:
        print("no workspaces registered.")
        return 0
    for w in ws:
        label = w.get("label") or ""
        print(f"{w.get('path')}" + (f"  [{label}]" if label else ""))
    return 0

def _cmd_workspace_migrate(args: argparse.Namespace) -> int:
    from ..workspace_migrate import migrate_workspace, WorkspaceMigrateError
    strategy = args.on_collision.replace("-", "_")
    try:
        result = migrate_workspace(
            from_path=args.from_path,
            to_path=args.to_path,
            on_collision=strategy,
            operator=args.operator,
            log_root=_log_root(args),
        )
    except WorkspaceMigrateError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"migrated workspace log:")
    print(f"  from: {result.from_path}  ({result.from_hash})")
    print(f"    to: {result.to_path}  ({result.to_hash})")
    print(f"  strategy:    {result.strategy}")
    print(f"  events:      {result.event_count}")
    print(f"  audit_id:    {result.audit_id}")
    return 0

def _cmd_workspace_gc(args: argparse.Namespace) -> int:
    if not args.orphans:
        print("nothing to do — pass --orphans to scan", file=sys.stderr)
        return 0
    if args.delete and not args.yes_i_mean_it:
        print("ERROR: --delete requires --yes-i-mean-it", file=sys.stderr)
        return 2
    if args.delete and args.archive:
        print("ERROR: --delete and --archive are mutually exclusive",
              file=sys.stderr)
        return 2
    from ..workspace_migrate import gc_orphans
    results = gc_orphans(
        log_root=_log_root(args),
        archive=args.archive,
        delete=args.delete,
    )
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2,
                          sort_keys=True, default=str))
        return 0
    if not results:
        print("(no log directories found)")
        return 0
    print(f"{'hash':<34} {'count':>6}  {'action':<10}  last_event_ts        recovered_path")
    for r in results:
        print(f"{r.folder_hash:<34} {r.event_count:>6}  {r.action:<10}  "
              f"{int(r.last_event_ts):<20} {r.recovered_path or '(unknown)'}")
    return 0

def cmd_models(args: argparse.Namespace) -> int:
    """Dispatch ``workspaces models <subcommand>``."""
    sub = getattr(args, "models_command", None)
    if sub == "list":
        return _cmd_models_list(args)
    if sub == "register":
        return _cmd_models_register(args)
    if sub == "pull":
        return _cmd_models_pull(args)
    if sub == "config":
        return _cmd_models_config(args)
    if sub == "config-show":
        return _cmd_models_config_show(args)
    print(f"unknown models subcommand: {sub!r}", file=sys.stderr)
    return 2

def _workspace_standard_gguf() -> str:
    """The workspace's ONE standard local model: the model registered under the
    ``workspace`` role, else the single smallest registered GGUF. "" if none.

    The workspace has one standard model; a companion's model (e.g. role
    ``code-fix``) is not the workspace default, and extra tiers are user-set."""
    from ..models_registry import list_models, models_for_role

    def _gguf(mid: str) -> str:
        for e in list_models():
            if e.id == mid and e.artifact_path:
                p = Path(e.artifact_path).expanduser()
                if p.suffix == ".gguf" and p.exists():
                    return str(p)
        return ""

    for mid in models_for_role("workspace"):
        path = _gguf(mid)
        if path:
            return path
    found: list[tuple[int, str]] = []
    for e in list_models():
        if e.artifact_path:
            p = Path(e.artifact_path).expanduser()
            if p.suffix == ".gguf" and p.exists():
                found.append((p.stat().st_size, str(p)))
    found.sort(key=lambda t: t[0])
    return found[0][1] if found else ""

def _cmd_models_config(args: argparse.Namespace) -> int:
    from ..workspace_cascade import write_local_config
    local_url = getattr(args, "local_url", "")
    local_model = getattr(args, "local_model", "")
    cloud_url = getattr(args, "cloud_url", "")
    # The workspace's default local rung is ONE standard model. BYOK --local-url wins;
    # else the registered workspace-standard GGUF. (Multi-model local cascades are a
    # power-user choice via a hand-written config 'local' array.)
    standard = "" if local_url else _workspace_standard_gguf()
    if not standard and not local_url and not cloud_url:
        print("nothing to configure. Choose one:\n"
              "  BYOK local endpoint:  workspaces models config --local-url <url> --local-model <id>\n"
              "  registered local model: workspaces models register --role workspace --model <id> --artifact-path <path>\n"
              "  cloud fallback:       workspaces models config --cloud-url <url> --cloud-model <id>\n"
              "                        then bind an egress connector credential_ref",
              file=sys.stderr)
        return 2
    kw: dict[str, Any] = {}
    if local_url:
        kw["local_url"] = local_url
        kw["local_model"] = local_model or "local-model"
    elif standard:
        kw["local_models"] = [{"model": standard}]
    if cloud_url:
        kw["cloud_url"] = cloud_url
        kw["cloud_model"] = args.cloud_model
    if getattr(args, "cloud_api_key", ""):
        print(
            "warning: --cloud-api-key is deprecated and ignored; use an "
            "egress connector credential_ref=env:NAME or keydir:path",
            file=sys.stderr,
        )
    cfg = write_local_config(**kw)
    print(f"wrote {cfg}")
    if local_url:
        print(f"  local (BYOK): {kw['local_model']} @ {local_url}")
    elif standard:
        print(f"  workspace standard local model: {Path(standard).parent.name}  ({standard})")
    if cloud_url:
        print(f"  cloud fallback: {args.cloud_model} @ {cloud_url}")
    print("ask_workspace / workspace_cascade now use this with no per-shell env "
          "(envs still override). Companions use their own models; "
          "extra cascade/cloud tiers are user-set.")
    return 0

def _cmd_models_config_show(args: argparse.Namespace) -> int:
    from ..workspace_cascade import config_path, _local_config, tiers_for_workspace
    cfg = _local_config()
    print(f"config: {config_path()}")
    print(json.dumps(cfg, indent=2) if cfg else "  (empty — run `workspaces models config`)")
    tiers = tiers_for_workspace()
    if not tiers:
        print("resolved tiers: none (no local/cloud configured)")
        return 0
    print("resolved tiers (in order):")
    for t in tiers:
        kind = "cloud" if t.is_cloud else ("in-process" if t.url == "inproc" else "http")
        print(f"  {t.name:<28} {kind:<11} model={t.model}")
    return 0

def _cmd_models_list(args: argparse.Namespace) -> int:
    from ..models_registry import list_models, health_check

    entries = list_models()
    if args.json:
        output = []
        for e in entries:
            row = {
                "id": e.id,
                "artifact_path": e.artifact_path,
                "sha256_verified": e.sha256_verified,
                "registered_at": e.registered_at,
                "registered_via": e.registered_via,
                "roles": e.roles,
            }
            if args.health:
                row["health"] = health_check(e)
            output.append(row)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    if not entries:
        print("(no models registered; run `workspaces models register --role R --model M`)")
        return 0

    if args.health:
        # 0.6.8.2: surface endpoint reachability alongside artifact health.
        # Format expected by the model health report:
        #   <id>  role=<r>  artifact=✓  endpoint=✓  (<url>)
        for e in entries:
            h = health_check(e)
            role = h.get("role") or (",".join(e.roles) if e.roles else "-")
            artifact_glyph = "✓" if h.get("artifact_exists") else "✗"
            reachable = h.get("endpoint_reachable")
            if reachable is True:
                ep_glyph = "✓"
                ep_suffix = f"  ({h.get('endpoint_url', '')})"
            elif reachable is False:
                ep_glyph = "✗"
                err = h.get("endpoint_error", "")
                ep_suffix = f"  ({err})" if err else ""
            else:
                ep_glyph = "—"
                ep_suffix = "  (no endpoint configured)"
            print(f"{e.id}  role={role}  "
                  f"artifact={artifact_glyph}  endpoint={ep_glyph}{ep_suffix}")
    else:
        print(f"{'id':<28} {'registered_via':<14} roles")
        for e in entries:
            roles = ",".join(e.roles)
            print(f"{e.id:<28} {e.registered_via:<14} {roles}")
    return 0

def _cmd_models_register(args: argparse.Namespace) -> int:
    from ..models_registry import (
        register_model, models_dir, InvalidRoleError, ModelRegistryError,
    )

    artifact_path = args.artifact_path
    if not artifact_path:
        # Default: ~/.workspace/models/<id>/<id>.gguf (the canonical symlink)
        artifact_path = str(models_dir() / args.model / f"{args.model}.gguf")

    try:
        entry = register_model(
            args.model,
            args.role,
            artifact_path=artifact_path,
            sha256=args.sha256,
            via="offline" if args.offline else "register",
        )
    except InvalidRoleError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ModelRegistryError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"registered: {entry.id}")
    print(f"  role(s):        {', '.join(entry.roles)}")
    print(f"  artifact_path:  {entry.artifact_path}")
    print(f"  registered_via: {entry.registered_via}")
    print(f"  registered_at:  {entry.registered_at}")
    return 0

def _cmd_models_pull(args: argparse.Namespace) -> int:
    from ..models_registry import pull_model

    package_root = Path(args.package_root) if args.package_root else None
    result = pull_model(args.name, package_root=package_root)
    if result["stdout"]:
        print(result["stdout"])
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    return 0 if result["ok"] else 1

DOCTOR_EXIT_OK = 0

DOCTOR_EXIT_WARN = 10

DOCTOR_EXIT_ERROR = 20

DOCTOR_LEVEL_OK = "ok"

DOCTOR_LEVEL_INFO = "info"

DOCTOR_LEVEL_WARN = "warn"

DOCTOR_LEVEL_ERROR = "error"

def _doctor_check_python() -> dict:
    """Accept 3.10+, warn otherwise."""
    import sys as _sys
    v = _sys.version_info
    have = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return {"name": "python_version", "level": DOCTOR_LEVEL_OK,
                "detail": f"{have} (>= 3.10)"}
    return {"name": "python_version", "level": DOCTOR_LEVEL_WARN,
            "detail": f"{have} (recommend >= 3.10)"}

def _doctor_check_required_deps() -> list[dict]:
    """cryptography>=41.0 and anyascii>=0.3 must be present."""
    out: list[dict] = []
    # cryptography
    try:
        import cryptography  # type: ignore[import-untyped]
        ver = getattr(cryptography, "__version__", "0")
        try:
            major = int(ver.split(".", 1)[0])
        except (ValueError, IndexError):
            major = 0
        if major >= 41:
            out.append({"name": "dep_cryptography",
                        "level": DOCTOR_LEVEL_OK,
                        "detail": f"cryptography {ver} (>= 41.0)"})
        else:
            out.append({"name": "dep_cryptography",
                        "level": DOCTOR_LEVEL_ERROR,
                        "detail": f"cryptography {ver} too old (need >= 41.0)"})
    except ImportError:
        out.append({"name": "dep_cryptography",
                    "level": DOCTOR_LEVEL_ERROR,
                    "detail": "missing — `pip install cryptography>=41.0`"})
    # anyascii (ASCII-folding for the Tier B+ confusable-bypass control)
    try:
        import anyascii  # noqa: F401
        out.append({"name": "dep_anyascii",
                    "level": DOCTOR_LEVEL_OK,
                    "detail": "present"})
    except ImportError:
        out.append({"name": "dep_anyascii",
                    "level": DOCTOR_LEVEL_ERROR,
                    "detail": "missing — `pip install anyascii>=0.3`"})
    # mcp — REQUIRED: the product is the MCP server; both entry points need it.
    # Not always *running*, but it must be installed so it works when invoked.
    try:
        import mcp  # noqa: F401
        out.append({"name": "dep_mcp",
                    "level": DOCTOR_LEVEL_OK,
                    "detail": "present"})
    except ImportError:
        out.append({"name": "dep_mcp",
                    "level": DOCTOR_LEVEL_ERROR,
                    "detail": "missing — `pip install mcp>=1.2`"})
    return out

def _doctor_check_optional_deps() -> list[dict]:
    """pypdf / python-docx — missing is a warn (not error).

    `mcp` was promoted to a required dependency (see _doctor_check_required_deps);
    it is no longer listed here.
    """
    optional = [
        ("pypdf", "pypdf"),
        ("python-docx", "docx"),
    ]
    out: list[dict] = []
    for pretty, modname in optional:
        try:
            __import__(modname)
            out.append({"name": f"opt_dep_{pretty}",
                        "level": DOCTOR_LEVEL_OK,
                        "detail": f"{pretty} present"})
        except ImportError:
            out.append({"name": f"opt_dep_{pretty}",
                        "level": DOCTOR_LEVEL_WARN,
                        "detail": f"{pretty} missing (optional)"})
    return out

def _doctor_check_key_dir() -> dict:
    """identity.priv must exist with 0600 (owner-only). Warn on group/other readable."""
    import stat as _stat
    try:
        from .. import signing
        priv_path = signing._private_key_path()
    except Exception as e:
        return {"name": "key_dir_perms",
                "level": DOCTOR_LEVEL_ERROR,
                "detail": f"cannot resolve key path: {e}"}
    if not priv_path.exists():
        # Will be created on first MutationLog.append; not an error here.
        return {"name": "key_dir_perms",
                "level": DOCTOR_LEVEL_INFO,
                "detail": f"identity.priv not yet created at {priv_path} "
                          f"(will be generated on first append)"}
    try:
        mode = priv_path.stat().st_mode
    except OSError as e:
        return {"name": "key_dir_perms",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"cannot stat {priv_path}: {e}"}
    perms = _stat.S_IMODE(mode)
    if perms == 0o600:
        return {"name": "key_dir_perms",
                "level": DOCTOR_LEVEL_OK,
                "detail": f"{priv_path} mode 0600"}
    if perms & (_stat.S_IRGRP | _stat.S_IROTH | _stat.S_IWGRP | _stat.S_IWOTH):
        return {"name": "key_dir_perms",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"{priv_path} mode {oct(perms)} "
                          f"(group/other readable; recommend chmod 600)"}
    return {"name": "key_dir_perms",
            "level": DOCTOR_LEVEL_OK,
            "detail": f"{priv_path} mode {oct(perms)}"}

def _doctor_check_controller_key() -> dict:
    """Controller key is needed for purge/erase; info-level only when missing."""
    try:
        from .. import signing
        fp = signing.public_controller_key_fingerprint()
    except Exception as e:
        return {"name": "controller_key",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"cannot resolve controller key: {e}"}
    if fp is None:
        return {"name": "controller_key",
                "level": DOCTOR_LEVEL_INFO,
                "detail": "not initialised (run `workspaces keys init-controller` "
                          "before purge/erase)"}
    return {"name": "controller_key",
            "level": DOCTOR_LEVEL_OK,
            "detail": f"initialised, fingerprint {fp}"}

def _doctor_check_log_root(log_root: Path) -> dict:
    """Log root must exist (or be creatable) and be writable."""
    try:
        log_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"name": "log_root",
                "level": DOCTOR_LEVEL_ERROR,
                "detail": f"cannot create {log_root}: {e}"}
    if not os.access(str(log_root), os.W_OK):
        return {"name": "log_root",
                "level": DOCTOR_LEVEL_ERROR,
                "detail": f"{log_root} is not writable"}
    return {"name": "log_root",
            "level": DOCTOR_LEVEL_OK,
            "detail": f"{log_root} writable"}

def _doctor_check_workspace_registry(log_root: Path) -> dict:
    """Workspace allowlist — the first wall a new user hits on `ingest`/`status`.

    A fresh install has no registered workspaces, so a folder operation is
    refused unless the folder is registered or WORKSPACES_ALLOW_UNREGISTERED=1. Say
    so here with the exact remedy, instead of letting the user discover it as a
    cryptic refusal mid-command.
    """
    from ..folder_context import ALLOW_UNREGISTERED_ENV
    allow = os.environ.get(ALLOW_UNREGISTERED_ENV) == "1"
    try:
        from .. import workspace_registry
        n = len(workspace_registry.list_known_workspaces(log_root=log_root))
    except Exception as e:
        return {"name": "workspace_registry", "level": DOCTOR_LEVEL_WARN,
                "detail": f"could not read registry: {e}"}
    if n > 0:
        return {"name": "workspace_registry", "level": DOCTOR_LEVEL_OK,
                "detail": f"{n} workspace(s) registered"}
    if allow:
        return {"name": "workspace_registry", "level": DOCTOR_LEVEL_INFO,
                "detail": f"none registered; {ALLOW_UNREGISTERED_ENV}=1 set "
                          "(unregistered folders allowed)"}
    return {"name": "workspace_registry", "level": DOCTOR_LEVEL_WARN,
            "detail": ("no workspaces registered — folder ops will be refused. "
                       "Register: `workspaces workspace add <folder>`  (or set "
                       f"{ALLOW_UNREGISTERED_ENV}=1 to allow any folder)")}

def _doctor_check_air_gap(log_root: Path) -> dict:
    """An air-gap declared in policy binds every process only with an OS lock.

    ``is_air_gapped()`` covers just the code paths that consult it; the
    ``deploy/firewall/`` templates are the tier that binds the whole host
    (docs/concepts/air-gap-enforcement.md). When a registered workspace declares
    local-only, probe for the Linux nftables lock; on other platforms say the
    lock cannot be verified. Diagnostics only — a missing ``nft`` degrades to
    the recommendation line, never a crash.
    """
    remedy = ("apply an OS-level egress lock — deploy/firewall/ "
              "(see docs/concepts/air-gap-enforcement.md)")
    try:
        from .. import workspace_registry
        from ..policy import is_air_gapped
        paths = [w.get("path") for w in
                 workspace_registry.list_known_workspaces(log_root=log_root)]
        gapped = [p for p in paths if p and is_air_gapped(p)]
    except Exception as e:
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_INFO,
                "detail": f"could not read registry/policies: {e}"}
    if not gapped:
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_OK,
                "detail": "no registered workspace declares local-only"}
    n = len(gapped)
    import platform as _platform
    if _platform.system().lower() != "linux":
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_INFO,
                "detail": f"{n} air-gapped workspace(s); cannot verify an OS "
                          f"egress lock on {_platform.system()} — {remedy}"}
    import shutil
    import subprocess
    nft = shutil.which("nft")
    if nft is None:
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"{n} air-gapped workspace(s) but nft is not "
                          f"installed — {remedy}"}
    try:
        result = subprocess.run(
            [nft, "list", "table", "inet", "rvnd_egress_lock"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"{n} air-gapped workspace(s); could not query nft "
                          f"({type(e).__name__}) — {remedy}"}
    if result.returncode == 0:
        return {"name": "air_gap_enforcement",
                "level": DOCTOR_LEVEL_OK,
                "detail": f"{n} air-gapped workspace(s); nftables table "
                          f"inet rvnd_egress_lock is loaded"}
    return {"name": "air_gap_enforcement",
            "level": DOCTOR_LEVEL_WARN,
            "detail": f"{n} air-gapped workspace(s) but nftables table "
                      f"inet rvnd_egress_lock is not loaded — {remedy}"}

def _doctor_check_sample_round_trip(log_root: Path) -> dict:
    """Create a tmpdir folder, append a system event, verify chain."""
    import tempfile
    from ..mutation_log import LogEvent, MutationLog

    try:
        with tempfile.TemporaryDirectory(prefix="workspaces-doctor-") as td:
            folder = Path(td) / "sample_workspace"
            folder.mkdir()
            # This probe exercises append + verify_chain mechanics, not the A6
            # allowlist. Its scratch folder is never registered, so on any
            # enforcing install (fresh machine or populated registry alike) the
            # MutationLog constructor would refuse it and the probe could never
            # report OK — masking the very diagnosis it exists to give. Allow
            # unregistered folders for the probe's own scratch dir only; the
            # prior value is restored below so enforcement for real folder ops
            # is untouched (registry state is surfaced by workspace_registry).
            prior = os.environ.get(ALLOW_UNREGISTERED_ENV)
            os.environ[ALLOW_UNREGISTERED_ENV] = "1"
            try:
                log = MutationLog(folder, log_root=log_root)
                evt = LogEvent(
                    event="system",
                    folder_path=str(folder),
                    pair_id="doctor:sample",
                    channel="system",
                    actor="system:doctor",
                    extra={"kind": "doctor_sample"},
                )
                log.append(evt)
                result = log.verify_chain()
            finally:
                if prior is None:
                    os.environ.pop(ALLOW_UNREGISTERED_ENV, None)
                else:
                    os.environ[ALLOW_UNREGISTERED_ENV] = prior
            if not result.ok:
                return {"name": "sample_round_trip",
                        "level": DOCTOR_LEVEL_ERROR,
                        "detail": f"verify_chain failed: "
                                  f"broken_links={len(result.broken_links)} "
                                  f"sig_failures={len(result.signature_failures)}"}
            return {"name": "sample_round_trip",
                    "level": DOCTOR_LEVEL_OK,
                    "detail": f"appended + verified ({result.total_events} event; "
                              f"scratch folder, allowlist scoped off for probe)"}
    except Exception as e:
        return {"name": "sample_round_trip",
                "level": DOCTOR_LEVEL_ERROR,
                "detail": f"round-trip failed: {type(e).__name__}: {e}"}

def _doctor_check_filesystem(log_root: Path) -> dict:
    """Best-effort filesystem-type detection on the log root; warn on NFS / FUSE."""
    try:
        # macOS: stat -f, Linux: stat --file-system; both expose fs type.
        import platform as _platform
        sys_name = _platform.system().lower()
        if sys_name == "darwin":
            cmd = ["stat", "-f", "%T", str(log_root)]
        elif sys_name == "linux":
            cmd = ["stat", "-f", "--format=%T", str(log_root)]
        else:
            return {"name": "filesystem",
                    "level": DOCTOR_LEVEL_INFO,
                    "detail": f"{sys_name}: type detection unsupported"}
        import subprocess
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=2,
        )
        fstype = (result.stdout or "").strip().lower()
        if not fstype:
            return {"name": "filesystem",
                    "level": DOCTOR_LEVEL_INFO,
                    "detail": "could not detect filesystem type"}
        risky = {"nfs", "fuse", "fuseblk", "smbfs", "cifs", "sshfs"}
        if any(token in fstype for token in risky):
            return {"name": "filesystem",
                    "level": DOCTOR_LEVEL_WARN,
                    "detail": f"{fstype} on {log_root} — flock may not work "
                              f"correctly under concurrency"}
        return {"name": "filesystem",
                "level": DOCTOR_LEVEL_OK,
                "detail": f"{fstype}"}
    except Exception as e:
        return {"name": "filesystem",
                "level": DOCTOR_LEVEL_INFO,
                "detail": f"detection failed: {type(e).__name__}"}

def _doctor_check_mcp_reachable(skip: bool = False) -> dict:
    """Spawn `workspaces-mcp --help`; success means the entry point works."""
    if skip:
        return {"name": "mcp_server_reachable",
                "level": DOCTOR_LEVEL_INFO,
                "detail": "skipped (--skip-mcp)"}
    import subprocess
    import shutil
    # Try the installed entry point first.
    exe = shutil.which("workspaces-mcp")
    if exe is None:
        return {"name": "mcp_server_reachable",
                "level": DOCTOR_LEVEL_WARN,
                "detail": "`workspaces-mcp` not on PATH (entry point not installed)"}
    try:
        result = subprocess.run(
            [exe, "--help"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return {"name": "mcp_server_reachable",
                    "level": DOCTOR_LEVEL_OK,
                    "detail": f"{exe} --help → exit 0"}
        return {"name": "mcp_server_reachable",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"{exe} --help → exit {result.returncode}"}
    except (subprocess.SubprocessError, OSError) as e:
        return {"name": "mcp_server_reachable",
                "level": DOCTOR_LEVEL_WARN,
                "detail": f"could not spawn {exe}: {type(e).__name__}: {e}"}

def _doctor_check_symlink_mode() -> dict:
    """Print current WORKSPACE_SYMLINK_MODE (follow/isolate)."""
    try:
        from ..folder_context import symlink_mode
        mode = symlink_mode()
    except Exception as e:
        return {"name": "symlink_mode",
                "level": DOCTOR_LEVEL_INFO,
                "detail": f"could not resolve mode: {e}"}
    env = os.environ.get("WORKSPACE_SYMLINK_MODE", "(unset → default 'follow')")
    return {"name": "symlink_mode",
            "level": DOCTOR_LEVEL_INFO,
            "detail": f"active mode: {mode}; WORKSPACE_SYMLINK_MODE={env}"}

def _doctor_check_python_binding() -> dict:
    """Detect the Python-binding mismatch class of installation bug.

    Installation mismatch pattern: a `workspaces` console-script lives on PATH but its shebang
    points at a Python interpreter that does NOT have the `workspaces` package
    installed.  The user sees ``ModuleNotFoundError: No module named 'workspaces'``
    and cannot even run ``workspaces doctor`` to diagnose it — by then the import
    has already failed.  This check, when reachable, catches the more subtle
    cousins (drift, shadowing) where doctor still runs but the wrong copy is
    being launched from the shell.

    Status mapping:

      * **GREEN** — every ``workspaces`` script on PATH is bound to a Python that
        successfully imports ``workspaces`` at the same version as the running
        interpreter.
      * **YELLOW** — some script imports ``workspaces`` but at a *different*
        version (install drift between multiple Pythons).
      * **RED** — at least one script's bound Python cannot import ``workspaces``
        at all. Remediation command is included
        in the detail string.
      * **INFO** — no ``workspaces`` script found on PATH (running from source,
        or in a sandbox without PATH propagation); nothing to compare.
    """
    import shutil as _shutil
    import subprocess as _subprocess
    import sys as _sys

    try:
        from workspaces import __version__ as _our_version  # noqa: WPS433
    except Exception:  # pragma: no cover — workspaces is importable in tests
        _our_version = "?"

    # Walk every directory on PATH; collect every distinct 'workspaces' file.
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    seen: set[str] = set()
    scripts: list[str] = []
    for d in path_dirs:
        if not d:
            continue
        candidate = os.path.join(d, "workspaces")
        if os.path.isfile(candidate) and candidate not in seen:
            seen.add(candidate)
            scripts.append(candidate)

    if not scripts:
        return {
            "name": "python_binding",
            "level": DOCTOR_LEVEL_INFO,
            "detail": (
                "no 'workspaces' script found on PATH (running from source "
                "or sandboxed)"
            ),
        }

    findings: list[str] = []
    # Per-script severity. The script the shell actually resolves is scripts[0]
    # (PATH order). A broken ACTIVE script is an ERROR (you genuinely can't run
    # workspaces); broken/drifting SIBLINGS when the active one is healthy are a
    # WARN (cleanup advisory) — otherwise any machine that ever had a second
    # install would fail doctor forever despite a perfectly good venv.
    per_script_level: list[str] = []
    for script in scripts:
        # Extract the shebang's interpreter, if any.
        py_path: str | None = None
        try:
            with open(script, "rb") as fh:
                first = fh.readline()
            if first.startswith(b"#!"):
                shebang = first[2:].decode("utf-8", errors="replace").strip()
                # Handle `/usr/bin/env python` style by extracting the last token.
                parts = shebang.split()
                py_path = parts[-1] if parts else None
        except OSError as exc:
            findings.append(f"{script}: cannot read shebang ({exc})")
            per_script_level.append(DOCTOR_LEVEL_WARN)
            continue

        if not py_path or not os.path.isfile(py_path):
            findings.append(
                f"{script}: shebang interpreter not found ({py_path!r})"
            )
            per_script_level.append(DOCTOR_LEVEL_ERROR)
            continue

        # Probe the bound Python for the package.
        try:
            probe = _subprocess.run(
                [py_path, "-c", "import workspaces; print(workspaces.__version__)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:  # pragma: no cover — probe shouldn't blow up
            findings.append(f"{script}: probe failed ({exc})")
            per_script_level.append(DOCTOR_LEVEL_ERROR)
            continue

        if probe.returncode != 0:
            err_tail = (probe.stderr or probe.stdout or "").strip().splitlines()
            tail = err_tail[-1] if err_tail else "no output"
            # Common mismatch case: install lives elsewhere.
            try:
                runtime_dir = str(Path(__file__).resolve().parents[3])
            except Exception:  # pragma: no cover
                runtime_dir = "/path/to/workspace/runtime"
            findings.append(
                f"{script} -> {py_path}: cannot import workspaces ({tail}). "
                f"Fix: {py_path} -m pip install -e {runtime_dir}  "
                "(or use a venv with the matching Python version — recommended)"
            )
            per_script_level.append(DOCTOR_LEVEL_ERROR)
            continue

        bound_version = probe.stdout.strip()
        if bound_version != _our_version:
            findings.append(
                f"{script} -> {py_path}: workspaces {bound_version} "
                f"(running interpreter has {_our_version}) — version drift"
            )
            per_script_level.append(DOCTOR_LEVEL_WARN)
            continue

        findings.append(
            f"{script} -> {py_path}: workspaces {bound_version} (ok)"
        )
        per_script_level.append(DOCTOR_LEVEL_OK)

    # Active script (first on PATH) governs ERROR; siblings only ever WARN.
    active_level = per_script_level[0] if per_script_level else DOCTOR_LEVEL_OK
    if active_level == DOCTOR_LEVEL_ERROR:
        worst = DOCTOR_LEVEL_ERROR
    elif (
        active_level == DOCTOR_LEVEL_WARN
        or any(lvl != DOCTOR_LEVEL_OK for lvl in per_script_level[1:])
    ):
        worst = DOCTOR_LEVEL_WARN
    else:
        worst = DOCTOR_LEVEL_OK

    detail = "; ".join(findings)
    return {"name": "python_binding", "level": worst, "detail": detail}

def _doctor_overall_exit(checks: list[dict]) -> int:
    """Map a set of check levels to the stable exit-code taxonomy."""
    has_error = any(c["level"] == DOCTOR_LEVEL_ERROR for c in checks)
    has_warn = any(c["level"] == DOCTOR_LEVEL_WARN for c in checks)
    if has_error:
        return DOCTOR_EXIT_ERROR
    if has_warn:
        return DOCTOR_EXIT_WARN
    return DOCTOR_EXIT_OK

_DOCTOR_GLYPHS = {
    DOCTOR_LEVEL_OK: "[OK]",
    DOCTOR_LEVEL_INFO: "[i] ",
    DOCTOR_LEVEL_WARN: "[!] ",
    DOCTOR_LEVEL_ERROR: "[x] ",
}

def cmd_doctor(args: argparse.Namespace) -> int:
    """Run preflight diagnostics and print a structured report."""
    log_root = _log_root(args)
    skip_mcp = bool(getattr(args, "skip_mcp", False))

    checks: list[dict] = []
    checks.append(_doctor_check_python())
    checks.append(_doctor_check_python_binding())
    checks.extend(_doctor_check_required_deps())
    checks.extend(_doctor_check_optional_deps())
    checks.append(_doctor_check_key_dir())
    checks.append(_doctor_check_controller_key())
    checks.append(_doctor_check_log_root(log_root))
    checks.append(_doctor_check_workspace_registry(log_root))
    checks.append(_doctor_check_air_gap(log_root))
    checks.append(_doctor_check_filesystem(log_root))
    checks.append(_doctor_check_sample_round_trip(log_root))
    checks.append(_doctor_check_mcp_reachable(skip=skip_mcp))
    checks.append(_doctor_check_symlink_mode())

    exit_code = _doctor_overall_exit(checks)

    if args.json:
        summary = {
            "exit_code": exit_code,
            "exit_code_taxonomy": {
                str(DOCTOR_EXIT_OK): "all green",
                str(DOCTOR_EXIT_WARN): "warnings only",
                str(DOCTOR_EXIT_ERROR): "errors present",
            },
            "log_root": str(log_root),
            "checks": checks,
            "counts": {
                "ok": sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_OK),
                "info": sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_INFO),
                "warn": sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_WARN),
                "error": sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_ERROR),
            },
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return exit_code

    print("workspaces doctor — preflight diagnostics")
    print(f"  log_root: {log_root}")
    print()
    for c in checks:
        glyph = _DOCTOR_GLYPHS.get(c["level"], "    ")
        print(f"  {glyph} {c['name']:<24} {c['detail']}")
    print()
    n_ok = sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_OK)
    n_info = sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_INFO)
    n_warn = sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_WARN)
    n_err = sum(1 for c in checks if c["level"] == DOCTOR_LEVEL_ERROR)
    print(f"  summary: ok={n_ok} info={n_info} warn={n_warn} error={n_err}")
    if exit_code == DOCTOR_EXIT_OK:
        print("  verdict: all green")
    elif exit_code == DOCTOR_EXIT_WARN:
        print("  verdict: warnings only — review and proceed")
    else:
        print("  verdict: errors present — see [x] entries above")
    return exit_code

def cmd_discipline(args: argparse.Namespace) -> int:
    """Run the discipline gate over a folder (audit / diff / check)."""
    from ..discipline import run_discipline
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    files = args.files if args.discipline_command == "check" else None
    res = run_discipline(
        folder,
        mode=args.discipline_command,
        files=files,
        manifest=getattr(args, "manifest", None),
        write_audit=not getattr(args, "no_audit", False),
        log_root=_log_root(args),
        strict=getattr(args, "strict", False),
    )
    if res.get("error"):
        print(f"error: {res['error']}", file=sys.stderr)
        return 3
    print(f"discipline {res['mode']} on {folder}")
    print(f"  scanned {res['scanned']} file(s) — "
          f"failures: {res['failures']}, warnings: {res['warnings']}")
    for f in res["findings"]:
        print(f"  {f['severity'].upper():4} [{f['rule']}] {f['file']} — {f['detail']}")
    audit = res.get("audit") or {}
    if audit.get("recorded"):
        print(f"  audit: recorded ({audit['audit_id'][:12]})")
    elif audit:
        print(f"  audit: not recorded ({audit.get('reason')})")
    return 0 if res.get("clean") else 1

def cmd_cross_workspace(args: argparse.Namespace) -> int:
    """Governed lateral read from source workspaces into a target workspace."""
    try:
        target = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not args.source:
        print("ERROR: at least one --source is required", file=sys.stderr)
        return 2
    from ..cross_workspace import cross_workspace_read
    res = cross_workspace_read(
        target, args.source, role=args.role,
        autonomy_grade=args.grade, log_root=_log_root(args),
    )
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(f"Target workspace: {res['target']}  (role: {res['role']})")
    for link in res["links"]:
        v = link["verdict"]
        mark = {"GO": "✓", "CONDITIONAL": "•", "NO-GO": "✗"}.get(v, "?")
        print(f"  {mark} {v:<11} {link['source']}")
        if link.get("error"):
            print(f"      error: {link['error']}")
        elif link["pair_ids"]:
            print(f"      {len(link['pair_ids'])} source pair(s); "
                  f"audit {str(link['audit_id'])[:8]}")
    return 0

def cmd_lock(args: argparse.Namespace) -> int:
    """Governance verb: seal a workspace's memory at rest + turn on egress screening."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    did_shield = False
    if not args.no_shield:
        from ..policy import enable_lock
        enable_lock(folder, actor=args.actor or "user", log_root=_log_root(args))
        did_shield = True
    sealed = None
    if not args.no_seal:
        from ..seal import SealError, seal_folder
        try:
            pw = _seal_passphrase(args, confirm=True)
            sealed = seal_folder(folder, passphrase=pw, log_root=_log_root(args))
        except SealError as e:
            print(f"seal error: {e}", file=sys.stderr)
            return 2
    print(f"Workspace locked: {folder}")
    if did_shield:
        print("  egress: ON — only approved text leaves (by risk x autonomy "
              "grade); the rest is held or minimised.")
    if sealed:
        print(f"  at rest: memory sealed (AES-256-GCM), {sealed['files_sealed']} "
              "file(s) — readable only with the passphrase.")
    print("  note: your source files are unchanged on disk; the seal covers the "
          "workspace's memory + audit, not the original documents.")
    return 0

def cmd_unlock(args: argparse.Namespace) -> int:
    """Governance verb: decrypt a workspace's sealed memory back to disk."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    from ..seal import SealError, unseal_folder
    try:
        pw = _seal_passphrase(args, confirm=False)
        out = unseal_folder(folder, passphrase=pw, log_root=_log_root(args))
    except SealError as e:
        print(f"unlock error: {e}", file=sys.stderr)
        return 2
    print(f"Workspace unlocked: {folder}")
    print(f"  at rest: memory decrypted to disk ({out['files_restored']} "
          "file(s)) — working state.")
    print("  egress screening unchanged (adjust with `workspaces policy` or "
          "`workspaces lock --no-seal`).")
    return 0

def cmd_ask(args: argparse.Namespace) -> int:
    """One governed chat turn over a workspace — the CLI face of /Workspaces."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    from ..workspace_orchestrate import ask_workspace
    res = ask_workspace(args.query, folder, max_tokens=args.max_tokens,
                   log_root=_log_root(args))
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    g = res.get("governance", {})
    print(f"workspace: {res['folder']}")
    print(f"  tools this turn: {', '.join(g.get('tools', [])) or '—'}")
    comps = res.get("companions") or []
    if comps:
        print(f"  companions: {', '.join(c['name'] for c in comps)}")
    gr = res.get("grounding", {})
    if gr.get("applied"):
        print(f"  grounding: on — {len(gr.get('sources', []))} source(s), "
              "creators credited")
    if res.get("ok"):
        print(f"  served by: {res.get('served_by')}")
        print()
        print(res.get("answer", ""))
    else:
        c = res.get("cascade", {})
        print(f"  (no answer generated: {c.get('error', 'unknown')})")
        if c.get("advice"):
            print(f"  fix: {c['advice']}")
    print(f"  audit: {res.get('audit_id')}")
    return 0

def cmd_shadow_scan(args: argparse.Namespace) -> int:
    """Classify recorded cross-workspace crossings into shadow vs declared flow."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    from ..shadow_workflow import classify_shadow_workflows
    r = classify_shadow_workflows(folder, high_fan_in=args.high_fan_in,
                                  log_root=_log_root(args))
    if getattr(args, "json", False):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"shadow-scan: {folder}")
    print(f"  {r['summary']}")
    if r["declared_workflows"]:
        print(f"  declared workflows: {', '.join(r['declared_workflows'])}")
    def _show(label, items):
        for e in items:
            print(f"  [{label}] {e['source']} (role={e['role']}, "
                  f"x{e['count']}, last={e['last_verdict']})")
    _show("shadow", r["shadow"])
    _show("sign-off", r["needs_signoff"])
    _show("blocked", r["blocked"])
    _show("review", r["review"])
    return 0

def cmd_oversight(args: argparse.Namespace) -> int:
    """Show or set a workspace's oversight dial (top-level governance verb)."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not args.level:                       # show
        pol = load_policy(folder)
        active = "active" if pol.oversight_is_active else "muted"
        print(f"oversight for {folder}")
        print(f"  level:  {pol.oversight_default_level}")
        print(f"  prompts: {active}")
        print(f"  levels:  {' < '.join(OVERSIGHT_LEVELS)}")
        return 0
    set_oversight_level(folder, args.level, actor=args.actor or "user",
                        log_root=_log_root(args))
    print(f"oversight level set to {args.level!r} for {folder}  (audit-logged)")
    return 0

def cmd_mute(args: argparse.Namespace) -> int:
    """Mute oversight prompts (disclaimer-gated alias of disable-oversight)."""
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not args.i_accept_the_risk:
        print(OVERSIGHT_DISCLAIMER)
        print()
        print("To proceed, re-run with --i-accept-the-risk.")
        return 2
    disable_oversight(folder, accepted_by=args.accepted_by,
                      reason=args.reason or "", log_root=_log_root(args))
    print(f"oversight MUTED for {folder} (audit chain still records).")
    print(f"  re-enable with: workspaces unmute --folder {folder}")
    return 0

def cmd_unmute(args: argparse.Namespace) -> int:
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    enable_oversight(folder, actor=args.actor or "user", log_root=_log_root(args))
    print(f"oversight UN-MUTED for {folder}")
    return 0


# --- in-vivo Lens (USP-2): govern what the agent LEARNS ---
def cmd_lens(args: argparse.Namespace) -> int:
    import json as _json
    from workspaces import lens_service as _ls
    sub = args.lens_command
    if sub == "log":
        try:
            folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
        except NoFolderContextError as e:
            print(f"error: {e}", file=sys.stderr); return 3
        res = _ls.admission_log(folder, limit=args.limit)
        cap = res.get("cap")
        meter = (f"spent {res['spent']:.2f}/{cap:.2f}"
                 + ("  OVER" if res.get("over_budget") else "")
                 if cap is not None else f"spent {res['spent']:.2f} (no cap set)")
        print(f"folder: {res['folder']}   admissions: {res['count']}   "
              f"held(awaiting you): {res['held']}   budget: {meter}")
        for r in res["events"]:
            agg = " [aggregate-only]" if r.get("aggregate_only") else ""
            print(f"  {r['admission']:>6}  {r['class']:<22}{agg}  {r['reason']}")
        return 0
    if sub == "precedents":
        try:
            folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
        except NoFolderContextError as e:
            print(f"error: {e}", file=sys.stderr); return 3
        res = _ls.precedent_list(folder, include_inactive=args.include_inactive)
        print(f"folder: {res['folder']}   precedents: {res['count']}")
        for p in res["precedents"]:
            flag = " (revoked)" if p.get("revoked") else ""
            exp = f"  expires={p['expires_at']}" if p.get("expires_at") else ""
            print(f"  {p['id']:<16} ≥{p.get('similarity_threshold')}  "
                  f"{p.get('chosen_option','')}  by {p.get('actor','')}{exp}{flag}")
        return 0
    if sub == "cap":
        try:
            folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
        except NoFolderContextError as e:
            print(f"error: {e}", file=sys.stderr); return 3
        if args.set_cap is not None:
            r = _ls.budget_cap_set(folder, args.set_cap)
            if "error" in r:
                print(f"error: {r['error']}", file=sys.stderr); return 2
            print(f"cap set: {r['cap']}"); return 0
        cap = _ls.budget_cap_get(folder)
        print(f"cap: {cap if cap is not None else '(unset)'}")
        return 0
    try:
        params = _json.loads(args.json)
    except _json.JSONDecodeError as e:
        print(f"error: --json is not valid JSON: {e}", file=sys.stderr); return 2
    if not isinstance(params, dict):
        print("error: --json must be a JSON object", file=sys.stderr); return 2
    fn = {"classify": _ls.classify, "select": _ls.select, "budget": _ls.budget,
          "precedent-declare": _ls.precedent_declare,
          "precedent-revoke": _ls.precedent_revoke}[sub]
    print(_json.dumps(fn(params), indent=2, ensure_ascii=False))
    return 0


def cmd_grounding(args: argparse.Namespace) -> int:
    """Output review: the grounded/flagged/stopped feed + attribution coverage —
    the CLI face of workspace_grounder oversight.feed (parity with app + MCP)."""
    sub = args.grounding_command
    try:
        folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    if sub == "feed":
        from workspaces.governance import grounding_feed
        res = grounding_feed(folder, log_root=_log_root(args), limit=args.limit)
        print(f"folder: {res['folder']}   output-review events: {res['count']}   "
              f"flagged: {res['flagged']}")
        for r in res["events"]:
            mark = "grounded" if r["grounded"] else "UNGROUNDED"
            print(f"  {r['verdict']:>6}  {mark:<10}  oversight={r['oversight']:<10}  {r['reason']}")
        return 0
    if sub == "coverage":
        import json as _json
        from workspaces.workspace_grounder import GroundingLedger
        res = GroundingLedger(folder, log_root=_log_root(args)).coverage()
        print(_json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print(f"unknown grounding command: {sub}", file=sys.stderr)
    return 3


# --- policy matrix (autonomy x oversight grid) — the plan/policy layer ---
def _matrix_ctx(args: argparse.Namespace):
    """Return (folder, pm, effective_matrix). Effective = this workspace's own grid if
    set, else inherited (nearest ancestor / global default) — the override cascade."""
    folder = resolve_folder_context(args.folder, allow_unscoped=False, log_root=_log_root(args))
    from workspaces import policy_matrix as pm
    return folder, pm, pm.resolve_matrix(folder)


def cmd_matrix_show(args: argparse.Namespace) -> int:
    try:
        folder, pm, m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    own = pm.own_matrix(folder)
    print(f"folder: {folder}   ({'own override' if own is not None else 'inherits (global/ancestor)'})")
    print(pm.render_matrix_text(m))
    return 0


def cmd_matrix_set(args: argparse.Namespace) -> int:
    try:
        folder, pm, m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    try:
        pm.set_cell(m, args.grade, args.oversight, args.light)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    pm.save_own_matrix(folder, m)
    print(f"set {args.grade} x {args.oversight} = {args.light} (this workspace)")
    return 0


def cmd_matrix_set_row(args: argparse.Namespace) -> int:
    try:
        folder, pm, m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    try:
        pm.set_row(m, args.oversight, args.light)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr); return 2
    pm.save_own_matrix(folder, m)
    print(f"set row {args.oversight} = {args.light} (all grades, this workspace)")
    return 0


def cmd_matrix_set_col(args: argparse.Namespace) -> int:
    try:
        folder, pm, m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    try:
        pm.set_col(m, args.grade, args.light)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr); return 2
    pm.save_own_matrix(folder, m)
    print(f"set column {args.grade} = {args.light} (all oversight levels, this workspace)")
    return 0


def cmd_matrix_reset(args: argparse.Namespace) -> int:
    try:
        folder, pm, _m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    pm.clear_own_matrix(folder)
    print("override cleared — this workspace inherits the global/ancestor policy again")
    return 0


def cmd_matrix_explain(args: argparse.Namespace) -> int:
    try:
        _folder, pm, m = _matrix_ctx(args)
    except NoFolderContextError as e:
        print(f"error: {e}", file=sys.stderr); return 3
    try:
        r = pm.effective_light(m, grade=args.grade, oversight=args.oversight,
                               privacy_class=args.privacy, gate_verdict=args.verdict)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    print(f"{args.grade} x {args.oversight}  ->  {r['light'].upper()}")
    print(f"  painted={r['painted']}  floored_oversight={r['floored_oversight']}  "
          f"gate={r['gate_light']}")
    print(f"  why: {r['reason']}")
    return 0


_MATRIX_DISPATCH = {
    "show": cmd_matrix_show, "set": cmd_matrix_set, "set-row": cmd_matrix_set_row,
    "set-col": cmd_matrix_set_col, "reset": cmd_matrix_reset, "explain": cmd_matrix_explain,
}


def cmd_matrix(args: argparse.Namespace) -> int:
    h = _MATRIX_DISPATCH.get(args.matrix_command)
    if h is None:
        print(f"unknown matrix command: {args.matrix_command}", file=sys.stderr)
        return 3
    return h(args)


# ---------------------------------------------------------------------------
# init — first-run setup wizard. Sectioned, interactive (Enter = default),
# with --yes (accept defaults) and --dry-run (write nothing); idempotent.
# Writes only real config surfaces (the ~/.workspace home, the default
# workspaces folder via the registry, an init marker) and *explains* the
# per-workspace / model-dependent choices (oversight ladder, Lock tiers)
# rather than inventing global config RVND does not keep.
# ---------------------------------------------------------------------------

_OVERSIGHT_LADDER = [
    ("autonomous", "acts silently; never asks"),
    ("notify",     "acts, then tells you afterwards"),
    ("review",     "acts, holds the result until you look"),
    ("approve",    "shows the plan; stops before any side-effect"),
    ("supervised", "prompts at every step"),
    ("manual",     "suggests a plan; you run it yourself"),
]

_LOCAL_FIRST_PROMISE = (
    "RVND is local-first. The server binds to 127.0.0.1 only; your data,\n"
    "policies and models stay on this machine. An AI agent you connect sees\n"
    "the governed tool surface and the verdicts the server returns — not the\n"
    "raw bytes of your files, your signing keys, or sealed content. Every\n"
    "grant, run and refusal is signed into a per-folder tamper-evident record.\n"
    "A folder can be marked local-only and kept from any cloud model."
)


def _wsay(w: IO[str], m: str = "") -> None:
    w.write(m + "\n"); w.flush()


def _wask(inp: IO[str], w: IO[str], prompt: str, default: str) -> str:
    w.write(f"{prompt} [{default}]: "); w.flush()
    raw = inp.readline().strip()
    return raw or default


def _wask_yn(inp: IO[str], w: IO[str], prompt: str, default: bool = True) -> bool:
    w.write(f"{prompt} [{'Y/n' if default else 'y/N'}]: "); w.flush()
    raw = inp.readline().strip().lower()
    return default if not raw else raw in ("y", "yes")


def _find_pull_package() -> "Path | None":
    """Best-effort: locate a marketplace package that ships a pull_models.sh,
    so the wizard can tell the user a downloadable local model is available.
    Mirrors the walk in models_registry.pull_model. Returns the package dir or
    None (the common case in a bare checkout with no model package installed)."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        mp = ancestor / "workspace-marketplace"
        if mp.is_dir():
            for pkg in sorted(mp.iterdir()):
                if (pkg / "scripts" / "pull_models.sh").exists():
                    return pkg
    return None


def cmd_init(args: argparse.Namespace) -> int:
    yes = getattr(args, "yes", False)
    dry = getattr(args, "dry_run", False)
    out, inp = sys.stdout, sys.stdin
    home = LOG_ROOT_DEFAULT.parent          # ~/.workspace
    marker = home / "init.json"

    _wsay(out, "RVND init")
    _wsay(out, "=" * 52)
    if dry:
        _wsay(out, "(dry-run: nothing will be written)")
    if yes:
        _wsay(out, "(non-interactive: accepting recommended defaults)")
    if marker.exists() and not dry:
        _wsay(out, f"(already initialized — {marker}; re-running updates it)")

    # §1 Foundations
    _wsay(out, "\n§1  Foundations")
    _wsay(out, "-" * 52)
    _wsay(out, "RVND keeps its signing keys and signed logs under your home:")
    if not dry:
        (home / "keys").mkdir(parents=True, exist_ok=True)
        (home / "log").mkdir(parents=True, exist_ok=True)
    _wsay(out, f"  home:  {home}")
    _wsay(out, f"  keys:  {home / 'keys'}")
    _wsay(out, f"  logs:  {home / 'log'}   (per-folder Ed25519 audit chains)")

    # §2 Local-first promise (gate — declined = stop)
    _wsay(out, "\n§2  Local-first promise")
    _wsay(out, "-" * 52)
    for line in _LOCAL_FIRST_PROMISE.splitlines():
        _wsay(out, "  " + line)
    accepted = True if yes else _wask_yn(inp, out, "\n  I've read this and want to continue", True)
    if not accepted:
        _wsay(out, "\n  Not accepted — stopping. Nothing further was configured.")
        return 1
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # §3 Where workspaces live
    default_ws = str(Path.home() / "Documents" / "Workspaces")
    _wsay(out, "\n§3  Where your workspaces live")
    _wsay(out, "-" * 52)
    _wsay(out, "A workspace is a folder RVND governs. New ones default here (any")
    _wsay(out, "folder anywhere can still be a workspace).")
    ws_home = default_ws if yes else _wask(inp, out, "Default workspaces folder", default_ws)
    if dry:
        _wsay(out, f"  [dry-run] would set default: {ws_home}")
    else:
        try:
            from ..workspace_registry import bootstrap_default_workspace
            bootstrap_default_workspace(target=ws_home)
            _wsay(out, f"  ✓ default workspaces folder: {ws_home}")
        except Exception as e:  # noqa: BLE001 — report, don't abort the whole wizard
            _wsay(out, f"  (could not set the default: {e})")

    # §4 Privacy Lock (explain; the pattern pass is on by default)
    _wsay(out, "\n§4  Privacy Lock")
    _wsay(out, "-" * 52)
    _wsay(out, "The Lock inspects text leaving the local boundary for secrets and")
    _wsay(out, "personal data. The deterministic pattern pass is ON by default and")
    _wsay(out, "needs nothing; a semantic pass is optional and needs a local model")
    _wsay(out, "(the next step helps you add one).")
    try:
        from ..mcp_impl import lock_setup_status
        st = lock_setup_status()
        _wsay(out, f"  current: backend={st.get('backend_spec', '?')} mode={st.get('default_mode', '?')}")
    except Exception:  # noqa: BLE001 — status is informational only
        pass

    # §5 Local model (optional — powers the semantic Privacy Lock pass).
    # Honest + adaptive: if a workspace model is already registered we say so;
    # otherwise we show the real ways to add one and flag a downloadable model
    # package only when one is actually present. We don't auto-pull: a model
    # download is large, and there is no reliable "which id?" source in a bare
    # checkout — so we hand the user the exact command instead of guessing.
    _wsay(out, "\n§5  Local model  (optional — for the semantic Privacy Lock)")
    _wsay(out, "-" * 52)
    try:
        from ..models_registry import models_for_role
        registered = models_for_role("workspace")
    except Exception:  # noqa: BLE001 — the registry is informational here
        registered = []
    if registered:
        _wsay(out, f"  ✓ a local model is registered: {registered[0]}")
        _wsay(out, "    The semantic Lock pass can use it — nothing to do here.")
    else:
        _wsay(out, "  None yet. The pattern pass already protects you; a local")
        _wsay(out, "  model adds the optional semantic pass. Add one when ready:")
        pkg = _find_pull_package()
        _wsay(out, "    • download a packaged model:")
        _wsay(out, "        workspaces models pull <id>")
        if pkg:
            _wsay(out, f"      (a model package is available here: {pkg.name})")
        else:
            _wsay(out, "      (needs a marketplace model package installed)")
        _wsay(out, "    • register one you already have:")
        _wsay(out, "        workspaces models register --role workspace \\")
        _wsay(out, "          --model <id> --artifact-path <path>")
        _wsay(out, "    • or point at a local endpoint (BYOK):")
        _wsay(out, "        workspaces models config --local-url <url> --local-model <id>")
        _wsay(out, "  Details: docs/concepts/local-models.md")
        # A real choice, not just printed commands: interactively hand off to the
        # already-built guided model wizard (lock.run_wizard — the bundled/
        # download/pick-existing/skip flow). Interactive-only: --yes / --dry-run
        # can't prompt, so they keep the printed paths above.
        if not yes and not dry and _wask_yn(
                inp, out, "\n  Set one up now with the guided model wizard?", False):
            try:
                from ..lock import run_wizard
                res = run_wizard(stdin=inp, stdout=out)
                _wsay(out, "  ✓ model wizard finished."
                      if getattr(res, "completed", False)
                      else "  (model wizard did not finish — the commands above "
                           "set one up later)")
            except Exception as e:  # noqa: BLE001 — optional; never abort init
                _wsay(out, f"  (could not launch the model wizard: {e})")

    # §6 Skills (companions) — pin starter skills to the default workspace.
    # Reuses the SAME multi-select as `workspaces pin --interactive`
    # (_cmd_pin_interactive, a consumer of pinned_skills.load_companion_catalogue)
    # rather than a second parallel picker. Interactive-only.
    _wsay(out, "\n§6  Skills")
    _wsay(out, "-" * 52)
    _wsay(out, "Skills (companions) are capabilities you pin to a workspace — the")
    _wsay(out, "governed toolset an agent may use there.")
    if yes or dry:
        _wsay(out, "  Pin them anytime:  workspaces pin --interactive")
    elif _wask_yn(inp, out, "  Pick starter skills now?", False):
        try:
            pin_args = argparse.Namespace(
                filter=None, by="init-wizard",
                note="pinned during first-run setup",
                log_root=getattr(args, "log_root", None))
            _cmd_pin_interactive(Path(ws_home), pin_args)
        except Exception as e:  # noqa: BLE001 — optional; never abort init
            _wsay(out, f"  (skill picker unavailable: {e}; "
                       "use 'workspaces pin --interactive')")
    else:
        _wsay(out, "  Skipped — pin anytime:  workspaces pin --interactive")

    # §7 Human oversight (per-workspace; explain the ladder)
    _wsay(out, "\n§7  Human oversight")
    _wsay(out, "-" * 52)
    _wsay(out, "How much a person is in the loop, per workspace (loosest → strictest):")
    for i, (label, desc) in enumerate(_OVERSIGHT_LADDER):
        _wsay(out, f"  {i}  {label:11s} {desc}")
    _wsay(out, "Set it per workspace with:  workspaces oversight <level>")
    _wsay(out, "(the console's first-run wizard also tightens autonomy when you")
    _wsay(out, "create your first workspace; start at 'approve' if unsure).")

    # §8 Connect to an agent hub — the 99% path: without this, RVND is installed
    # but no agent can drive it. Offer to run the connector inline (idempotent,
    # self-detecting Claude Code / Codex) rather than only printing the command.
    _wsay(out, "\n§8  Connect to your AI agent")
    _wsay(out, "-" * 52)
    _wsay(out, "Let Claude Code / Codex drive RVND — registers the governance MCP")
    _wsay(out, "server and installs the governance skills (idempotent, self-detecting).")
    _hub = Path(__file__).resolve().parents[4] / "scripts" / "connect-agent-hub.sh"
    if yes or dry or not _hub.exists():
        _wsay(out, "  Connect anytime:  ./scripts/connect-agent-hub.sh")
    elif _wask_yn(inp, out, "  Connect your AI agent now?", False):
        try:
            import subprocess as _sp
            _sp.run(["bash", str(_hub), "--yes"], check=False)
        except Exception as e:  # noqa: BLE001 — optional; never abort init
            _wsay(out, f"  (couldn't run the connector: {e}; "
                       "run ./scripts/connect-agent-hub.sh)")
    else:
        _wsay(out, "  Skipped — connect anytime:  ./scripts/connect-agent-hub.sh")

    if not dry:
        marker.write_text(
            json.dumps({"initialized_at": ts, "workspaces_home": ws_home,
                        "promise_accepted": True}, indent=2) + "\n",
            encoding="utf-8")

    _wsay(out, "\nSetup complete.")
    _wsay(out, "Next — start the console:")
    _wsay(out, "  python app/serve.py       (or double-click 'app/Open Rvnd.command')")
    _wsay(out, "  → http://127.0.0.1:8799   (the first-run wizard walks your first workspace)")
    return 0


# ---------------------------------------------------------------------------
# uninstall — the mirror of `init`. Same sectioned, --yes / --dry-run feel,
# walking the setup in reverse. The guiding rule: nothing precious dies by
# accident. Your audit chains, signing keys, and governed workspaces are KEPT
# by default and removed only on an explicit, typed confirmation — never under
# --yes. What uninstall touches on its own is only RVND's own throwaway state
# (the init marker) and the guidance to disconnect the agent hub.
# ---------------------------------------------------------------------------

def _wconfirm_delete(inp: IO[str], w: IO[str], what: str) -> bool:
    """A stronger gate than y/N for an irreversible delete: the user must type
    the word DELETE. Empty / anything else = keep."""
    w.write(f"  To delete {what}, type DELETE (anything else keeps it): ")
    w.flush()
    return inp.readline().strip() == "DELETE"


def cmd_uninstall(args: argparse.Namespace) -> int:
    import shutil
    yes = getattr(args, "yes", False)
    dry = getattr(args, "dry_run", False)
    out, inp = sys.stdout, sys.stdin
    home = LOG_ROOT_DEFAULT.parent          # ~/.workspace
    marker = home / "init.json"
    ws_home = str(Path.home() / "Documents" / "Workspaces")
    if marker.exists():
        try:
            ws_home = json.loads(marker.read_text(encoding="utf-8")).get(
                "workspaces_home", ws_home)
        except Exception:  # noqa: BLE001 — a corrupt marker just falls back
            pass

    kept: list[str] = []
    removed: list[str] = []

    _wsay(out, "RVND uninstall")
    _wsay(out, "=" * 52)
    if dry:
        _wsay(out, "(dry-run: nothing will be removed)")
    if yes:
        _wsay(out, "(non-interactive: accepting SAFE defaults — your data is kept)")

    # §1 What this does
    _wsay(out, "\n§1  What uninstall does")
    _wsay(out, "-" * 52)
    _wsay(out, "It walks setup in reverse. Your governance record and your files")
    _wsay(out, "are KEPT by default; the only things removed without asking are")
    _wsay(out, "RVND's own throwaway state. Anything irreversible needs you to")
    _wsay(out, "type DELETE, and never happens under --yes.")

    # §2 Disconnect from the agent hub (mirror of connect-agent-hub.sh)
    _wsay(out, "\n§2  Disconnect from your AI agent")
    _wsay(out, "-" * 52)
    _wsay(out, "Undo what ./scripts/connect-agent-hub.sh registered. For Claude Code:")
    _wsay(out, "  claude mcp remove rvnd-governance")
    _wsay(out, "  claude plugin uninstall rvnd-governance")
    _wsay(out, "  claude plugin marketplace remove rvnd")
    _wsay(out, "(For Codex: remove the 'rvnd-governance' entry from your ~/.codex config.)")

    # §3 Privacy Lock (a SEPARATE tool — we don't touch it)
    _wsay(out, "\n§3  Privacy Lock")
    _wsay(out, "-" * 52)
    _wsay(out, "The Lock is a separate tool (agent-tool-lock) with its own config")
    _wsay(out, "under ~/.config/agent-tool-lock/. RVND won't remove another tool's")
    _wsay(out, "files — uninstall it on its own if you no longer want it.")

    # §4 RVND's own throwaway state — the init marker (safe to remove)
    _wsay(out, "\n§4  RVND setup marker")
    _wsay(out, "-" * 52)
    if marker.exists():
        if dry:
            _wsay(out, f"  [dry-run] would remove {marker}")
        else:
            marker.unlink()
            removed.append(str(marker))
            _wsay(out, f"  ✓ removed {marker}")
    else:
        _wsay(out, "  (no init marker found)")

    # §5 Audit chains + signing keys — PROTECTED. Typed confirm, never --yes.
    _wsay(out, "\n§5  Your audit chains and signing keys")
    _wsay(out, "-" * 52)
    _wsay(out, f"  {home}")
    _wsay(out, "This holds your Ed25519 signing keys, the per-folder tamper-evident")
    _wsay(out, "audit chains, and the workspace registry. Deleting it DESTROYS the")
    _wsay(out, "signed history — it cannot be recovered. Back it up first if unsure.")
    if not home.exists():
        _wsay(out, "  (nothing here to remove)")
    elif yes or dry:
        kept.append(str(home))
        _wsay(out, "  → KEEPING it" + (" (dry-run)" if dry else " (safe default)"))
    elif _wconfirm_delete(inp, out, f"the entire {home}"):
        shutil.rmtree(home, ignore_errors=True)
        removed.append(str(home))
        _wsay(out, f"  ✓ removed {home}")
    else:
        kept.append(str(home))
        _wsay(out, "  → kept.")

    # §6 Your governed workspaces — NEVER deleted by this tool.
    _wsay(out, "\n§6  Your workspaces")
    _wsay(out, "-" * 52)
    _wsay(out, f"  {ws_home}")
    _wsay(out, "These folders are your own content. RVND will not delete them —")
    _wsay(out, "remove them yourself if, and only if, you mean to.")
    kept.append(ws_home)

    # §7 The code itself
    _wsay(out, "\n§7  The RVND code")
    _wsay(out, "-" * 52)
    _wsay(out, "Finally, delete the cloned repo folder (the .venv lives inside it).")
    _wsay(out, "RVND can't delete the code it's running from — do this last, by hand.")

    # Summary
    _wsay(out, "\n" + "=" * 52)
    _wsay(out, "Summary")
    if removed:
        _wsay(out, "  removed:")
        for r in removed:
            _wsay(out, f"    - {r}")
    if kept:
        _wsay(out, "  kept:")
        for k in kept:
            _wsay(out, f"    - {k}")
    _wsay(out, "\nUninstall walk-through complete.")
    return 0


# ---------------------------------------------------------------------------
# guide — a categorized, human-readable map of every subcommand. `--help`
# dumps ~40 commands as a flat wall; this groups them by purpose and pulls
# each one-line description straight from the parser, so it can never drift
# from the real help. New commands not yet placed in a group still appear
# under "Other", so the guide can never silently omit a command.
# ---------------------------------------------------------------------------

# Ordered groups. Each command name maps to exactly one group; the order here
# is the order sections print in. Keep this in sync when adding a command —
# an unplaced command falls through to "Other" (and the test flags it).
_GUIDE_GROUPS: list[tuple[str, list[str]]] = [
    ("Setup & lifecycle",   ["init", "upgrade", "uninstall", "doctor", "status", "keys", "licence", "guide"]),
    ("Backup & recovery",   ["backup", "restore"]),
    ("Workspaces & memory", ["workspace", "folders", "list", "show", "watch", "ingest"]),
    ("Policy & governance", ["policy", "oversight", "discipline", "matrix", "lens",
                             "grounding", "mute", "unmute", "ask", "cross-workspace",
                             "shadow-scan", "tools"]),
    ("Privacy & sealing",   ["seal", "unseal", "lock", "unlock", "mirror"]),
    ("Skills & publishing", ["pin", "unpin", "list-pins", "resolve-skills",
                             "publish", "unpublish", "run-worker"]),
    ("Local models",        ["models"]),
    ("Data & erasure",      ["delete", "delete-document", "purge", "purge-document",
                             "erase", "erase-status", "audit-tail"]),
]


def _guide_help_map() -> dict[str, str]:
    """{command: one-line help} pulled from the live parser — the source of
    truth, so descriptions never drift from `--help`."""
    from . import build_parser
    parser = build_parser()
    sub = next((a for a in parser._actions
                if isinstance(a, argparse._SubParsersAction)), None)
    if sub is None:  # pragma: no cover — the parser always has subcommands
        return {}
    return {act.dest: (act.help or "") for act in sub._choices_actions}


def cmd_guide(args: argparse.Namespace) -> int:
    out = sys.stdout
    help_map = _guide_help_map()
    if getattr(args, "json", False):
        grouped = {title: {c: help_map[c] for c in cmds if c in help_map}
                   for title, cmds in _GUIDE_GROUPS}
        placed = {c for _, cmds in _GUIDE_GROUPS for c in cmds}
        other = {c: help_map[c] for c in help_map if c not in placed}
        if other:
            grouped["Other"] = other
        _wsay(out, json.dumps(grouped, indent=2))
        return 0

    width = max((len(c) for c in help_map), default=0)
    _wsay(out, "RVND — command guide")
    _wsay(out, "=" * 60)
    _wsay(out, "Run any command with -h for its own options, e.g. `workspaces init -h`.")

    placed: set[str] = set()
    for title, cmds in _GUIDE_GROUPS:
        rows = [(c, help_map[c]) for c in cmds if c in help_map]
        if not rows:
            continue
        _wsay(out, f"\n{title}")
        _wsay(out, "-" * 60)
        for name, htext in rows:
            _wsay(out, f"  {name:<{width}}  {htext}")
            placed.add(name)

    leftover = [(c, help_map[c]) for c in help_map if c not in placed]
    if leftover:
        _wsay(out, "\nOther")
        _wsay(out, "-" * 60)
        for name, htext in leftover:
            _wsay(out, f"  {name:<{width}}  {htext}")

    _wsay(out, "\nNew here? Start with:  workspaces init   → then open the console.")
    _wsay(out, "Leaving?               workspaces uninstall")
    return 0


# ---------------------------------------------------------------------------
# backup / restore — capture and recover ~/.workspace (keys + audit chains +
# registry). The one folder whose loss is unrecoverable. See ..backup.
# ---------------------------------------------------------------------------

def _prompt_new_passphrase(out: IO[str]) -> str:
    """Ask for a passphrase twice (not echoed). Env wins for non-interactive use.
    Returns "" if empty or mismatched."""
    import getpass
    env = os.environ.get("RVND_BACKUP_PASSPHRASE")
    if env:
        return env
    p1 = getpass.getpass("Passphrase to encrypt the backup: ")
    if not p1:
        return ""
    p2 = getpass.getpass("Confirm passphrase: ")
    if p1 != p2:
        _wsay(out, "  passphrases did not match.")
        return ""
    return p1


def cmd_backup(args: argparse.Namespace) -> int:
    from ..backup import create_backup, BackupError
    out = sys.stdout
    home = LOG_ROOT_DEFAULT.parent          # ~/.workspace
    encrypt = getattr(args, "encrypt", False)

    passphrase = None
    if encrypt:
        passphrase = _prompt_new_passphrase(out)
        if not passphrase:
            _wsay(out, "encryption requested but no passphrase given — aborting.")
            return 1

    if getattr(args, "out", None):
        out_path = Path(args.out).expanduser()
    else:
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        ext = "rvndbackup" if encrypt else "tar.gz"
        out_path = Path.home() / f"rvnd-backup-{ts}.{ext}"

    try:
        m = create_backup(home, out_path, passphrase=passphrase)
    except BackupError as e:
        _wsay(out, f"backup failed: {e}")
        return 1

    _wsay(out, "RVND backup")
    _wsay(out, "=" * 52)
    _wsay(out, f"  archive:  {m['archive']}")
    _wsay(out, f"  contents: {m['file_count']} files ({m['total_bytes'] // 1024} KB) from {home}")
    _wsay(out, f"  encrypted: {'yes (AES-256-GCM)' if m['encrypted'] else 'NO'}")
    if not m["encrypted"]:
        _wsay(out, "")
        _wsay(out, "  ! This archive contains your PRIVATE SIGNING KEYS in the clear.")
        _wsay(out, "    It is written owner-only (0600). For anything leaving this")
        _wsay(out, "    machine, re-run with --encrypt.")
    _wsay(out, "")
    _wsay(out, "  Keep it somewhere safe and off this machine. To recover:")
    _wsay(out, f"    workspaces restore {m['archive']}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from ..backup import restore_backup, read_manifest, is_encrypted_archive, BackupError
    out = sys.stdout
    home = LOG_ROOT_DEFAULT.parent
    archive = Path(args.archive).expanduser()
    dry = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    if not archive.is_file():
        _wsay(out, f"restore failed: no such archive: {archive}")
        return 1

    passphrase = None
    try:
        if is_encrypted_archive(archive):
            import getpass
            passphrase = os.environ.get("RVND_BACKUP_PASSPHRASE") or getpass.getpass(
                "Backup passphrase: ")
    except OSError as e:
        _wsay(out, f"restore failed: cannot read {archive}: {e}")
        return 1

    _wsay(out, "RVND restore")
    _wsay(out, "=" * 52)
    try:
        man = read_manifest(archive, passphrase=passphrase)
        _wsay(out, f"  from:     {man.get('created_at', '?')}"
                   f"  (rvnd {man.get('rvnd_version', '?')})")
        if man.get("hostname") or man.get("host_id"):
            _wsay(out, f"  host:     {man.get('hostname', '?')} ({man.get('host_id', '?')})")
        _wsay(out, f"  contents: {man.get('file_count', '?')} files")
    except BackupError as e:
        _wsay(out, f"restore failed: {e}")
        return 1

    try:
        res = restore_backup(archive, home, passphrase=passphrase, force=force, dry_run=dry)
    except BackupError as e:
        _wsay(out, f"restore failed: {e}")
        return 1

    _wsay(out, "-" * 52)
    if dry:
        _wsay(out, f"  [dry-run] would restore {res['members']} items into {home}")
        if res["existing"]:
            _wsay(out, "  [dry-run] the existing home would be moved aside (needs force).")
        return 0
    if res.get("moved_existing_to"):
        _wsay(out, f"  ✓ existing home moved aside → {res['moved_existing_to']}")
    _wsay(out, f"  ✓ restored {res['members']} items into {home}")
    _wsay(out, "")
    _wsay(out, "  Verify the chains:  workspaces doctor")
    _wsay(out, "  Note: on a different machine RVND keeps this record's keys for")
    _wsay(out, "  verification and mints a new host identity for future writes —")
    _wsay(out, "  the host change is itself recorded in the audit trail.")
    return 0


# ---------------------------------------------------------------------------
# upgrade — a version/schema-aware safe upgrade. RVND applies its migrations
# lazily and idempotently, so the real safety a user needs when moving to a new
# release is: (1) a backup taken first, (2) the audit chains verified intact
# BEFORE and AFTER, so a migration can never silently invalidate the signed
# record, and (3) a version stamp so a future release can detect the jump.
# ---------------------------------------------------------------------------

def _read_version_stamp(home: Path) -> "dict | None":
    f = home / "version.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None  # a corrupt stamp reads as "unknown" — the upgrade re-stamps it


def _write_version_stamp(home: Path, version: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (home / "version.json").write_text(
        json.dumps({"rvnd_version": version, "stamped_at": ts}, indent=2) + "\n",
        encoding="utf-8")


def _verify_all_chains(log_root: Path) -> dict[str, Any]:
    """Verify every per-folder audit chain. Returns {total, ok, broken:[paths]}."""
    from ..mutation_log import MutationLog
    folders = discover_folders(log_root)
    total = ok = 0
    broken: list[str] = []
    for path in folders:
        total += 1
        try:
            if MutationLog(path, log_root=log_root).verify_chain().ok:
                ok += 1
            else:
                broken.append(path)
        except Exception:  # noqa: BLE001 — an unreadable chain counts as broken
            broken.append(path)
    return {"total": total, "ok": ok, "broken": broken}


def _apply_migrations(out: IO[str]) -> list[str]:
    """Run the idempotent migrations explicitly. Each is a no-op if already done."""
    done: list[str] = []
    try:
        from .. import signing
        msg = signing.migrate_legacy_keypair_to_host_subdir()
        done.append(f"key layout: {msg}")
    except Exception as e:  # noqa: BLE001 — a migration that can't run is reported, not fatal
        done.append(f"key layout: skipped ({e})")
    for d in done:
        _wsay(out, f"    - {d}")
    return done


def cmd_upgrade(args: argparse.Namespace) -> int:
    from .._version import __version__ as code_ver
    out = sys.stdout
    home = LOG_ROOT_DEFAULT.parent
    log_root = LOG_ROOT_DEFAULT
    check = getattr(args, "check", False)
    skip_backup = getattr(args, "skip_backup", False)

    stamp = _read_version_stamp(home)
    prev = stamp.get("rvnd_version") if stamp else None

    _wsay(out, "RVND upgrade")
    _wsay(out, "=" * 52)
    _wsay(out, f"  installed version:  {code_ver}")
    _wsay(out, f"  data last stamped:  {prev or '(never — first upgrade run)'}")

    # 1. Verify BEFORE — establishes the baseline; pre-existing damage is not
    #    the upgrade's fault, but it must be surfaced.
    _wsay(out, "\n  verifying audit chains…")
    before = _verify_all_chains(log_root)
    line = f"  chains: {before['ok']}/{before['total']} intact"
    if before["broken"]:
        line += f"  ({len(before['broken'])} already broken before this run)"
    _wsay(out, line)

    up_needed = prev != code_ver

    if check:
        _wsay(out, "-" * 52)
        if not up_needed:
            _wsay(out, "  Up to date — your data matches the installed version.")
        else:
            _wsay(out, f"  An upgrade pass is advised: {prev or 'unstamped'} → {code_ver}.")
            _wsay(out, "  Run `workspaces upgrade` — it backs up and verifies first.")
        return 0

    if not up_needed and not before["broken"]:
        _wsay(out, "\n  Already current — nothing to migrate.")
        return 0

    # 2. Backup — the safety net. Refuse to migrate without one unless forced.
    if not skip_backup:
        from ..backup import create_backup, BackupError
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        bpath = Path.home() / f"rvnd-backup-preupgrade-{ts}.tar.gz"
        try:
            m = create_backup(home, bpath, passphrase=None)
            _wsay(out, f"\n  ✓ safety backup: {m['archive']}  ({m['file_count']} files)")
        except BackupError as e:
            _wsay(out, f"\n  backup failed: {e}")
            _wsay(out, "  Aborting — refusing to upgrade without a backup "
                       "(override with --skip-backup).")
            return 1
    else:
        _wsay(out, "\n  ! --skip-backup: proceeding WITHOUT a safety backup.")

    # 3. Apply the idempotent migrations.
    _wsay(out, "\n  applying migrations…")
    _apply_migrations(out)

    # 4. Verify AFTER — a chain intact before that is broken now means the
    #    upgrade damaged the record. Fail loudly; the backup is the way back.
    after = _verify_all_chains(log_root)
    newly_broken = sorted(set(after["broken"]) - set(before["broken"]))
    if newly_broken:
        _wsay(out, f"\n  ✗ FAILED — {len(newly_broken)} chain(s) intact before are now broken:")
        for p in newly_broken[:10]:
            _wsay(out, f"      {p}")
        _wsay(out, "  Your record is safe in the backup above — restore it with "
                   "`workspaces restore`. The version stamp was NOT advanced.")
        return 1
    _wsay(out, f"  chains after: {after['ok']}/{after['total']} intact — no new breakage.")

    # 5. Stamp — only after a clean, verified pass.
    _write_version_stamp(home, code_ver)
    _wsay(out, "-" * 52)
    _wsay(out, f"  ✓ upgraded to {code_ver} and stamped.")
    return 0


_DISPATCH = {
    "init": cmd_init,
    "uninstall": cmd_uninstall,
    "guide": cmd_guide,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "upgrade": cmd_upgrade,
    "matrix": cmd_matrix,
    "lens": cmd_lens,
    "grounding": cmd_grounding,
    "list": cmd_list,
    "show": cmd_show,
    "oversight": cmd_oversight,
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "shadow-scan": cmd_shadow_scan,
    "discipline": cmd_discipline,
    "delete": cmd_delete,
    "delete-document": cmd_delete_document,
    "purge": cmd_purge,
    "purge-document": cmd_purge_document,
    "audit-tail": cmd_audit_tail,
    "folders": cmd_folders,
    "licence": cmd_licence,
    "watch": cmd_watch,
    "ingest": cmd_ingest,
    "publish": cmd_publish,
    "unpublish": cmd_unpublish,
    "policy": cmd_policy,
    "pin": cmd_pin,
    "unpin": cmd_unpin,
    "list-pins": cmd_list_pins,
    "resolve-skills": cmd_resolve_skills,
    "run-worker": cmd_run_worker,
    "status": cmd_status,
    "cross-workspace": cmd_cross_workspace,
    "tools": cmd_tools,
    "mirror": cmd_mirror,
    "keys": cmd_keys,
    "erase": cmd_erase,
    "erase-status": cmd_erase_status,
    "workspace": cmd_workspace,
    "models": cmd_models,
    "doctor": cmd_doctor,
    "seal": cmd_seal,
    "unseal": cmd_unseal,
    "lock": cmd_lock,
    "unlock": cmd_unlock,
    "ask": cmd_ask,
}

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``workspaces`` — user-facing CLI for L0 memory operations.

Subcommands:

.. code-block:: text

    workspaces list [--folder PATH] [--state STATE] [--limit N]
    workspaces show <pair_id> [--folder PATH]
    workspaces delete <pair_id> [--folder PATH] [--yes]
    workspaces delete-document <document_path> [--folder PATH] [--yes]
    workspaces purge <pair_id> [--folder PATH] [--yes-i-mean-it]
    workspaces purge-document <document_path> [--folder PATH] [--yes-i-mean-it]
    workspaces audit-tail [--folder PATH] [--limit N]
    workspaces folders                                  # list known folders

Folder resolution priority (matches WorkspaceMemory):
    1. --folder PATH
    2. WORKSPACE_FOLDER_CONTEXT env var
    3. Contextvar (rarely set from a CLI; useful for embedded uses)

Exit codes:
    0   success
    1   pair / document not found
    2   user aborted at confirmation
    3   invalid arguments / missing folder context
    4   internal error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Any

from ..folder_context import (
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


# ---------------------------------------------------------------------------
# Confirmation helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: show
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: delete (logical)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: delete-document (logical cascade)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: purge (physical, irreversible)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: purge-document (physical cascade)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: audit-tail
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: folders (list folders with logs)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: watch (poll the Inbox)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: ingest (one-off file ingest)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# argparse + entry point
# ---------------------------------------------------------------------------




def _add_folder_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--folder",
        default=None,
        help="Folder context (path). Default: WORKSPACE_FOLDER_CONTEXT env var.",
    )


def _workspaces_version() -> str:
    """Installed package version, for ``workspaces --version`` (DoD ship gate 1).

    The import package is ``workspaces`` but the distribution is ``rvnd`` (the product
    name), so resolve either: try the package name, then the distribution that ships it."""
    from importlib.metadata import PackageNotFoundError, version
    for dist in ("workspaces", "rvnd"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "unknown (package metadata not found)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workspaces",
        description="Workspaces memory operations — list / show / delete / purge / audit / pin / workflow.",
    )
    parser.add_argument(
        "--version", action="version", version=f"workspaces {_workspaces_version()}",
    )
    parser.add_argument(
        "--log-root",
        default=None,
        help=f"Override log root (default: {LOG_ROOT_DEFAULT}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init — first-run setup wizard
    p_init = sub.add_parser("init", help="First-run setup wizard (foundations, "
                                         "workspaces folder, Lock, local model, oversight, agent hub).")
    p_init.add_argument("--yes", "-y", action="store_true",
                        help="non-interactive — accept all recommended defaults")
    p_init.add_argument("--dry-run", action="store_true",
                        help="print the plan but write nothing")

    p_uninstall = sub.add_parser("uninstall", help="Guided removal (mirror of init). "
                                                   "Keeps your data unless you explicitly delete it.")
    p_uninstall.add_argument("--yes", "-y", action="store_true",
                             help="non-interactive — accept SAFE defaults (your data is kept)")
    p_uninstall.add_argument("--dry-run", action="store_true",
                             help="print what would happen but remove nothing")

    p_guide = sub.add_parser("guide", help="Categorized map of every command, "
                                           "grouped by what you're trying to do.")
    p_guide.add_argument("--json", action="store_true",
                         help="machine-readable grouped output")

    p_backup = sub.add_parser("backup", help="Archive ~/.workspace (keys + audit "
                                             "chains + registry) — the irreplaceable record.")
    p_backup.add_argument("--out", default=None,
                          help="Archive path (default: ~/rvnd-backup-<timestamp>).")
    p_backup.add_argument("--encrypt", action="store_true",
                          help="Encrypt with a passphrase (recommended for off-machine copies).")

    p_restore = sub.add_parser("restore", help="Restore ~/.workspace from a backup archive.")
    p_restore.add_argument("archive", help="Path to a backup archive.")
    p_restore.add_argument("--force", action="store_true",
                           help="Restore over an existing home (moved aside to .bak first).")
    p_restore.add_argument("--dry-run", action="store_true",
                           help="Show the manifest and validate, but write nothing.")

    # list
    p_list = sub.add_parser("list", help="List live pairs in scope.")
    _add_folder_arg(p_list)
    p_list.add_argument("--state", default=None,
                        help="Filter by lifecycle state (e.g. 'live').")
    p_list.add_argument("--limit", type=int, default=50,
                        help="Max pairs to show. Default: 50.")

    # show
    p_show = sub.add_parser("show", help="Print one pair as JSON.")
    _add_folder_arg(p_show)
    p_show.add_argument("pair_id", help="The pair's stable id (sha256:...).")

    # delete
    p_del = sub.add_parser("delete", help="Logical delete (recoverable from audit log).")
    _add_folder_arg(p_del)
    p_del.add_argument("pair_id", help="The pair's stable id (sha256:...).")
    p_del.add_argument("--yes", action="store_true",
                       help="Skip the y/N prompt.")

    # delete-document
    p_dd = sub.add_parser("delete-document",
                          help="Logical cascade-delete of every pair derived from a document.")
    _add_folder_arg(p_dd)
    p_dd.add_argument("document_path", help="Source document path to cascade-delete.")
    p_dd.add_argument("--yes", action="store_true", help="Skip the y/N prompt.")

    # purge
    p_purge = sub.add_parser("purge",
                             help="PHYSICAL erasure (irreversible — for GDPR Art. 17).")
    _add_folder_arg(p_purge)
    p_purge.add_argument("pair_id", help="The pair's stable id (sha256:...).")
    p_purge.add_argument(
        "--legal-basis", dest="legal_basis", default="",
        help="GDPR Art. 17(1) ground: art_17_1_a..art_17_1_f. Required (B1).",
    )
    p_purge.add_argument(
        "--requester-ref", dest="requester_ref", default="",
        help="Opaque reference to the requesting data subject. Required.",
    )
    p_purge.add_argument(
        "--reason", default="",
        help="Free-text reason recorded in the on-chain tombstone. Required.",
    )
    p_purge.add_argument("--yes-i-mean-it", action="store_true",
                         help="Confirm — without this, the command prompts.")

    # purge-document
    p_pd = sub.add_parser("purge-document",
                          help="PHYSICAL cascade-erasure of every pair from a document (irreversible).")
    _add_folder_arg(p_pd)
    p_pd.add_argument("document_path", help="Source document path to purge.")
    p_pd.add_argument(
        "--legal-basis", dest="legal_basis", default="",
        help="GDPR Art. 17(1) ground: art_17_1_a..art_17_1_f. Required (B1).",
    )
    p_pd.add_argument(
        "--requester-ref", dest="requester_ref", default="",
        help="Opaque reference to the requesting data subject. Required.",
    )
    p_pd.add_argument(
        "--reason", default="",
        help="Free-text reason recorded in each on-chain tombstone. Required.",
    )
    p_pd.add_argument("--yes-i-mean-it", action="store_true",
                      help="Confirm.")

    # audit-tail
    p_audit = sub.add_parser("audit-tail",
                             help="Print the tail of the folder's mutation log.")
    _add_folder_arg(p_audit)
    p_audit.add_argument("--limit", type=int, default=20,
                         help="How many recent events to show. Default: 20.")

    # folders
    sub.add_parser("folders", help="List all folder paths that have logs.")

    # watch
    p_watch = sub.add_parser("watch",
                             help="Watch a workspace folder and ingest new files. "
                                  "Scans Inbox/ if it exists, otherwise the folder root.")
    _add_folder_arg(p_watch)
    p_watch.add_argument("--once", action="store_true",
                         help="Scan once and exit (don't loop).")
    p_watch.add_argument("--interval", type=float, default=2.0,
                         help="Poll interval in seconds when looping. Default: 2.0.")
    p_watch.add_argument("--recursive", action="store_true",
                         help="Also scan sub-folders. Each sub-folder receives "
                              "its files into ITS OWN workspace memory (per the "
                              "asymmetric hierarchical rule).")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest one file into the folder's memory.")
    _add_folder_arg(p_ingest)
    p_ingest.add_argument("file_path", help="Path to the file to ingest.")

    # publish (B5)
    p_pub = sub.add_parser(
        "publish",
        help="Mark a pair as DISTRIBUTED downward to descendant folders.",
    )
    _add_folder_arg(p_pub)
    p_pub.add_argument("pair_id", help="The pair to publish.")
    p_pub.add_argument("--yes", action="store_true", help="Skip confirmation.")

    # unpublish (B5)
    p_unpub = sub.add_parser(
        "unpublish",
        help="Revoke a previously-published pair from descendants (logical).",
    )
    _add_folder_arg(p_unpub)
    p_unpub.add_argument("pair_id", help="The pair to unpublish.")
    p_unpub.add_argument("--yes", action="store_true", help="Skip confirmation.")

    # policy (B6) — show / enable / disable
    p_policy = sub.add_parser(
        "policy",
        help="Per-folder policy: enable / disable Privacy Lock + Oversight.",
    )
    pol_sub = p_policy.add_subparsers(dest="policy_command", required=True)

    p_pol_show = pol_sub.add_parser("show", help="Show the folder's policy.")
    _add_folder_arg(p_pol_show)

    p_pol_ds = pol_sub.add_parser(
        "disable-lock",
        help="DISABLE Privacy Lock for this folder. Requires --i-accept-the-risk.",
    )
    _add_folder_arg(p_pol_ds)
    p_pol_ds.add_argument("--i-accept-the-risk", action="store_true",
                          dest="i_accept_the_risk",
                          help="Acknowledge the disclaimer + proceed.")
    p_pol_ds.add_argument("--accepted-by", dest="accepted_by", default="user",
                          help="Actor identifier (default: 'user').")
    p_pol_ds.add_argument("--reason", default="", help="Free-text reason.")

    p_pol_es = pol_sub.add_parser("enable-lock",
                                  help="Re-enable Privacy Lock.")
    _add_folder_arg(p_pol_es)
    p_pol_es.add_argument("--actor", default="user",
                          help="Actor identifier (default: 'user').")

    p_pol_do = pol_sub.add_parser(
        "disable-oversight",
        help="DISABLE Oversight for this folder. Requires --i-accept-the-risk.",
    )
    _add_folder_arg(p_pol_do)
    p_pol_do.add_argument("--i-accept-the-risk", action="store_true",
                          dest="i_accept_the_risk",
                          help="Acknowledge the disclaimer + proceed.")
    p_pol_do.add_argument("--accepted-by", dest="accepted_by", default="user",
                          help="Actor identifier (default: 'user').")
    p_pol_do.add_argument("--reason", default="", help="Free-text reason.")

    p_pol_eo = pol_sub.add_parser("enable-oversight",
                                  help="Re-enable Oversight.")
    _add_folder_arg(p_pol_eo)
    p_pol_eo.add_argument("--actor", default="user",
                          help="Actor identifier (default: 'user').")

    p_pol_ed = pol_sub.add_parser("enable-discipline",
                                  help="Enable the discipline gate (third dial).")
    _add_folder_arg(p_pol_ed)
    p_pol_ed.add_argument("--manifest", default="",
                          help="Rule manifest path (relative to folder or absolute). "
                               "Empty uses the built-in default.")
    p_pol_ed.add_argument("--actor", default="user",
                          help="Actor identifier (default: 'user').")

    p_pol_dd = pol_sub.add_parser("disable-discipline",
                                  help="Disable the discipline gate. No disclaimer.")
    _add_folder_arg(p_pol_dd)
    p_pol_dd.add_argument("--actor", default="user",
                          help="Actor identifier (default: 'user').")

    # discipline (third dial) — audit / diff / check
    # Policy matrix (autonomy grade × oversight level → traffic light) — plan layer.
    p_mx = sub.add_parser(
        "matrix", help="The autonomy×oversight policy matrix (traffic-light grid).")
    mx_sub = p_mx.add_subparsers(dest="matrix_command", required=True)
    p_mx_show = mx_sub.add_parser("show", help="Print the matrix grid.")
    _add_folder_arg(p_mx_show)
    p_mx_set = mx_sub.add_parser(
        "set", help="Set one cell: <grade> <oversight> <go|ask|block>.")
    _add_folder_arg(p_mx_set)
    p_mx_set.add_argument("grade"); p_mx_set.add_argument("oversight")
    p_mx_set.add_argument("light")
    p_mx_row = mx_sub.add_parser(
        "set-row", help="Bulk: set a whole oversight row: <oversight> <light>.")
    _add_folder_arg(p_mx_row)
    p_mx_row.add_argument("oversight"); p_mx_row.add_argument("light")
    p_mx_col = mx_sub.add_parser(
        "set-col", help="Bulk: set a whole grade column: <grade> <light>.")
    _add_folder_arg(p_mx_col)
    p_mx_col.add_argument("grade"); p_mx_col.add_argument("light")
    p_mx_reset = mx_sub.add_parser(
        "reset", help="Reset to the recommended anti-diagonal default.")
    _add_folder_arg(p_mx_reset)
    p_mx_exp = mx_sub.add_parser(
        "explain", help="Explain a cell's effective light + why (the floors/gate).")
    _add_folder_arg(p_mx_exp)
    p_mx_exp.add_argument("grade"); p_mx_exp.add_argument("oversight")
    p_mx_exp.add_argument("--privacy", default=None,
                          help="privacy class: public|pseudonymous|sensitive|regulated")
    p_mx_exp.add_argument("--verdict", default=None,
                          help="gate verdict: GO|CONDITIONAL|NO-GO")

    # In-vivo Lens (USP-2): govern what the agent LEARNS, not just what it does.
    p_lens = sub.add_parser(
        "lens", help="In-vivo Lens: admission / precedent / update-budget / log.")
    lens_sub = p_lens.add_subparsers(dest="lens_command", required=True)
    p_lens_log = lens_sub.add_parser(
        "log", help="Show the learning-admission feed (admit/hold/reject).")
    _add_folder_arg(p_lens_log)
    p_lens_log.add_argument("--limit", type=int, default=50)
    for _name, _help in (("classify", "admit/hold/reject one learning object"),
                         ("select", "pick the applicable precedent (stare decisis)"),
                         ("budget", "update-budget spent vs cap"),
                         ("precedent-declare", "declare a precedent learnable"),
                         ("precedent-revoke", "revoke a precedent")):
        _sp = lens_sub.add_parser(_name, help=_help)
        _sp.add_argument("--json", required=True,
                         help="params as a JSON object (see workspace_lens op help)")
    p_lens_prec = lens_sub.add_parser(
        "precedents", help="List the live precedent shelf (replayed from the log).")
    _add_folder_arg(p_lens_prec)
    p_lens_prec.add_argument("--include-inactive", action="store_true")
    p_lens_cap = lens_sub.add_parser(
        "cap", help="Get the update-budget cap, or set it with --set.")
    _add_folder_arg(p_lens_cap)
    p_lens_cap.add_argument("--set", type=float, default=None, dest="set_cap",
                            help="set the cap (> 0); omit to just read it")

    # Grounding (output review): no citation, no claim. The output-review feed +
    # attribution coverage — the same story as workspace_grounder oversight.feed.
    p_gnd = sub.add_parser(
        "grounding", help="Output review: grounded/flagged/stopped feed + coverage.")
    gnd_sub = p_gnd.add_subparsers(dest="grounding_command", required=True)
    p_gnd_feed = gnd_sub.add_parser(
        "feed", help="The output-review feed (grounded / flagged / stopped).")
    _add_folder_arg(p_gnd_feed)
    p_gnd_feed.add_argument("--limit", type=int, default=50)
    p_gnd_cov = gnd_sub.add_parser(
        "coverage", help="Attribution coverage: claims by status, missing creators.")
    _add_folder_arg(p_gnd_cov)

    p_disc = sub.add_parser(
        "discipline",
        help="Run the discipline gate over a folder (code/text conformance).",
    )
    disc_sub = p_disc.add_subparsers(dest="discipline_command", required=True)

    def _add_disc_common(pp: argparse.ArgumentParser) -> None:
        _add_folder_arg(pp)
        pp.add_argument("--manifest", default=None,
                        help="Override rule manifest (else the folder's policy "
                             "manifest, else the built-in default).")
        pp.add_argument("--strict", action="store_true",
                        help="Treat warnings as failures (non-zero exit).")
        pp.add_argument("--no-audit", action="store_true", dest="no_audit",
                        help="Do not write the run to the audit chain.")

    p_disc_audit = disc_sub.add_parser("audit", help="Scan the whole folder tree.")
    _add_disc_common(p_disc_audit)
    p_disc_diff = disc_sub.add_parser("diff", help="Scan only changed + new files vs HEAD.")
    _add_disc_common(p_disc_diff)
    p_disc_check = disc_sub.add_parser("check", help="Scan an explicit list of files.")
    _add_disc_common(p_disc_check)
    p_disc_check.add_argument("files", nargs="+", help="Files to check.")

    # pin (#145) — pin a skill to a folder
    p_pin = sub.add_parser(
        "pin",
        help="Pin a skill (fully-qualified id) to this folder. "
             "Use --interactive to browse and select from the catalogue.",
    )
    _add_folder_arg(p_pin)
    p_pin.add_argument("skill_id", nargs="?", default=None,
                        help="Skill id, e.g. 'ai-governance-watch:newsletter-research'. "
                             "Omit when using --interactive.")
    p_pin.add_argument("--by", default="user",
                        help="Who pinned it (default: 'user').")
    p_pin.add_argument("--note", default="", help="Free-text note.")
    p_pin.add_argument("--interactive", "-i", action="store_true",
                        help="Browse installed skills and pick what to pin. "
                             "Reads the companion catalogue; no skill_id required.")
    p_pin.add_argument("--filter", default="",
                        help="With --interactive: pre-filter the catalogue by "
                             "case-insensitive substring on plugin or skill id.")

    # unpin (#145)
    p_unpin = sub.add_parser(
        "unpin",
        help="Unpin a skill from this folder.",
    )
    _add_folder_arg(p_unpin)
    p_unpin.add_argument("skill_id", help="Skill id to unpin.")

    # list-pins (#145) — pins on THIS folder (no ancestor walk)
    p_lp = sub.add_parser(
        "list-pins",
        help="List skills pinned to this folder only (no ancestor walk).",
    )
    _add_folder_arg(p_lp)

    # resolve-skills (#145) — effective set including ancestor pins
    p_rs = sub.add_parser(
        "resolve-skills",
        help="Resolve effective pinned-skill set for this folder (self + ancestors).",
    )
    _add_folder_arg(p_rs)
    p_rs.add_argument("--query", default="",
                       help="Optional case-insensitive substring filter on skill id.")
    p_rs.add_argument("--no-ancestors", action="store_true",
                       help="Skip ancestor walk — return only pins on this folder.")

    # run-worker — background workflow drain loop
    p_worker = sub.add_parser(
        "run-worker",
        help="Drain the workflow queue. Survives MCP restarts; user starts manually.",
    )
    p_worker.add_argument("--worker-id", default="",
                          help="Worker identifier (default: worker-<host>-<pid>).")
    p_worker.add_argument("--lease-seconds", type=int, default=60,
                          help="Initial lease length per claimed run. Default: 60.")
    p_worker.add_argument("--interval", type=float, default=2.0,
                          help="Sleep seconds when the queue is empty. Default: 2.0.")
    p_worker.add_argument("--once", action="store_true",
                          help="Drain at most one run and exit.")
    p_worker.add_argument("--max-iterations", type=int, default=0,
                          help="Stop after N iterations (0 = unlimited).")
    p_worker.add_argument("--status", action="store_true",
                          help="Print queue snapshot and exit (no draining).")
    p_worker.add_argument("--verbose", action="store_true",
                          help="DEBUG-level worker logging.")

    # ---------------------------------------------------------------------
    # status — folder-aware overview (0.6.7+)
    # ---------------------------------------------------------------------
    p_status = sub.add_parser(
        "status",
        help="Folder-aware overview: policy, pinned skills, recent events, "
             "chain verification. The 'what's happening here' entry point.",
    )
    _add_folder_arg(p_status)
    p_status.add_argument("--events", type=int, default=10,
                           help="Number of recent events to show. Default: 10.")
    p_status.add_argument("--json", action="store_true",
                           help="Output as JSON instead of human-readable text.")

    # ---------------------------------------------------------------------
    # seal / unseal — at-rest encryption of a folder's memory
    # ---------------------------------------------------------------------
    p_seal = sub.add_parser(
        "seal",
        help="Encrypt this folder's memory at rest with a passphrase; removes "
             "the plaintext until you unseal.",
    )
    _add_folder_arg(p_seal)
    p_seal.add_argument(
        "--passphrase", default=None,
        help="Passphrase. If omitted, read from WORKSPACE_SEAL_PASSPHRASE or prompted.",
    )
    p_unseal = sub.add_parser(
        "unseal",
        help="Decrypt this folder's sealed memory back to plaintext with the passphrase.",
    )
    _add_folder_arg(p_unseal)
    p_unseal.add_argument(
        "--passphrase", default=None,
        help="Passphrase. If omitted, read from WORKSPACE_SEAL_PASSPHRASE or prompted.",
    )

    # ---------------------------------------------------------------------
    # lock / unlock — governance-first verbs: at-rest seal + egress screening
    # ---------------------------------------------------------------------
    p_lock = sub.add_parser(
        "lock",
        help="Lock a workspace: seal its memory at rest (AES-256-GCM) and turn on "
             "egress screening so only approved text leaves. Governance verb "
             "over `seal` + Privacy Lock.",
    )
    _add_folder_arg(p_lock)
    p_lock.add_argument("--passphrase", default=None,
                        help="Seal passphrase. If omitted, read from "
                             "WORKSPACE_SEAL_PASSPHRASE or prompted.")
    p_lock.add_argument("--no-seal", action="store_true",
                        help="Turn on egress screening only; do not seal at rest.")
    p_lock.add_argument("--no-shield", action="store_true",
                        help="Seal at rest only; do not change egress screening.")
    p_lock.add_argument("--actor", default="user", help="Who is locking (audit).")

    p_unlock = sub.add_parser(
        "unlock",
        help="Unlock a workspace: decrypt its sealed memory back to disk (working "
             "state) with the passphrase. Egress screening is left unchanged.",
    )
    _add_folder_arg(p_unlock)
    p_unlock.add_argument("--passphrase", default=None,
                          help="Seal passphrase. If omitted, read from "
                               "WORKSPACE_SEAL_PASSPHRASE or prompted.")

    # ---------------------------------------------------------------------
    # oversight / mute / unmute — the oversight dial as top-level verbs
    # ---------------------------------------------------------------------
    p_ovr = sub.add_parser(
        "oversight",
        help="Show or set a workspace's oversight dial. With no LEVEL, shows the "
             "current position; with a LEVEL, sets the default "
             "(autonomous|notify|review|approve|supervised|manual).",
    )
    _add_folder_arg(p_ovr)
    p_ovr.add_argument("level", nargs="?", default=None, choices=OVERSIGHT_LEVELS,
                       help="New default level. Omit to show the current one.")
    p_ovr.add_argument("--actor", default="user", help="Who is setting it (audit).")

    p_mute = sub.add_parser(
        "mute",
        help="Mute Oversight prompts for this workspace (the audit chain keeps "
             "recording). Lowers a protection, so requires --i-accept-the-risk.",
    )
    _add_folder_arg(p_mute)
    p_mute.add_argument("--i-accept-the-risk", action="store_true",
                        dest="i_accept_the_risk",
                        help="Acknowledge the disclaimer + proceed.")
    p_mute.add_argument("--accepted-by", dest="accepted_by", default="user",
                        help="Actor identifier (default: 'user').")
    p_mute.add_argument("--reason", default="", help="Free-text reason.")

    p_unmute = sub.add_parser(
        "unmute", help="Re-enable Oversight prompts for this workspace.")
    _add_folder_arg(p_unmute)
    p_unmute.add_argument("--actor", default="user", help="Actor (audit).")

    # ---------------------------------------------------------------------
    # ask — one governed chat turn over a workspace (the CLI face of /Workspaces)
    # ---------------------------------------------------------------------
    p_ask = sub.add_parser(
        "ask",
        help="One governed chat turn over a workspace: scope = this workspace + its "
             "sub-workspaces (access = workspace), route to companions (gated), generate "
             "local-first, ground only when the turn rests on works.",
    )
    _add_folder_arg(p_ask)
    p_ask.add_argument("query", help="Your question or task for this workspace.")
    p_ask.add_argument("--max-tokens", type=int, default=512)
    p_ask.add_argument("--json", action="store_true",
                       help="Output as JSON instead of human-readable text.")

    # ---------------------------------------------------------------------
    # cross-workspace — governed lateral read from source workspaces into a target
    # ---------------------------------------------------------------------
    p_xc = sub.add_parser(
        "cross-workspace",
        help="Govern a lateral read from one or more source workspaces into this "
             "(target) workspace. Each crossing is gated (GO/CONDITIONAL/NO-GO) and "
             "recorded on the target's signed chain with provenance.",
    )
    _add_folder_arg(p_xc)
    p_xc.add_argument("--source", action="append", default=[], metavar="FOLDER",
                      help="A source workspace to read from. Repeatable.")
    p_xc.add_argument("--role", choices=["source", "companion"], default="source",
                      help="source: the workspace feeds a companion. companion: the "
                           "companion is applied to the workspace.")
    p_xc.add_argument("--grade", default="L2",
                      help="Autonomy grade for the crossing (L0..L4). Default L2.")
    p_xc.add_argument("--json", action="store_true",
                      help="Output as JSON instead of human-readable text.")

    # ---------------------------------------------------------------------
    # shadow-scan — classify recorded cross-workspace crossings (detective)
    # ---------------------------------------------------------------------
    p_shadow = sub.add_parser(
        "shadow-scan",
        help="Classify this workspace's recorded cross-workspace crossings: surface "
             "shadow flows (no declared workflow), crossings needing sign-off, "
             "blocked attempts, and high fan-in. Read-only; never blocks.",
    )
    _add_folder_arg(p_shadow)
    p_shadow.add_argument("--high-fan-in", type=int, default=3,
                          help="Flag when this many distinct sources feed the workspace.")
    p_shadow.add_argument("--json", action="store_true",
                          help="Output as JSON instead of human-readable text.")

    # ---------------------------------------------------------------------
    # tools — MCP tool surface discoverability (0.6.7+)
    # ---------------------------------------------------------------------
    p_tools = sub.add_parser(
        "tools",
        help="List MCP tools grouped by domain prefix. Discoverability for "
             "the ~78-tool surface.",
    )
    p_tools.add_argument("--filter", default="",
                          help="Show only tools whose name contains this substring.")
    p_tools.add_argument("--describe", default="",
                          help="Print the docstring of a single tool by name.")

    # ---------------------------------------------------------------------
    # mirror — folder mirrors (F1, 0.6.8). Lock + Oversight outputs
    # as user-reviewable files on disk.
    # ---------------------------------------------------------------------
    p_mirror = sub.add_parser(
        "mirror",
        help="Folder mirrors: Lock-cleaned + Oversight-approved files.",
    )
    mirror_sub = p_mirror.add_subparsers(dest="mirror_command", required=True)

    p_mir_gen = mirror_sub.add_parser(
        "generate",
        help="Run Lock over a source file; write the cleaned mirror "
             "+ spans sidecar; emit an audit event.",
    )
    _add_folder_arg(p_mir_gen)
    p_mir_gen.add_argument("source_path",
                            help="Absolute path to the source file.")
    p_mir_gen.add_argument("--actor", default="system:mirror",
                            help="Actor identifier for the audit event.")

    p_mir_app = mirror_sub.add_parser(
        "approve",
        help="Copy a Lock mirror into the Oversight surface as an "
             "approved snapshot; emit an audit event.",
    )
    _add_folder_arg(p_mir_app)
    p_mir_app.add_argument("mirror_path",
                            help="Absolute path to the .cleaned.md mirror.")
    p_mir_app.add_argument("--approver", required=True,
                            help="Approver identifier (recorded on-chain).")

    p_mir_list = mirror_sub.add_parser(
        "list",
        help="List every mirror present under <folder>/mirrors/.",
    )
    _add_folder_arg(p_mir_list)
    p_mir_list.add_argument("--kind", default="",
                             choices=("", "lock", "oversight"),
                             help="Filter by mirror kind.")

    # B9.3 — oversight-editor verbs
    p_mir_edit = mirror_sub.add_parser(
        "edit",
        help="Open a revision draft for a Lock mirror (acquires the lock).",
    )
    _add_folder_arg(p_mir_edit)
    p_mir_edit.add_argument("mirror_path",
                              help="Path to the lock .cleaned.md (or .approved.md).")
    p_mir_edit.add_argument("--actor", default="system:editor")

    p_mir_revs = mirror_sub.add_parser(
        "revisions",
        help="List revisions of a draft.",
    )
    _add_folder_arg(p_mir_revs)
    p_mir_revs.add_argument("mirror_path")

    p_mir_diff = mirror_sub.add_parser(
        "diff",
        help="Unified diff between two revisions of a draft.",
    )
    _add_folder_arg(p_mir_diff)
    p_mir_diff.add_argument("mirror_path")
    p_mir_diff.add_argument("--from", dest="from_rev", type=int, required=True)
    p_mir_diff.add_argument("--to", dest="to_rev", type=int, default=None)

    p_mir_disc = mirror_sub.add_parser(
        "discard",
        help="Discard the current draft and release the lock.",
    )
    _add_folder_arg(p_mir_disc)
    p_mir_disc.add_argument("mirror_path")
    p_mir_disc.add_argument("--actor", default="system:editor")
    p_mir_disc.add_argument("--reason", default="")

    # ---------------------------------------------------------------------
    # keys — key-custody operations (0.6.8 D2 / B4)
    # ---------------------------------------------------------------------
    p_keys = sub.add_parser(
        "keys",
        help="Key-custody operations: initialise the controller co-signing key, "
             "show fingerprints. Per-host identity keys are auto-generated; only "
             "the controller key needs explicit initialisation.",
    )
    keys_sub = p_keys.add_subparsers(dest="keys_command", required=True)

    p_keys_ic = keys_sub.add_parser(
        "init-controller",
        help="Initialise the controller co-signing keypair. Idempotent — "
             "running twice is a no-op. Prints the controller pubkey fingerprint.",
    )

    # ---------------------------------------------------------------------
    # erase — first-class GDPR Art. 17 verb (B5, 0.6.8)
    # ---------------------------------------------------------------------
    p_erase = sub.add_parser(
        "erase",
        help="Erase every reference to a subject — sweep + composite "
             "tombstone + forgotten_subjects ledger. Three-state intake "
             "(D4): `erase request ...` for ticket intake; default "
             "`erase --subject ...` runs sweep+execute.",
    )
    erase_sub = p_erase.add_subparsers(dest="erase_command", required=False)

    # default form (no subcommand) → execute
    p_erase.add_argument("--subject", default="",
                          help="Subject text to erase. PII name, identifier, etc.")
    _add_folder_arg(p_erase)
    p_erase.add_argument("--cascade", action="store_true",
                          help="Also sweep + purge descendant folders.")
    p_erase.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="Preview only; no writes.")
    p_erase.add_argument(
        "--legal-basis", dest="legal_basis", default="",
        help="GDPR Art. 17(1) ground: art_17_1_a..art_17_1_f. Required.",
    )
    p_erase.add_argument(
        "--requester-ref", dest="requester_ref", default="",
        help="Opaque reference to the requesting data subject. Required.",
    )
    p_erase.add_argument(
        "--reason", default="",
        help="Free-text reason recorded on the composite tombstone. Required.",
    )

    # `workspaces erase request --subject ... --requester-ref ... --reason ...`
    p_erase_req = erase_sub.add_parser(
        "request",
        help="Two-phase intake: write ERASURE_REQUESTED event. No purge.",
    )
    _add_folder_arg(p_erase_req)
    p_erase_req.add_argument("--subject", required=True,
                              help="Subject text the requester wants erased.")
    p_erase_req.add_argument("--requester-ref", dest="requester_ref",
                              required=True,
                              help="Opaque reference to the requesting subject.")
    p_erase_req.add_argument("--reason", required=True,
                              help="Free-text reason recorded in the audit event.")

    # erase-status
    p_erase_status = sub.add_parser(
        "erase-status",
        help="Show the cascade manifest for a previously-issued erase request.",
    )
    _add_folder_arg(p_erase_status)
    p_erase_status.add_argument("--request-id", dest="request_id", required=True,
                                 help="Request id returned by `erase request` or `erase`.")

    # ---------------------------------------------------------------------
    # workspace — maintenance verbs (B7 / 0.6.8)
    # ---------------------------------------------------------------------
    p_ws = sub.add_parser(
        "workspace",
        help="Workspace maintenance: migrate a renamed/moved folder log, "
             "garbage-collect orphan log dirs.",
    )
    ws_sub = p_ws.add_subparsers(dest="workspace_command", required=True)

    p_ws_add = ws_sub.add_parser(
        "add",
        help="Register a folder as a known workspace (the allowlist that "
             "folder ops consult). Idempotent.",
    )
    p_ws_add.add_argument("folder_path", help="Folder to register.")
    p_ws_add.add_argument("--label", default="", help="Optional label.")

    p_ws_rm = ws_sub.add_parser(
        "remove", help="Unregister a folder from the known workspaces.")
    p_ws_rm.add_argument("folder_path", help="Folder to unregister.")

    ws_sub.add_parser("list", help="List registered workspaces.")

    p_ws_mig = ws_sub.add_parser(
        "migrate",
        help="Re-key a workspace log directory from <old hash> to <new hash> "
             "after a folder rename/move. Records the migration on the new "
             "log's chain.",
    )
    p_ws_mig.add_argument("--from", dest="from_path", required=True,
                           help="Old workspace path (the one currently keyed).")
    p_ws_mig.add_argument("--to", dest="to_path", required=True,
                           help="New workspace path (where the folder lives now).")
    p_ws_mig.add_argument(
        "--on-collision", dest="on_collision", default="refuse",
        choices=("refuse", "merge", "archive-existing"),
        help="What to do if a log already exists at the new hash. "
             "Default: refuse.",
    )
    p_ws_mig.add_argument("--operator", default="system",
                           help="Operator identifier recorded on the audit event.")

    p_ws_gc = ws_sub.add_parser(
        "gc",
        help="Walk <log_root> and list orphan log dirs (no live folder).",
    )
    p_ws_gc.add_argument("--orphans", action="store_true",
                          help="Required: opt in to the orphan scan (no-op otherwise).")
    p_ws_gc.add_argument("--archive", action="store_true",
                          help="Move orphan dirs to <log_root>/_archived/<hash>/.")
    p_ws_gc.add_argument("--delete", action="store_true",
                          help="DESTRUCTIVE: remove orphan dirs. Requires --yes-i-mean-it.")
    p_ws_gc.add_argument("--yes-i-mean-it", dest="yes_i_mean_it",
                          action="store_true",
                          help="Required when --delete is set.")
    p_ws_gc.add_argument("--json", action="store_true",
                          help="JSON output.")

    # ---------------------------------------------------------------------
    # models — local-model registry (0.6.8.1+ shell; full surface in 0.7)
    # ---------------------------------------------------------------------
    p_models = sub.add_parser(
        "models",
        help="Local-model registry: list registered models, register a "
             "pulled model into a Workspace role, pull via marketplace "
             "package's pull_models.sh.",
    )
    models_sub = p_models.add_subparsers(dest="models_command", required=True)

    p_mod_list = models_sub.add_parser(
        "list",
        help="List registered models (and their roles).",
    )
    p_mod_list.add_argument("--health", action="store_true",
                            help="Run a health check on each artifact.")
    p_mod_list.add_argument("--json", action="store_true",
                            help="JSON output.")

    p_mod_pull = models_sub.add_parser(
        "pull",
        help="Pull a model by id (wraps the marketplace package's "
             "scripts/pull_models.sh; air-gap operators can call the "
             "script directly).",
    )
    p_mod_pull.add_argument("name", help="Model id, e.g. 'phi-3.5-mini-q4'.")
    p_mod_pull.add_argument("--package-root", dest="package_root", default=None,
                            help="Marketplace package directory containing "
                                 "scripts/pull_models.sh. Auto-discovered if "
                                 "omitted.")

    p_mod_reg = models_sub.add_parser(
        "register",
        help="Register a pulled model into the local registry and tie "
             "it to a Workspace role.",
    )
    p_mod_reg.add_argument("--role", required=True,
                            help="Workspace role: validator | lock-tier-C | "
                                 "drafter | code-fix.")
    p_mod_reg.add_argument("--model", dest="model", required=True,
                            help="Model id (e.g. 'phi-3.5-mini-q4').")
    p_mod_reg.add_argument("--artifact-path", dest="artifact_path", default="",
                            help="Path to the model file on disk. Optional; "
                                 "defaults to ~/.workspace/models/<id>/.")
    p_mod_reg.add_argument("--sha256", default="",
                            help="SHA256 hex of the artifact (optional; will "
                                 "be recorded if provided).")
    p_mod_reg.add_argument("--offline", action="store_true",
                            help="Air-gap path: skip any network-touching "
                                 "verification.")

    p_mod_cfg = models_sub.add_parser(
        "config",
        help="Write the workspace cascade's local-model config. With no flags it uses "
             "the registered local models (in-process, cheap->capable). BYOK: "
             "point the local rung at your own OpenAI-compatible endpoint with "
             "--local-url/--local-model (Ollama, LM Studio, vLLM, llama-server), "
             "and/or a cloud fallback with --cloud-url/--cloud-model. Cloud "
             "credentials come from the active track's egress connector.",
    )
    p_mod_cfg.add_argument("--local-url", dest="local_url", default="",
                           help="BYOK: your local OpenAI-compatible endpoint "
                                "(e.g. http://localhost:11434/v1 for Ollama). "
                                "Omit to use registered in-process GGUFs.")
    p_mod_cfg.add_argument("--local-model", dest="local_model", default="",
                           help="Model id served at --local-url (e.g. qwen2.5-coder).")
    p_mod_cfg.add_argument("--cloud-url", dest="cloud_url", default="",
                           help="OpenAI-compatible cloud endpoint for the last "
                                "rung (optional).")
    p_mod_cfg.add_argument("--cloud-model", dest="cloud_model", default="")
    p_mod_cfg.add_argument("--cloud-api-key", dest="cloud_api_key", default="",
                           help=argparse.SUPPRESS)

    models_sub.add_parser(
        "config-show",
        help="Show the workspace cascade local-model config and the resolved tiers.",
    )

    # ---------------------------------------------------------------------
    # doctor — preflight diagnostics (068 / A+I-r1 patch)
    # ---------------------------------------------------------------------
    p_doctor = sub.add_parser(
        "doctor",
        help="Preflight diagnostics: Python version, deps, key dir perms, "
             "log root, sample round-trip, MCP server reachable, symlink mode. "
             "Exit 0 = all green, 10 = warnings only, 20 = errors present.",
    )
    p_doctor.add_argument("--json", action="store_true",
                          help="Machine-readable JSON output.")
    p_doctor.add_argument("--skip-mcp", action="store_true",
                          help="Skip the MCP-server stdio reachability probe "
                               "(useful in environments where the entry point "
                               "isn't on PATH, e.g. uninstalled dev tree).")

    # licence usage — local, read-only commercial-capacity evidence
    p_licence = sub.add_parser(
        "licence", help="Inspect local commercial-licence capacity evidence.")
    licence_sub = p_licence.add_subparsers(
        dest="licence_command", required=True)
    p_usage = licence_sub.add_parser(
        "usage", help="Report current and peak enabled governed agents.")
    p_usage.add_argument("--from", dest="from_date", default="",
                         help="Reporting start date (YYYY-MM-DD, UTC).")
    p_usage.add_argument("--to", dest="to_date", default="",
                         help="Reporting end date (YYYY-MM-DD, UTC, inclusive).")
    p_usage.add_argument("--capacity", type=int, default=None,
                         help="Licensed agent capacity to compare against.")
    p_usage.add_argument("--json", action="store_true",
                         help="Print the complete machine-readable report.")

    return parser


# ---------------------------------------------------------------------------
# Subcommand: policy show / enable / disable (B6)
# ---------------------------------------------------------------------------
























# ---------------------------------------------------------------------------
# Subcommand: pinned skills (#145)
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Subcommand: run-worker
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: status — folder-aware overview (0.6.7+)
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Subcommand: tools — MCP tool surface discoverability (0.6.7+)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Subcommand: keys (0.6.8 D2 / B4)
# ---------------------------------------------------------------------------


























# ---------------------------------------------------------------------------
# Subcommand: erase (B5, 0.6.8) — first-class GDPR Art. 17 verb
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Subcommand: models (local-model registry — 0.6.8.1 shell, 0.7 full surface)
# ---------------------------------------------------------------------------
















# ---------------------------------------------------------------------------
# Subcommand: doctor — preflight diagnostics (068 A+I-r1 patch)
# ---------------------------------------------------------------------------

# Stable exit-code taxonomy (panel ask):
#   0  = all green (only ok / info)
#   10 = warnings present, no errors
#   20 = errors present

# Check levels, in severity order. Used in tests + JSON output.
































# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------























# Command handlers + dispatch tables live in cli_impl (CLI surface vs impl).
# The doctor taxonomy (levels, exit codes) and check functions are re-exported
# because `workspaces.cli` is the public face of the CLI: tests and callers read
# the doctor surface from this module, not from the impl module.
from .impl import (
    _DISPATCH,
    DOCTOR_EXIT_ERROR,
    DOCTOR_EXIT_OK,
    DOCTOR_EXIT_WARN,
    DOCTOR_LEVEL_ERROR,
    DOCTOR_LEVEL_INFO,
    DOCTOR_LEVEL_OK,
    DOCTOR_LEVEL_WARN,
    _doctor_check_controller_key,
    _doctor_check_optional_deps,
    _doctor_check_python_binding,
    _doctor_check_required_deps,
    _doctor_check_sample_round_trip,
    _doctor_overall_exit,
)

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")
        return 3
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Ingest-time cyber quarantine — hold untrusted auto-input before it reaches memory or a prompt.

The Lock is a PRIVACY boundary (secrets/PII leaving); this is the missing INGRESS-CYBER gate for
auto-input folders. It runs at the ingest boundary and returns a verdict in the Lens vocabulary —
``admit / hold / reject``, **default-deny** — so a threat-shaped input is quarantined (``hold``)
or refused (``reject``) before its body can enter memory or a downstream prompt. It flags and
holds; it never opens, executes, or rewrites the input.

Two pure-stdlib layers (no external engine, no cross-repo dependency):

  1. PROMPT-INJECTION tripwire over the text body — DELEGATED to ``workspaces.lock.injection_scan``
     (RVND's own vendored Tier-D scan: ``IGNORE THE ABOVE`` / ``NEW INSTRUCTIONS:`` / role-hijack /
     exfil / tool-subversion); its Findings map into this module's Threat shape. Aligned to OWASP
     LLM Top-10 ``LLM01`` and MITRE ATLAS.
  2. FILE-SHAPE checks over the raw bytes — magic-byte ↔ extension mismatch (an executable
     masquerading as a document) and active-content markers (Office macros, PDF ``/JavaScript`` /
     ``/OpenAction`` / ``/Launch``). Detection PATTERNS are adapted from oletools / pdfid; the
     signatures are carried as data (YARA-rule shape) without the libraries.

A tripwire, not containment — it catches known shapes, not every phrasing (the same limit the
Circles THREAT-MODEL states). It reduces specific vectors; it is not a comprehensive defence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

from .lens import Admission

TIER = "ingest-cyber"


class QuarantineRefused(Exception):
    """Raised at the ingest boundary when a candidate is held or rejected. Carries the verdict
    so the caller can audit it and, for a ``hold``, surface it for human release."""
    def __init__(self, verdict: "Verdict") -> None:
        self.verdict = verdict
        super().__init__(f"ingest quarantine {verdict.admission}: {verdict.reason}")

# ── Layer 1: prompt injection — DELEGATED to workspaces.lock.injection_scan (the vendored
# Privacy-Lock Tier-D scan). We do not re-implement its ruleset here; ``scan_text`` calls it and
# maps its Findings into this module's Threat shape. (Reuse, not a parallel scanner.)

# active content that can appear as text (scripts / shell / macro entrypoints)
_ACTIVE_TEXT: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"<script\b|javascript:|vbscript:", re.I), "embedded_script"),
    (re.compile(r"\b(?:Auto_?Open|Workbook_Open|Document_Open|AutoExec)\b"), "office_macro_autoexec"),
    (re.compile(r"powershell\s+-enc|cmd\.exe\s*/c|/bin/sh\s+-c|base64\s+-d", re.I), "shell_payload"),
)

# ── Layer 2: file-shape (magic bytes) ─────────────────────────────────────────────────────
_EXEC_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "pe-executable"), (b"\x7fELF", "elf-executable"),
    (b"\xca\xfe\xba\xbe", "macho"), (b"\xcf\xfa\xed\xfe", "macho"),
    (b"#!", "script-shebang"),
)
_DOC_EXT = {".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx", ".pptx", ".rtf", ".html", ".xml"}
# PDF / OLE active-content markers, adapted from pdfid / olevba (carried as data, not the libs)
_PDF_ACTIVE = (b"/JavaScript", b"/JS", b"/OpenAction", b"/Launch", b"/AA", b"/EmbeddedFile")
_OLE_MACRO = (b"vbaProject.bin", b"_VBA_PROJECT", b"Macros", b"AutoOpen")


@dataclass
class Threat:
    kind: str            # prompt_injection | active_content | malware | file_mismatch
    label: str
    severity: str        # high | medium
    detail: str
    confidence: float
    standard: str        # OWASP-LLM01 / ATLAS-… / file-shape

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_text(text: str) -> list[Threat]:
    """Prompt-injection (via the vendored lock scanner) + text-borne active-content threats."""
    if not text:
        return []
    out: list[Threat] = []
    # prompt injection: reuse workspaces.lock.injection_scan — its Findings become Threats.
    from .lock import scan_text as _lis_scan_text
    for f in _lis_scan_text(text):
        detail = getattr(f, "detail", "") or ""
        m = re.search(r"'([^']+)'", detail)
        out.append(Threat("prompt_injection", m.group(1) if m else "prompt_injection",
                          getattr(f, "severity", "high") or "high", detail,
                          float(getattr(f, "confidence", 0.8) or 0.8), "OWASP-LLM01"))
    for rx, label in _ACTIVE_TEXT:
        if rx.search(text):
            out.append(Threat("active_content", label, "high",
                              f"active-content marker {label!r}", 0.85, "file-shape"))
    return out


def scan_bytes(data: bytes, filename: Optional[str] = None) -> list[Threat]:
    """File-shape threats: executable masquerade + document active content."""
    if not data:
        return []
    out: list[Threat] = []
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
    for magic, kind in _EXEC_MAGIC:
        if data.startswith(magic):
            # an executable/script is a threat in an auto-input folder; masquerading as a
            # document is worse (name says .pdf, bytes say PE).
            masq = ext in _DOC_EXT
            out.append(Threat("malware", f"{kind}{'_masquerade' if masq else ''}", "high",
                              f"magic {magic!r} = {kind}" + (f" but extension {ext}" if masq else ""),
                              0.95, "file-shape"))
            return out                       # decisive — no need to scan further
    if data[:5] == b"%PDF-" or ext == ".pdf":
        for marker in _PDF_ACTIVE:
            if marker in data:
                out.append(Threat("active_content", "pdf_active_content", "high",
                                  f"PDF active-content marker {marker.decode(errors='replace')!r}",
                                  0.8, "file-shape"))
                break
    if data[:4] == b"\xd0\xcf\x11\xe0" or (data[:2] == b"PK" and b"vbaProject.bin" in data):
        for marker in _OLE_MACRO:
            if marker in data:
                out.append(Threat("active_content", "office_macro", "high",
                                  f"Office macro marker {marker.decode(errors='replace')!r}",
                                  0.8, "file-shape"))
                break
    return out


@dataclass
class Verdict:
    admission: str                 # Admission value: admit | hold | reject
    threats: list[dict[str, Any]]
    reason: str

    @property
    def quarantined(self) -> bool:
        return self.admission != Admission.ADMIT.value

    def as_dict(self) -> dict[str, Any]:
        return {"admission": self.admission, "quarantined": self.quarantined,
                "threats": self.threats, "reason": self.reason}


def release(folder: str, held_event_id: str, *, actor: str = "user", rationale: str = "",
            log_root: Optional[str] = None) -> str:
    """Human clears a held input — recorded as a signed ``QuarantineReleased`` event referencing
    the original hold, so ``security_dashboard`` decrements ``holds_pending`` live. Requires a
    rationale (origination, like every override). Returns the new event's pair id."""
    if not rationale.strip():
        raise ValueError("releasing a quarantine hold requires a rationale")
    from .memory import WorkspaceMemory
    from .mutation_log import LogEvent
    from pathlib import Path
    mem = WorkspaceMemory(folder, log_root=log_root, actor=actor)
    ev_id = f"quarantine:released:{held_event_id[:24]}"
    mem._own_log.append(LogEvent(
        event="system", folder_path=str(Path(folder).expanduser().resolve()),
        pair_id=ev_id, channel="system", actor=actor,
        extra={"kind": "QuarantineReleased", "released_event_id": held_event_id,
               "rationale": rationale}))
    return ev_id


def scan(*, text: Optional[str] = None, data: Optional[bytes] = None,
         filename: Optional[str] = None) -> Verdict:
    """Scan an ingest candidate and return a default-deny verdict in the Lens vocabulary:

      * ``reject`` — malware / executable (incl. document masquerade): never admitted.
      * ``hold``   — any high-confidence injection or active-content: quarantined for a human.
      * ``admit``  — clean, or only medium-confidence prose signals (attached as advisory).

    The verdict FLAGS and HOLDS; the caller (ingest path) refuses or surfaces — nothing here
    opens or runs the input."""
    threats: list[Threat] = []
    if text:
        threats += scan_text(text)
    if data:
        threats += scan_bytes(data, filename)
    if any(t.kind == "malware" for t in threats):
        adm, reason = Admission.REJECT.value, "executable/malware in an auto-input folder"
    elif any(t.severity == "high" for t in threats):
        adm, reason = Admission.HOLD.value, "high-confidence injection/active-content — quarantined for review"
    else:
        adm, reason = Admission.ADMIT.value, ("advisory signals only" if threats else "no threat pattern matched")
    return Verdict(adm, [t.as_dict() for t in threats], reason)

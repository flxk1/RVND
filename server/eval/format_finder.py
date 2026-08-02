# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Format-discovery loop — find document genres the genre_router doesn't yet know.

"Policy ingest should know them all" is open-ended, so make it empirical: scan a corpus, mine
each document's dominant STRUCTURAL MARKER (the numbering/heading scheme that defines its
genre), and report which markers the router already covers vs. which recur but are UNHANDLED —
the prioritized list of new genre profiles to add. Re-run after adding a profile; the
uncovered list shrinks. Yield scales with the corpus you point it at.

    PYTHONPATH=src python3 eval/format_finder.py <root> [<root> ...]
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from workspaces import format_extractors as fx
from workspaces import genre_router as gr

# A battery of structural-marker fingerprints (more than the router handles, on purpose).
# name -> (regex, genre the router maps it to, or None = NOT yet covered → a candidate).
MARKERS = {
    "Article N":        (re.compile(r"(?m)^\s*Article\s+\d+\b"), "eu-regulation"),
    "§ N":              (re.compile(r"§\s*\d+"), "paragraph-statute"),
    "ISO/IEC clause":   (re.compile(r"ISO/IEC|Annex SL"), "iso-standard"),
    "WHEREAS":          (re.compile(r"\bWHEREAS\b"), "contract"),
    # --- not yet covered by the router (candidate new genres) ---
    "Section N (UK/US)":(re.compile(r"(?m)^\s*Section\s+\d+\b"), None),
    "Sec. N":           (re.compile(r"(?m)^\s*Sec\.\s*\d+"), None),
    "U.S.C. cite":      (re.compile(r"\b\d+\s+U\.S\.C\.\s*§?\s*\d+"), None),
    "CFR cite":         (re.compile(r"\b\d+\s+CFR\s+\d+"), None),
    "Rule N":           (re.compile(r"(?m)^\s*Rule\s+\d+\b"), None),
    "Clause N":         (re.compile(r"(?m)^\s*Clause\s+\d+\b"), None),
    "RFC N":            (re.compile(r"\bRFC\s+\d{3,5}\b"), None),
    "NIST SP 800-N":    (re.compile(r"\bSP\s*800-\d+|\b800-\d+\b"), None),
    "ECLI / [YYYY] (judgment)": (re.compile(r"\bECLI:|\[\d{4}\]\s+[A-Z]{2,}"), None),
    "Schedule N":       (re.compile(r"(?m)^\s*Schedule\s+\d+\b"), None),
    "Chapter/Part N":   (re.compile(r"(?m)^\s*(?:Chapter|Part)\s+[IVXLC0-9]+\b"), None),
    "Recital (N)":      (re.compile(r"(?m)^\s*\(\d{1,3}\)\s+[A-Z]"), None),
}

# Single markers find candidates only among docs the router leaves 'generic'. But a MISSING genre
# can wear a WRONG label: a court judgment cites dozens of §§, so the §-count rule files it as a
# statute and its true genre never surfaces. A genre FINGERPRINT — several signature markers that
# co-occur — catches that. name -> (list of (label, rx), router genres that legitimately own it).
# Empty 'owned_by' = the router has no profile for this genre at all → every strong match is a gap.
GENRE_FINGERPRINTS: dict[str, tuple[list[tuple[str, re.Pattern]], set[str]]] = {
    "case-law (court decision)": ([
        ("Leitsatz",            re.compile(r"\bLeitsa(?:tz|tze|tzes)\b", re.I)),
        ("Tatbestand",          re.compile(r"\bTatbestand\b")),
        ("Entscheidungsgründe", re.compile(r"\bEntscheidungsgr(?:ü|ue)nde\b")),
        ("Urteil/Beschluss",    re.compile(r"\b(?:Urteil|Beschluss)\b")),
        ("Rn. (Randnummer)",    re.compile(r"\bRn\.?\s*\d+")),
        ("ECLI",                re.compile(r"\bECLI:")),
        ("Aktenzeichen",        re.compile(r"\b[IVX]+\s*ZR\s*\d+/\d+|\bKVR\s*\d+/\d+|\bB\d\s*-\d+/\d+")),
        ("v. (case name)",      re.compile(r"(?m)\b[A-Z][a-z]+ v\.? [A-Z][a-z]+")),
    ], set()),
}
_FINGERPRINT_MIN = 3   # distinct signature markers that must co-occur to call the fingerprint a hit

_TEXT_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".xml", ".html"}


def _read(p: Path) -> str:
    try:
        if p.suffix.lower() in (".txt", ".md", ".html", ".xml"):
            return p.read_text(encoding="utf-8", errors="replace")[:400_000]
        return fx._extract_text(p).text[:400_000]
    except Exception:
        return ""


def scan(roots: list[str]) -> None:
    files: list[Path] = []
    for r in roots:
        rp = Path(r)
        if rp.is_file():
            files.append(rp)
        else:
            files += [p for p in rp.rglob("*")
                      if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES
                      and "__pycache__" not in p.parts and "node_modules" not in p.parts]
    print(f"format-finder: scanning {len(files)} documents\n")

    genre_dist: Counter = Counter()
    uncovered: Counter = Counter()           # candidate-new-format markers, by total weight
    uncovered_docs: dict[str, list[str]] = {}
    misgenre: dict[str, list[tuple[str, str, list[str]]]] = {}   # fingerprint-genre → [(doc, assigned, evidence)]
    rows = []
    for p in files:
        text = _read(p)
        if len(text) < 200:
            continue
        genre = gr.detect_genre(text)
        genre_dist[genre] += 1
        hits = {name: len(rx.findall(text)) for name, (rx, _) in MARKERS.items()}
        hits = {k: v for k, v in hits.items() if v}
        dominant = max(hits, key=hits.get) if hits else "—"
        rows.append((p, genre, dominant, hits))
        # a marker that recurs but the router classified the doc 'generic' → candidate
        if genre == "generic":
            for name, n in hits.items():
                if MARKERS[name][1] is None and n >= 3:
                    uncovered[name] += n
                    uncovered_docs.setdefault(name, []).append(p.name)
        # a genre FINGERPRINT that co-occurs strongly but isn't owned by the assigned label → a
        # missing genre wearing a wrong label (e.g. a court judgment filed as paragraph-statute)
        for fg_name, (markers, owned_by) in GENRE_FINGERPRINTS.items():
            present = [lbl for lbl, rx in markers if rx.search(text)]
            if len(present) >= _FINGERPRINT_MIN and genre not in owned_by:
                misgenre.setdefault(fg_name, []).append((p.name, genre, present))

    print("=== detected genre distribution ===")
    for g, n in genre_dist.most_common():
        print(f"  {g:<20} {n}")

    print("\n=== sample documents (genre · dominant marker) ===")
    for p, genre, dom, hits in rows[:18]:
        print(f"  [{genre:<16}] dom={dom:<22} {p.name[:42]}")

    print("\n=== CANDIDATE NEW FORMATS (recurring markers in 'generic' docs the router ignores) ===")
    if not uncovered:
        print("  none in this corpus — point it at real statutes/standards to surface more")
    for name, weight in uncovered.most_common():
        ex = ", ".join(sorted(set(uncovered_docs[name]))[:3])
        print(f"  + {name:<24} weight={weight:<5} e.g. {ex}")

    print("\n=== MISCLASSIFIED GENRES (strong fingerprint the router has no profile for) ===")
    if not misgenre:
        print("  none — every doc's fingerprint matches a genre the router owns")
    for fg_name, items in sorted(misgenre.items(), key=lambda kv: -len(kv[1])):
        labels = Counter(asgn for _, asgn, _ in items)
        as_what = ", ".join(f"{g}×{n}" for g, n in labels.most_common())
        print(f"  ! {fg_name}  ({len(items)} docs, currently labelled: {as_what})")
        for doc, asgn, evidence in items[:4]:
            print(f"      [{asgn:<16}] {doc[:46]:<46} ⟵ {', '.join(evidence[:5])}")


if __name__ == "__main__":
    roots = sys.argv[1:] or ["."]
    scan(roots)

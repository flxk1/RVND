# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deterministic PDF/standard text normaliser — ingest cleanup BEFORE rule extraction.

Raw PDF text (EUR-Lex regulations, ISO standards) is noisy: running headers/footers, page
numbers, hyphenated line-breaks, sentences wrapped over many lines, and large non-normative
preambles (a regulation's recitals; a standard's foreword/terms). Fed raw to the extractor it
over-segments (one rule → many fragments) and drowns in preamble — e.g. the EU AI Act's 180
recitals produced ~1400 unmapped fragments.

Generic, not document-specific. Key techniques: FREQUENCY-BASED boilerplate detection (a short
line recurring across pages is chrome, not content), de-hyphenation, wrapped-line reflow, and
front-matter trimming for the two shapes we ingest:
  * EU regulation — drop everything before "HAS/HAVE ADOPTED THIS REGULATION" (the recitals);
  * ISO / Annex-SL standard — drop clauses 1–3 (Scope / Refs / Terms) before clause 4.

Pure stdlib, deterministic: same input → same output.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_ALWAYS_DROP = [
    re.compile(r"^\d{1,4}$"),                         # bare page number
    re.compile(r"^[ivxlcdm]{1,6}$", re.I),           # roman page number
    re.compile(r"^©"),                               # copyright line
    re.compile(r"all rights reserved", re.I),
    re.compile(r"single user licence|copying and networking", re.I),
    re.compile(r"^OJ\s+[LC]\b", re.I),               # EUR-Lex running header (Official Journal)
    re.compile(r"^\s*(Figure|Table)\s+\d+", re.I),
]

_HEADING = re.compile(r"^(A\.)?\d+(\.\d+)*\s+[A-Za-z]")
_DEONTIC = re.compile(r"\b(shall|must|should|may|required|prohibit\w*|forbidden)\b", re.I)
_ADOPTED = re.compile(r"HA(?:VE|S)\s+ADOPTED\s+THIS\s+REGULATION", re.I)


def _always_drop(line: str) -> bool:
    return any(p.search(line) for p in _ALWAYS_DROP)


def _trim_front_matter(paras: list[str]) -> list[str]:
    """Drop the non-normative preamble. EU regulation: everything up to and including
    'HAVE ADOPTED THIS REGULATION' (title + citations + ALL recitals). ISO/Annex-SL: clauses
    1–3 before clause 4. Otherwise leave untouched."""
    for i, p in enumerate(paras):                     # EU regulation
        if _ADOPTED.search(p):
            return paras[i + 1:]
    has_terms = any(re.match(r"^3(\.\d+)?\s+Terms", p) for p in paras)   # ISO/Annex-SL
    idx = next((i for i, p in enumerate(paras) if re.match(r"^4(\.\d+)?\s+[A-Z]", p)), None)
    return paras[idx:] if (has_terms and idx is not None) else paras


def detect_boilerplate(lines: list[str], min_repeat: int = 4, max_len: int = 90) -> set[str]:
    """Short lines recurring >= min_repeat times are running headers/footers/notices."""
    freq = Counter(l for l in lines if l)
    return {l for l, n in freq.items() if n >= min_repeat and len(l) <= max_len}


def clean(raw: str, *, drop_after: tuple[str, ...] = ("Bibliography",),
          min_repeat: int = 4, trim: bool = True) -> tuple[str, dict[str, Any]]:
    """Normalise raw document text → reflowed paragraphs. Returns (text, report). With
    ``trim=False`` skips front-matter trimming (the genre_router owns preamble removal)."""
    stripped = [l.strip() for l in raw.split("\n")]
    boiler = detect_boilerplate(stripped, min_repeat=min_repeat)

    kept, n_boiler, n_chrome = [], 0, 0
    for s in stripped:
        if not s:
            kept.append(""); continue
        if s in boiler:
            n_boiler += 1; continue
        if _always_drop(s):
            n_chrome += 1; continue
        kept.append(s)

    paras: list[str] = []
    buf = ""

    def flush():
        nonlocal buf
        if buf.strip():
            paras.append(re.sub(r"\s+", " ", buf).strip())
        buf = ""

    for s in kept:
        if not s:
            flush(); continue
        if _HEADING.match(s) and len(s) < 70:
            flush(); paras.append(s); buf = ""; continue   # clause heading = its own paragraph
        if re.match(r"^(Note\b|NOTE\b|EXAMPLE\b)", s):
            flush(); buf = s; continue
        if not buf:
            buf = s
        elif buf.endswith("-"):
            buf = buf[:-1] + s                              # de-hyphenate across the break
        else:
            buf = buf + " " + s
    flush()

    if trim:
        paras = _trim_front_matter(paras)

    out = []
    for p in paras:
        if any(p.startswith(m) for m in drop_after):
            break
        if _HEADING.match(p) and len(p) < 70:
            out.append(p); continue                        # keep clause headings (section markers)
        if re.match(r"^(Note\b|NOTE\b|EXAMPLE\b)", p) or "to entry:" in p:
            continue
        if _DEONTIC.search(p) or len(p.split()) >= 8:      # keep real requirements / substantive prose
            out.append(p)

    text = "\n\n".join(out) + "\n"
    report = {
        "lines_in": len([l for l in stripped if l]),
        "boilerplate_lines_dropped": n_boiler,
        "chrome_lines_dropped": n_chrome,
        "paragraphs_out": len(out),
        "shall_in": len(re.findall(r"\bshall\b", raw, re.I)),
        "shall_out": len(re.findall(r"\bshall\b", text, re.I)),
    }
    return text, report

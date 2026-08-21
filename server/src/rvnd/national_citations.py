# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""National-citation recogniser — German statute citations as mappable entities.

The cross-reference extractor recognises EU instruments ("Regulation (EU)
2016/679"); this module is its national counterpart. It reads German statutory
citations — "§ 286 BGB", "§ 147 Abs. 1 AO", "Art. 229 EGBGB", "§ 48 VwVfG" — and
turns each cited *statute* into a corpus instrument entity (code, name, an
official gesetze-im-internet URL, jurisdiction DE, domain tags), so a German clause
anchors onto the legal map exactly like an EU one.

Design choices kept honest:
  * The corpus entity is the **statute** (stable URL); the **section pinpoint**
    ("§ 286 Abs. 1") rides along as the anchor basis, not as a separate entity.
  * URLs use the official gesetze-im-internet host. For §-numbered statutes whose
    slug is curated we emit the statute page; an uncurated abbreviation is still
    recognised and anchored, with ``url=None`` (the corpus-validate pass then flags
    the missing URL rather than shipping a guessed-broken link).

This is the national half of "if a document carries ND-relevant info, it should
get mapped": a clause citing § 286 BGB makes BGB a mapped, retrievable entity even
though the seed corpus (digital-law only) never pre-loaded it. Pure stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# abbrev → (full name, gesetze-im-internet slug | None, domain tags)
_STATUTES: dict[str, tuple[str, Optional[str], tuple[str, ...]]] = {
    "BGB":       ("Bürgerliches Gesetzbuch", "bgb", ("civil",)),
    "HGB":       ("Handelsgesetzbuch", "hgb", ("commercial",)),
    "AO":        ("Abgabenordnung", "ao_1977", ("tax",)),
    "EGBGB":     ("Einführungsgesetz zum BGB", "bgbeg", ("civil",)),
    "GG":        ("Grundgesetz", "gg", ("constitutional",)),
    "StGB":      ("Strafgesetzbuch", "stgb", ("criminal",)),
    "StPO":      ("Strafprozessordnung", "stpo", ("criminal",)),
    "ZPO":       ("Zivilprozessordnung", "zpo", ("procedure",)),
    "VwVfG":     ("Verwaltungsverfahrensgesetz", "vwvfg", ("administrative",)),
    "VwGO":      ("Verwaltungsgerichtsordnung", "vwgo", ("administrative",)),
    "BDSG":      ("Bundesdatenschutzgesetz", "bdsg_2018", ("data",)),
    "UrhG":      ("Urheberrechtsgesetz", "urhg", ("copyright",)),
    "UWG":       ("Gesetz gegen den unlauteren Wettbewerb", "uwg", ("competition",)),
    "GWB":       ("Gesetz gegen Wettbewerbsbeschränkungen", "gwb", ("competition",)),
    "GmbHG":     ("GmbH-Gesetz", "gmbhg", ("corporate",)),
    "AktG":      ("Aktiengesetz", "aktg", ("corporate",)),
    "GeschGehG": ("Geschäftsgeheimnisgesetz", "geschgehg", ("trade-secrets",)),
    "MarkenG":   ("Markengesetz", "markeng", ("trademark",)),
    "PatG":      ("Patentgesetz", "patg", ("patent",)),
    "ProdHaftG": ("Produkthaftungsgesetz", "prodhaftg", ("liability",)),
    "BImSchG":   ("Bundes-Immissionsschutzgesetz", "bimschg", ("environment",)),
    "TMG":       ("Telemediengesetz", "tmg", ("digital",)),
    "TKG":       ("Telekommunikationsgesetz", None, ("telecom",)),
    "SGB":       ("Sozialgesetzbuch", None, ("social",)),
}

# Statutes whose section is cited with "Art." rather than "§".
_ART_STATUTES = frozenset({"GG", "EGBGB"})

_ABBR_ALT = "|".join(sorted((re.escape(a) for a in _STATUTES), key=len, reverse=True))

# "§ 286 BGB", "§§ 305 ff. BGB", "§ 147 Abs. 1 AO", "§ 312g Abs. 2 Nr. 1 BGB"
_PARA_RE = re.compile(
    r"§{1,2}\s*(?P<sec>\d+[a-z]?)"
    r"(?:\s*(?:ff\.?|f\.?))?"
    r"(?:\s+Abs\.?\s*\d+)?(?:\s+S\.?\s*\d+|\s+Satz\s*\d+)?(?:\s+Nr\.?\s*\d+)?"
    r"\s+(?P<abbr>" + _ABBR_ALT + r")\b")
# "Art. 229 EGBGB", "Art. 6 GG"
_ART_RE = re.compile(
    r"Art\.?\s*(?P<sec>\d+[a-z]?)(?:\s+Abs\.?\s*\d+)?\s+(?P<abbr>GG|EGBGB)\b")
# bare statute mention without a section ("nach dem BDSG", "im HGB")
_BARE_RE = re.compile(r"\b(?P<abbr>" + _ABBR_ALT + r")\b")


@dataclass
class Citation:
    code: str            # corpus entity code (abbrev lowercased): bgb, ao, hgb
    abbrev: str          # BGB
    name: str            # Bürgerliches Gesetzbuch
    section: str         # "§ 286" / "Art. 229" / "" if bare
    url: Optional[str]
    domains: tuple
    jurisdiction: str = "DE"


def _statute_url(abbr: str) -> Optional[str]:
    slug = _STATUTES[abbr][1]
    return f"https://www.gesetze-im-internet.de/{slug}/" if slug else None


def extract_citations(text: str) -> list[Citation]:
    """Every German statute citation in ``text``. One Citation per statute, with
    the most specific section pinpoint seen for it."""
    found: dict[str, Citation] = {}

    def _record(abbr: str, marker: str, sec: str) -> None:
        name, _slug, domains = _STATUTES[abbr]
        code = abbr.lower()
        section = f"{marker} {sec}".strip() if sec else ""
        cur = found.get(code)
        if cur is None:
            found[code] = Citation(code, abbr, name, section, _statute_url(abbr), domains)
        elif section and not cur.section:
            cur.section = section

    for m in _PARA_RE.finditer(text):
        _record(m.group("abbr"), "§", m.group("sec"))
    for m in _ART_RE.finditer(text):
        _record(m.group("abbr"), "Art.", m.group("sec"))
    # bare mentions only add a statute that wasn't already pinned with a section
    for m in _BARE_RE.finditer(text):
        abbr = m.group("abbr")
        if abbr.lower() not in found:
            _record(abbr, "", "")
    return list(found.values())


def to_candidates(text: str) -> list[dict]:
    """National statute citations as corpus-entity candidates (same shape as
    ``corpus_ingest.candidates_from_text``), carrying the section pinpoint."""
    out = []
    for c in extract_citations(text):
        out.append({
            "code": c.code,
            "name": f"{c.name} ({c.abbrev})",
            "kind": "instrument",
            "url": c.url,
            "jurisdiction": "DE",
            "domains": c.domains,
            "celex": None,
            "pinpoint": c.section,
        })
    return out

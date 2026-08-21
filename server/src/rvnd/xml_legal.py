# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Structure-aware ingest for EU legislation in XML (Layer-1).

The regex extractors flatten a statute into sentences and then *guess* its
article/paragraph/point hierarchy and its cross-references. EUR-Lex publishes
every act as structured XML — Akoma Ntoso (OASIS LegalDocML) and Formex 4 —
where those are first-class elements. Parsing that source removes the guessing:
the hierarchy and the cross-reference targets come from the document's own
markup, not from a pattern that hopes to spot them.

This module produces a :class:`DocumentTree` of :class:`ProvisionNode` leaves.
It does NOT replace the NDs; it gives the routing layer a per-provision unit to
run them on (one obligation per node instead of a run-on sentence) and a set of
native cross-references the crossref ND can trust at authority tier 1.

Scope of this first build:
- ``parse_akoma_ntoso(xml_bytes) -> DocumentTree`` — articles, paragraphs,
  points, and ``<ref href>`` cross-references.
- ``parse_formex(xml_bytes) -> DocumentTree`` — the Publications Office format
  (ARTICLE / PARAG / ALINEA; REF.DOC.OJ for citations).
- ``parse_legal_xml(xml_bytes)`` — sniffs the root element and dispatches.
- ``document_tree_to_text(tree)`` — a flat text projection so the existing
  text-only path still works when a caller wants prose.

Stdlib only (``xml.etree``). Namespace-tolerant: Akoma Ntoso documents are
usually in the ``http://docs.oasis-open.org/legaldocml/ns/akn/3.0`` namespace,
but real-world files vary, so we match on local tag names.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class CrossRef:
    """A cross-reference read from the markup (not regex-guessed)."""
    raw: str                       # the link text / href as it appeared
    target_celex: str = ""         # resolved CELEX if derivable from href
    target_eId: str = ""           # intra/inter-document eId the href points at
    href: str = ""                 # the raw href attribute

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProvisionNode:
    """One leaf (or structural) provision: an article, paragraph, or point."""
    eId: str                       # Akoma Ntoso eId, e.g. "art_28__para_3__point_a"
    kind: str                      # article | paragraph | point | recital | annex | citation
    heading: str = ""              # the article/section heading, if any
    text: str = ""                 # leaf text (this node's own operative text)
    num: str = ""                  # the visible number ("28", "3", "(a)")
    parent_eId: str = ""
    refs: list[CrossRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["refs"] = [r.to_dict() for r in self.refs]
        return d


@dataclass
class DocumentTree:
    """A parsed legal instrument: its identity + an ordered list of provisions."""
    celex: str = ""
    title: str = ""
    format: str = ""               # "akoma-ntoso" | "formex"
    nodes: list[ProvisionNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "celex": self.celex,
            "title": self.title,
            "format": self.format,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    def leaf_provisions(self) -> list[ProvisionNode]:
        """Nodes that carry operative text (the units NDs run on)."""
        return [n for n in self.nodes if n.text.strip()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Strip an XML namespace, returning the local tag name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _itertext(el: ET.Element) -> str:
    """All descendant text, whitespace-normalised."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


# CELEX from common EUR-Lex href shapes, e.g.
# ".../TXT/?uri=CELEX:32016R0679", "celex=32016R0679", "/eli/reg/2016/679/oj"
_CELEX_IN_HREF = re.compile(r"CELEX[:%]?3?\s*(?P<c>3\d{4}[A-Z]\d{4})", re.IGNORECASE)
_BARE_CELEX = re.compile(r"\b(?P<c>3\d{4}[A-Z]\d{4})\b")
# ELI shape: /eli/reg/2016/679  → 3 2016 R 0679  (best-effort)
_ELI = re.compile(r"/eli/(?P<type>reg|dir)/(?P<year>\d{4})/(?P<num>\d+)", re.IGNORECASE)


def _celex_from_href(href: str) -> str:
    if not href:
        return ""
    m = _CELEX_IN_HREF.search(href) or _BARE_CELEX.search(href)
    if m:
        return m.group("c").upper()
    m = _ELI.search(href)
    if m:
        sector = "3"
        letter = "R" if m.group("type").lower() == "reg" else "L"
        return f"{sector}{m.group('year')}{letter}{int(m.group('num')):04d}"
    return ""


# ---------------------------------------------------------------------------
# Akoma Ntoso
# ---------------------------------------------------------------------------

# AKN hierarchical container tags whose leaves we surface.
_AKN_CONTAINER = {"article", "paragraph", "point", "subparagraph", "recital",
                  "citation", "list", "blockList"}
_AKN_NUM = {"num"}
_AKN_HEADING = {"heading"}


def _akn_refs(el: ET.Element) -> list[CrossRef]:
    out: list[CrossRef] = []
    for ref in el.iter():
        if _local(ref.tag) != "ref":
            continue
        href = ref.attrib.get("href", "") or ref.attrib.get("{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}href", "")
        out.append(CrossRef(
            raw=_itertext(ref) or href,
            href=href,
            target_celex=_celex_from_href(href),
            target_eId=href.lstrip("#") if href.startswith("#") else "",
        ))
    return out


def _akn_walk(el: ET.Element, parent_eId: str, out: list[ProvisionNode]) -> None:
    tag = _local(el.tag)
    if tag not in _AKN_CONTAINER:
        for child in el:
            _akn_walk(child, parent_eId, out)
        return

    eId = el.attrib.get("eId") or el.attrib.get("id") or f"{tag}_{len(out)}"
    num = ""
    heading = ""
    own_text_parts: list[str] = []
    child_containers: list[ET.Element] = []

    for child in el:
        ctag = _local(child.tag)
        if ctag in _AKN_NUM:
            num = _itertext(child)
        elif ctag in _AKN_HEADING:
            heading = _itertext(child)
        elif ctag in _AKN_CONTAINER:
            child_containers.append(child)
        else:
            # content / p / intro / wrapUp etc. — this node's own text
            own_text_parts.append(_itertext(child))

    node = ProvisionNode(
        eId=eId,
        kind=tag if tag in ("article", "paragraph", "point", "recital", "citation")
        else "paragraph",
        heading=heading,
        num=num,
        text=" ".join(t for t in own_text_parts if t).strip(),
        parent_eId=parent_eId,
        # Container nodes carry no refs of their own; they are gathered per-leaf
        # below. This was written as a comprehension over an empty iterable.
        refs=_akn_refs(el) if not child_containers else [],
    )
    # If this container has child containers, its own refs still belong to its
    # intro text; gather refs only from non-container children to avoid
    # double-counting a child's refs on the parent.
    if child_containers:
        node.refs = []
        for child in el:
            if _local(child.tag) not in _AKN_CONTAINER and _local(child.tag) not in (_AKN_NUM | _AKN_HEADING):
                node.refs.extend(_akn_refs(child))
    out.append(node)
    for child in child_containers:
        _akn_walk(child, eId, out)


def parse_akoma_ntoso(xml_bytes: bytes | str) -> DocumentTree:
    """Parse an Akoma Ntoso act into a :class:`DocumentTree`."""
    root = ET.fromstring(xml_bytes)
    tree = DocumentTree(format="akoma-ntoso")

    # Identity: FRBRWork/FRBRuri or FRBRalias often carries the CELEX; the
    # docTitle / heading carries the title.
    for el in root.iter():
        lt = _local(el.tag)
        if lt in ("FRBRuri", "FRBRalias"):
            val = el.attrib.get("value", "") or el.attrib.get("name", "")
            c = _celex_from_href(val)
            if c and not tree.celex:
                tree.celex = c
        elif lt in ("docTitle", "shortTitle", "title") and not tree.title:
            t = _itertext(el)
            if t:
                tree.title = t[:200]

    # Body: walk the act body / mainBody for hierarchical containers.
    body = None
    for el in root.iter():
        if _local(el.tag) in ("body", "mainBody", "act"):
            body = el
            break
    _akn_walk(body if body is not None else root, "", tree.nodes)
    return tree


# ---------------------------------------------------------------------------
# Formex 4 (Publications Office)
# ---------------------------------------------------------------------------

# Formex tags: ARTICLE > PARAG > ALINEA; TI.ART = article heading; NO.ART = num;
# REF.DOC.OJ carries an OJ/CELEX citation.
def _formex_refs(el: ET.Element) -> list[CrossRef]:
    out: list[CrossRef] = []
    for ref in el.iter():
        lt = _local(ref.tag)
        if lt not in ("REF.DOC.OJ", "REF.DOC.CELEX", "HREF", "REF"):
            continue
        href = (ref.attrib.get("CELEX", "") or ref.attrib.get("href", "")
                or _itertext(ref))
        out.append(CrossRef(
            raw=_itertext(ref) or href,
            href=ref.attrib.get("href", ""),
            target_celex=_celex_from_href(href) or _celex_from_href(_itertext(ref)),
        ))
    return out


def parse_formex(xml_bytes: bytes | str) -> DocumentTree:
    """Parse a Formex 4 act into a :class:`DocumentTree` (best-effort)."""
    root = ET.fromstring(xml_bytes)
    tree = DocumentTree(format="formex")

    for el in root.iter():
        lt = _local(el.tag)
        if lt == "NO.DOC.OJ" and not tree.celex:
            tree.celex = _celex_from_href(el.attrib.get("CELEX", "") or _itertext(el))
        elif lt in ("TITLE", "STI.ART") and not tree.title:
            t = _itertext(el)
            if t:
                tree.title = t[:200]

    art_idx = 0
    for art in root.iter():
        if _local(art.tag) != "ARTICLE":
            continue
        art_idx += 1
        num = ""
        heading = ""
        para_nodes: list[ET.Element] = []
        for child in art:
            ctag = _local(child.tag)
            if ctag == "NO.ART":
                num = _itertext(child)
            elif ctag == "TI.ART":
                heading = _itertext(child)
            elif ctag in ("PARAG", "ALINEA"):
                para_nodes.append(child)
        art_eId = (art.attrib.get("IDENTIFIER") or f"art_{num or art_idx}").strip()
        # article-level node (heading + intro)
        tree.nodes.append(ProvisionNode(
            eId=art_eId, kind="article", heading=heading, num=num,
            text="", parent_eId="", refs=[]))
        if not para_nodes:
            # whole-article text fallback
            tree.nodes[-1].text = _itertext(art)
            tree.nodes[-1].refs = _formex_refs(art)
            continue
        for pi, para in enumerate(para_nodes, 1):
            pnum = ""
            for c in para:
                if _local(c.tag) == "NO.PARAG":
                    pnum = _itertext(c)
            tree.nodes.append(ProvisionNode(
                eId=f"{art_eId}__para_{pnum or pi}",
                kind="paragraph", num=pnum,
                text=_itertext(para),
                parent_eId=art_eId,
                refs=_formex_refs(para)))
    return tree


# ---------------------------------------------------------------------------
# Dispatch + text projection
# ---------------------------------------------------------------------------

def parse_legal_xml(xml_bytes: bytes | str) -> DocumentTree:
    """Sniff the root element and dispatch to the right parser.

    Akoma Ntoso roots are ``<akomaNtoso>``; Formex roots vary
    (``<ACT>``, ``<DOC>``, ``<REGULATION>``) but use ARTICLE/PARAG inside.
    Falls back to Akoma Ntoso parsing (it is the more standardised format).
    """
    root = ET.fromstring(xml_bytes)
    rt = _local(root.tag).lower()
    if rt == "akomantoso" or any(_local(e.tag) == "akomaNtoso" for e in [root]):
        return parse_akoma_ntoso(xml_bytes)
    # Formex signal: an ARTICLE element with Formex-style children anywhere.
    for el in root.iter():
        if _local(el.tag) == "ARTICLE":
            return parse_formex(xml_bytes)
    # default
    return parse_akoma_ntoso(xml_bytes)


def document_tree_to_text(tree: DocumentTree) -> str:
    """Flat text projection so the text-only ND path still works.

    Each provision is rendered with its number + heading + text so the
    sentence-based extractors see structure-respecting boundaries (one
    provision per block) rather than a single run-on blob.
    """
    blocks: list[str] = []
    if tree.title:
        blocks.append(tree.title)
    for n in tree.nodes:
        head = " ".join(p for p in (n.num, n.heading) if p).strip()
        body = n.text.strip()
        if head and body:
            blocks.append(f"{head}\n{body}")
        elif body:
            blocks.append(body)
        elif head:
            blocks.append(head)
    return "\n\n".join(blocks)


def all_cross_refs(tree: DocumentTree) -> list[CrossRef]:
    """Every cross-reference in the document, deduped by (celex, eId, raw)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[CrossRef] = []
    for n in tree.nodes:
        for r in n.refs:
            key = (r.target_celex, r.target_eId, r.raw.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out

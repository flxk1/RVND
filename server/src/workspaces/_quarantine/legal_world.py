# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The world map of legal entities — jurisdictions, regulators, standards bodies,
instruments, and (at query time) legal persons — plus the run-time that folds the
connection algebra over them to answer *which law reaches an entity*.

Two entity layers (per the chosen design): **jurisdictions / orders** and **legal
persons**, joined by the connection algebra in ``legal_connection.py``. On top sit
two corpus kinds that make the map a *retrievable* thing rather than an abstract
graph: **instruments** (the laws themselves) and **regulators / standards bodies**
(the organisations), each carrying a canonical URL. That URL is the point: the map
is a corpus of pointers to real organisations and laws, growable through the
ingest path in ``legal_corpus.py``.

Coverage is deliberately the *digital* stack — data, platform, AI, cyber,
digital-markets, digital-identity, and the standards/governance bodies around them
— not general civil or local law. The seed (`seed_world()`) is real and cited;
it is a starting corpus, marked ``seed`` and meant to grow.

``reach(person)`` is the headline: establish a person under a jurisdiction, climb
the membership ladder via the algebra, and return the legal orders that govern it
*and the instruments that apply there*, with provenance — escalating the chains
the law itself leaves contested (corporate-group reach, un-incorporated treaties).

Pure stdlib; projection reuses ``workspaces.dimensions`` so the map drops straight into
the 5D KG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from workspaces.adapters.solver.dimensions import Dimension
from .legal_connection import (Connection, ESCALATE, GOVERNING, compose_path,
                               dimension)


class EntityKind(Enum):
    # jurisdictions / legal orders
    STATE = "state"
    SUPRANATIONAL = "supranational"
    INTERNATIONAL_REGIME = "international_regime"
    # corpus organisations
    REGULATOR = "regulator"
    STANDARDS_BODY = "standards_body"
    # corpus instruments (the laws)
    INSTRUMENT = "instrument"
    # private instruments (contracts between legal persons — first-class on the
    # map so clauses can anchor to them the way rules anchor to laws)
    CONTRACT = "contract"
    # legal persons (usually added at query time, not seeded)
    LEGAL_PERSON = "legal_person"
    NATURAL_PERSON = "natural_person"
    PUBLIC_BODY = "public_body"


JURISDICTION_KINDS = frozenset({EntityKind.STATE, EntityKind.SUPRANATIONAL,
                                EntityKind.INTERNATIONAL_REGIME})


@dataclass
class Entity:
    code: str                         # unique slug (ISO code for states; slug otherwise)
    name: str
    kind: EntityKind
    url: Optional[str] = None         # canonical retrievable URL (the corpus pointer)
    jurisdiction: Optional[str] = None  # owning legal order (e.g. "EU", "DE")
    domains: tuple[str, ...] = ()     # data/platform/ai/cyber/digital-markets/…
    region: str = ""
    source: str = "seed"              # provenance: seed | user | ingest
    facets: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "kind": self.kind.value,
                "url": self.url, "jurisdiction": self.jurisdiction,
                "domains": list(self.domains), "region": self.region,
                "source": self.source, "facets": self.facets}


@dataclass
class WorldEdge:
    subject: str                      # entity code
    connection: Connection
    object: str                       # entity code
    basis: str = ""                   # the legal instrument / agreement behind the edge
    url: str = ""                     # source for the relation
    source: str = "seed"

    def to_dict(self) -> dict:
        return {"subject": self.subject, "connection": self.connection.value,
                "object": self.object, "basis": self.basis, "url": self.url,
                "source": self.source}


@dataclass
class GovEntry:
    jurisdiction: str
    relation: str                     # subject_to | bound_by | escalate
    escalated: bool
    via: list[dict]                   # provenance edges
    instruments: list[dict]           # laws applying in that jurisdiction (with URLs)


@dataclass
class ReachResult:
    person: str
    governed_by: list[GovEntry]

    def to_dict(self) -> dict:
        return {"person": self.person,
                "governed_by": [{"jurisdiction": g.jurisdiction,
                                 "relation": g.relation, "escalated": g.escalated,
                                 "via": g.via, "instruments": g.instruments}
                                for g in self.governed_by]}


class WorldMap:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.edges: list[WorldEdge] = []
        self._adj: dict[str, list[WorldEdge]] = {}

    # ── construction ──────────────────────────────────────────────────────────
    def add(self, e: Entity) -> Entity:
        self.entities[e.code] = e
        return e

    def connect(self, subject: str, connection: Connection, obj: str, *,
                basis: str = "", url: str = "", source: str = "seed") -> WorldEdge:
        edge = WorldEdge(subject, connection, obj, basis=basis, url=url, source=source)
        self.edges.append(edge)
        self._adj.setdefault(subject, []).append(edge)
        return edge

    # ── queries ───────────────────────────────────────────────────────────────
    def get(self, code: str) -> Optional[Entity]:
        return self.entities.get(code)

    def neighbours(self, code: str) -> list[WorldEdge]:
        return list(self._adj.get(code, ()))

    def search(self, *, kind: Optional[EntityKind] = None,
               domain: Optional[str] = None,
               jurisdiction: Optional[str] = None) -> list[Entity]:
        out = []
        for e in self.entities.values():
            if kind is not None and e.kind is not kind:
                continue
            if domain is not None and domain not in e.domains:
                continue
            if jurisdiction is not None and e.jurisdiction != jurisdiction:
                continue
            out.append(e)
        return out

    def instruments_in(self, jurisdiction: str) -> list[Entity]:
        """Instruments that APPLY_IN the given jurisdiction (the governing-law set)."""
        out = []
        for ed in self.edges:
            if ed.connection is Connection.APPLIES_IN and ed.object == jurisdiction:
                inst = self.entities.get(ed.subject)
                if inst is not None:
                    out.append(inst)
        return out

    def urls(self) -> list[dict]:
        """The retrievable corpus: every entity that carries a URL."""
        return [{"code": e.code, "kind": e.kind.value, "name": e.name,
                 "url": e.url, "domains": list(e.domains)}
                for e in self.entities.values() if e.url]

    # ── reach: which law governs a person ─────────────────────────────────────
    def reach(self, person: str, *, max_depth: int = 6) -> ReachResult:
        """Fold the connection algebra from ``person`` up the jurisdiction ladder.
        Returns the legal orders that govern it (SUBJECT_TO / BOUND_BY) — or that
        the law leaves contested (ESCALATE) — each with the instruments applying
        there and full provenance."""
        found: dict[str, GovEntry] = {}

        def walk(node: str, chain: list[Connection], path: list[WorldEdge],
                 visited: frozenset) -> None:
            if len(path) >= max_depth:
                return
            for ed in self._adj.get(node, ()):
                if ed.object in visited:
                    continue
                new_chain = chain + [ed.connection]
                new_path = path + [ed]
                target = self.entities.get(ed.object)
                if target is not None and target.kind in JURISDICTION_KINDS and len(new_chain) >= 1:
                    result, escalated = compose_path(new_chain) if len(new_chain) > 1 \
                        else (new_chain[0], False)
                    is_gov = result in GOVERNING
                    if is_gov or escalated or result is ESCALATE:
                        rel = (result.value if result in GOVERNING
                               else "escalate")
                        prev = found.get(ed.object)
                        # keep the shortest clean path; prefer non-escalated
                        if prev is None or (prev.escalated and not escalated):
                            found[ed.object] = GovEntry(
                                jurisdiction=ed.object, relation=rel,
                                escalated=bool(escalated) or result is ESCALATE,
                                via=[e.to_dict() for e in new_path],
                                instruments=[{"code": i.code, "name": i.name,
                                              "url": i.url, "domains": list(i.domains)}
                                             for i in self.instruments_in(ed.object)])
                walk(ed.object, new_chain, new_path, visited | {ed.object})

        walk(person, [], [], frozenset({person}))
        return ReachResult(person=person,
                           governed_by=sorted(found.values(),
                                              key=lambda g: (g.escalated, g.jurisdiction)))

    # ── projection into the 5D / ND KG ────────────────────────────────────────
    def project(self) -> list[dict]:
        """Emit the map as dimensioned pair dicts (consumed by reasoning.py and
        the audit log). Each entity becomes a node carrying its URL; each edge
        carries its connection's 5D dimension."""
        adj_by: dict[str, list[dict]] = {}
        for ed in self.edges:
            adj_by.setdefault(ed.subject, []).append({
                "subject": f"entity:{ed.subject}", "predicate": ed.connection.value,
                "object": f"entity:{ed.object}", "dimension": dimension(ed.connection).value,
                "note": ed.basis})
        pairs = []
        for code, e in self.entities.items():
            nid = f"entity:{code}"
            pairs.append({
                "id": nid,
                "problem": {"id": f"{nid}-p", "scope": "legal-world",
                            "type": e.kind.value, "summary": e.name,
                            "facets": {"url": e.url, "domains": list(e.domains),
                                       "jurisdiction": e.jurisdiction,
                                       "region": e.region, "source": e.source,
                                       **(e.facets or {})}},
                "solution": {"id": nid, "problem_id": f"{nid}-p", "body": e.name,
                             "body_format": "kg-node", "authority_tier": 1,
                             "confidence": 1.0, "url": e.url},
                "edges": adj_by.get(code, []),
            })
        return pairs

    def dimensions_present(self) -> set[str]:
        return {dimension(ed.connection).value for ed in self.edges}


# ── the seed corpus (digital / platform / data / regulatory) ──────────────────

def _eu_eli(kind: str, year: int, num: int) -> str:
    return f"https://eur-lex.europa.eu/eli/{kind}/{year}/{num}/oj"


def seed_world() -> WorldMap:
    """A real, citable starting corpus of the digital-law stack. Marked ``seed``;
    extend via ``legal_corpus.ingest_entity``. NOT exhaustive and NOT general/local
    law — by design it covers data, platform, AI, cyber, digital-markets,
    digital-identity, and the standards/governance bodies around them."""
    w = WorldMap()
    C = Connection

    # jurisdictions / orders
    w.add(Entity("EU", "European Union", EntityKind.SUPRANATIONAL,
                 url="https://european-union.europa.eu", region="Europe"))
    for code, name in [("DE", "Germany"), ("FR", "France"), ("IE", "Ireland"),
                       ("NL", "Netherlands")]:
        w.add(Entity(code, name, EntityKind.STATE, jurisdiction="EU", region="Europe"))
        w.connect(code, C.MEMBER_OF, "EU", basis="TEU Art. 1; Accession Treaty")
        w.connect("EU", C.HAS_PRIMACY_OVER, code,
                  basis="primacy of EU law (Costa v ENEL, 6/64)")
    w.add(Entity("US", "United States", EntityKind.STATE, region="Americas"))
    w.add(Entity("UK", "United Kingdom", EntityKind.STATE, region="Europe"))
    w.add(Entity("COE", "Council of Europe", EntityKind.INTERNATIONAL_REGIME,
                 url="https://www.coe.int", region="Europe"))
    w.add(Entity("OECD", "OECD", EntityKind.INTERNATIONAL_REGIME,
                 url="https://www.oecd.org"))

    # instruments — the EU digital acquis (each APPLIES_IN EU)
    instruments = [
        ("gdpr", "General Data Protection Regulation", ("data",), _eu_eli("reg", 2016, 679)),
        ("ai-act", "AI Act", ("ai",), _eu_eli("reg", 2024, 1689)),
        ("dsa", "Digital Services Act", ("platform",), _eu_eli("reg", 2022, 2065)),
        ("dma", "Digital Markets Act", ("digital-markets",), _eu_eli("reg", 2022, 1925)),
        ("nis2", "NIS2 Directive", ("cyber",), _eu_eli("dir", 2022, 2555)),
        ("cra", "Cyber Resilience Act", ("cyber",), _eu_eli("reg", 2024, 2847)),
        ("data-act", "Data Act", ("data",), _eu_eli("reg", 2023, 2854)),
        ("dga", "Data Governance Act", ("data",), _eu_eli("reg", 2022, 868)),
        ("eidas", "eIDAS Regulation", ("digital-identity",), _eu_eli("reg", 2014, 910)),
        ("eprivacy", "ePrivacy Directive", ("data",), _eu_eli("dir", 2002, 58)),
    ]
    for code, name, domains, url in instruments:
        w.add(Entity(code, name, EntityKind.INSTRUMENT, url=url,
                     jurisdiction="EU", domains=domains))
        w.connect(code, C.APPLIES_IN, "EU", basis="directly applicable / transposed")
    # Council-of-Europe instrument
    w.add(Entity("convention-108-plus", "Convention 108+", EntityKind.INSTRUMENT,
                 url="https://www.coe.int/en/web/data-protection/convention108-and-protocol",
                 jurisdiction="COE", domains=("data",)))
    w.connect("convention-108-plus", C.APPLIES_IN, "COE", basis="CETS 223")
    # temporal lineage (supersedes)
    w.add(Entity("dpd-95", "Data Protection Directive 95/46", EntityKind.INSTRUMENT,
                 url=_eu_eli("dir", 1995, 46), jurisdiction="EU", domains=("data",)))
    w.connect("gdpr", C.SUPERSEDES, "dpd-95", basis="GDPR Art. 94")
    w.add(Entity("nis1", "NIS Directive 2016/1148", EntityKind.INSTRUMENT,
                 url=_eu_eli("dir", 2016, 1148), jurisdiction="EU", domains=("cyber",)))
    w.connect("nis2", C.SUPERSEDES, "nis1", basis="NIS2 Art. 44")

    # regulators / authorities
    regulators = [
        ("ec", "European Commission", "EU", ("platform", "digital-markets"),
         "https://commission.europa.eu", [("enforces", "dsa"), ("enforces", "dma")]),
        ("edpb", "European Data Protection Board", "EU", ("data",),
         "https://edpb.europa.eu", [("enforces", "gdpr"), ("established_by", "gdpr")]),
        ("edps", "European Data Protection Supervisor", "EU", ("data",),
         "https://edps.europa.eu", []),
        ("ai-office", "European AI Office", "EU", ("ai",),
         "https://digital-strategy.ec.europa.eu/en/policies/ai-office",
         [("enforces", "ai-act"), ("established_by", "ai-act")]),
        ("enisa", "ENISA (EU Agency for Cybersecurity)", "EU", ("cyber",),
         "https://www.enisa.europa.eu", []),
        ("berec", "BEREC", "EU", ("platform",), "https://www.berec.europa.eu", []),
        ("cnil", "CNIL", "FR", ("data",), "https://www.cnil.fr", [("enforces", "gdpr")]),
        ("bfdi", "BfDI", "DE", ("data",), "https://www.bfdi.bund.de", [("enforces", "gdpr")]),
        ("dpc-ie", "Data Protection Commission (Ireland)", "IE", ("data",),
         "https://www.dataprotection.ie", [("enforces", "gdpr")]),
    ]
    for code, name, jur, domains, url, rels in regulators:
        w.add(Entity(code, name, EntityKind.REGULATOR, url=url,
                     jurisdiction=jur, domains=domains))
        for rel, target in rels:
            conn = C.ENFORCES if rel == "enforces" else C.ESTABLISHED_BY
            w.connect(code, conn, target, basis="mandate")

    # standards / internet-governance bodies
    bodies = [
        ("iso", "ISO", ("standards",), "https://www.iso.org", None),
        ("iec", "IEC", ("standards",), "https://www.iec.ch", None),
        ("cen-cenelec", "CEN-CENELEC", ("standards",), "https://www.cencenelec.eu", "iso"),
        ("etsi", "ETSI", ("standards", "cyber"), "https://www.etsi.org", None),
        ("nist", "NIST", ("standards", "cyber"), "https://www.nist.gov", None),
        ("icann", "ICANN", ("platform", "governance"), "https://www.icann.org", None),
        ("w3c", "W3C", ("platform", "governance"), "https://www.w3.org", None),
        ("ietf", "IETF", ("platform", "governance"), "https://www.ietf.org", None),
    ]
    for code, name, domains, url, equiv in bodies:
        w.add(Entity(code, name, EntityKind.STANDARDS_BODY, url=url, domains=domains))
        if equiv:
            w.connect(code, C.EQUIVALENT_TO, equiv,
                      basis="CEN-ISO Vienna Agreement (1991)")
    # standards body that develops harmonised standards for the AI Act
    w.connect("cen-cenelec", C.DESCENDS_FROM, "ai-act",
              basis="JTC 21 harmonised standards request")
    return w

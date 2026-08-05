# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P0 relational pass — derive the law *between* the nodes of the world corpus.

`world_corpus_loader` gets the entities in (473) but only four containment edge
types. This module enriches the map with the relations that make it a legal graph
rather than an inventory:

  * the **EU acquis** merged in from the companion's `instruments.csv`
    (GDPR, AI Act, DSA … with `applies_in` EU and the `supersedes` chain);
  * **memberships** — UN, Council of Europe, OECD, WTO, ASEAN, African Union
    (curated lists, structurally stable facts);
  * **regulators connected** — `supervises` their jurisdiction and `enforces` the
    instruments of that jurisdiction in their domain (DPAs additionally enforce
    the GDPR where their state is an EU member);
  * **treaty bindings** — `party_to` ECHR / Berne / Budapest for curated state
    sets; WTO members `bound_by` TRIPS;
  * **adequacy** — EU `equivalent_to` the jurisdictions holding a GDPR Art. 45
    adequacy decision;
  * **inter-instrument lineage** — WCT/WPPT/TRIPS `descends_from` Berne,
    Convention 108+ from 108, UK GDPR from GDPR, CPRA from CCPA;
  * **standards → instruments** — `presumes_conformity` (M/593 → AI Act,
    ETSI EN 319 4xx → eIDAS).

Every derived edge carries a `basis`; edges whose membership/ratification status
moves over time carry an explicit "verify current status" marker. Curated facts
are stable, pre-cutoff structural data; nothing here is guessed.

Internal by design: an enrichment pass over the world corpus, consumed downstream; not operator-queryable.
"""

from __future__ import annotations

from typing import Optional

from .legal_connection import Connection
from .legal_world import Entity, EntityKind, WorldMap
from .world_corpus_loader import EU27


# ── curated membership lists (intersected with states actually in the map) ────

_COE = set(EU27) | {"UK", "CH", "TR", "UA"}            # Russia expelled 2022
_OECD = {"US", "UK", "CA", "MX", "CL", "CO", "JP", "KR", "AU", "NZ", "IL",
         "TR", "CH", "AT", "BE", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
         "HU", "IE", "IT", "LV", "LT", "LU", "NL", "PL", "PT", "SK", "SI",
         "ES", "SE"}
_WTO = set(EU27) | {"US", "UK", "CH", "JP", "KR", "CN", "IN", "AU", "NZ", "CA",
                    "BR", "AR", "MX", "CL", "CO", "PE", "ZA", "NG", "KE", "EG",
                    "GH", "RW", "UG", "TH", "ID", "MY", "VN", "PH", "SG", "SA",
                    "AE", "QA", "BH", "IL", "TR", "RU", "UA"}
_ASEAN = {"SG", "TH", "ID", "MY", "VN", "PH"}
_AFRICAN_UNION = {"ZA", "NG", "KE", "EG", "GH", "RW", "UG"}
_BERNE = set(EU27) | {"US", "UK", "CH", "JP", "KR", "CN", "IN", "AU", "CA", "BR",
                      "MX", "ZA", "NG", "EG", "RU", "TR", "IL", "SG", "TH", "ID",
                      "MY", "VN", "PH", "NZ", "AR", "CL", "CO", "PE", "SA", "AE",
                      "QA", "KE", "GH"}
_BUDAPEST = set(EU27) | {"US", "UK", "JP", "CH", "TR", "CA", "AU"}
_ADEQUACY = {"JP", "UK", "CH", "NZ", "AR", "IL", "KR", "CA", "US"}   # Art. 45 (US: DPF)

# regulator code → (jurisdiction, domains it supervises)
_REGULATORS = {
    "cnil": ("FR", {"privacy", "data"}), "anssi": ("FR", {"cyber"}),
    "bfdi": ("DE", {"privacy", "data"}), "bsi": ("DE", {"cyber"}),
    "ico": ("UK", {"privacy", "data"}), "ncsc": ("UK", {"cyber"}),
    "garante": ("IT", {"privacy", "data"}), "aepd": ("ES", {"privacy", "data"}),
    "pipc": ("KR", {"privacy"}), "ppc": ("JP", {"privacy"}),
    "pdpc": ("SG", {"privacy"}), "cac": ("CN", {"privacy", "data", "cyber", "ai", "platform"}),
    "meity": ("IN", {"privacy", "ai"}), "anpd": ("BR", {"privacy"}),
    "opc": ("CA", {"privacy"}), "oaic": ("AU", {"privacy"}),
    "esafety-commissioner": ("AU", {"platform"}), "acma": ("AU", {"platform"}),
    "ftc": ("US", {"privacy"}), "fcc": ("US", {"platform"}),
    "cisa": ("US", {"cyber"}), "cppa": ("US", {"privacy"}),
    "edpb": ("EU", {"privacy", "data"}), "edps": ("EU", {"privacy", "data"}),
    "enisa": ("EU", {"cyber"}), "berec": ("EU", {"platform"}),
}
_DPA_DOMAINS = {"privacy", "data"}


def _find(world: WorldMap, *needles: str, kind: Optional[EntityKind] = None) -> Optional[str]:
    """Locate an entity code by case-insensitive name substring(s). Among multiple
    matches, prefer the SHORTEST name — 'AI Act' must beat 'AI Act standardisation
    request M/593'."""
    best: Optional[tuple[int, str]] = None
    for e in world.entities.values():
        if kind is not None and e.kind is not kind:
            continue
        name = e.name.lower()
        if all(n.lower() in name for n in needles):
            cand = (len(name), e.code)
            if best is None or cand < best:
                best = cand
    return best[1] if best else None


def _domains_match(domains, wanted: set) -> bool:
    ds = {str(d).lower() for d in (domains or ())}
    return any(any(w in d or d in w for d in ds) for w in {x.lower() for x in wanted})


def enrich(world: WorldMap) -> dict:
    """Apply the relational pass to a loaded world map. Returns a stats dict."""
    stats: dict[str, int] = {}

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    states = {e.code for e in world.entities.values() if e.kind is EntityKind.STATE}

    # 0. EU acquis from the companion registry (instruments.csv)
    from .regulatory_population import load_instruments, CODE, DOMAIN
    try:
        inst = load_instruments()
    except FileNotFoundError:
        inst = {}
    for celex, row in inst.items():
        code = CODE.get(celex, celex.lower())
        if code not in world.entities:
            world.add(Entity(code=code, name=row["short"], kind=EntityKind.INSTRUMENT,
                             url=row.get("source") or None, jurisdiction="EU",
                             domains=DOMAIN.get(code, ()),
                             facets={"celex": celex, "in_force_from": row.get("in_force_from", "")}))
            bump("acquis_instruments")
        world.connect(code, Connection.APPLIES_IN, "EU",
                      basis="directly applicable / transposed (EU acquis)")
        bump("applies_in")
        sup = row.get("superseded_by")
        if sup and sup in CODE:
            world.connect(CODE[sup], Connection.SUPERSEDES, code,
                          basis=row.get("note", "supersession per instruments.csv"))
            bump("supersedes")

    # 1. memberships (only for states present in the map)
    def member(codes: set, org_code: Optional[str], basis: str):
        if not org_code:
            return
        for c in sorted(codes & states):
            world.connect(c, Connection.MEMBER_OF, org_code, basis=basis)
            bump("member_of")

    member(states, _find(world, "UN", kind=EntityKind.INTERNATIONAL_REGIME) or "un",
           "UN member state")
    member(_COE, "coe", "Council of Europe member (RU expelled 2022)")
    member(_OECD, "oecd", "OECD member")
    member(_WTO, "wto", "WTO member — verify accession status for edge cases")
    member(_ASEAN, _find(world, "asean") or _find(world, "southeast asian"), "ASEAN member")
    member(_AFRICAN_UNION, "au", "African Union member")

    # 2. regulators: supervise their jurisdiction; enforce its in-domain instruments
    gdpr = "gdpr" if "gdpr" in world.entities else None
    for reg, (jur, domains) in _REGULATORS.items():
        if reg not in world.entities:
            continue
        if jur in world.entities:
            world.connect(reg, Connection.SUPERVISES, jur,
                          basis=f"national/EU supervisory authority ({'/'.join(sorted(domains))})")
            bump("supervises")
        for e in world.entities.values():
            if e.kind is EntityKind.INSTRUMENT and e.jurisdiction == jur \
                    and _domains_match(e.domains, domains):
                world.connect(reg, Connection.ENFORCES, e.code,
                              basis="supervisory mandate over this domain")
                bump("enforces")
        # member-state DPAs also enforce the GDPR
        if gdpr and domains & _DPA_DOMAINS and (jur in EU27 or jur == "EU"):
            world.connect(reg, Connection.ENFORCES, gdpr, basis="GDPR Art. 51/55")
            bump("enforces")

    # 3. treaty bindings
    echr = _find(world, "echr") or _find(world, "european convention on human rights")
    berne = _find(world, "berne")
    budapest = _find(world, "budapest")
    trips = _find(world, "trips")
    def party(codes: set, treaty: Optional[str], basis: str):
        if not treaty:
            return
        for c in sorted(codes & states):
            world.connect(c, Connection.PARTY_TO, treaty, basis=basis)
            bump("party_to")
    party(_COE, echr, "ECHR contracting party (CoE membership)")
    party(_BERNE, berne, "Berne Union member — near-universal; verify for edge cases")
    party(_BUDAPEST, budapest, "Budapest Convention party — verify current ratification")
    if trips:
        for c in sorted(_WTO & states):
            world.connect(c, Connection.BOUND_BY, trips,
                          basis="TRIPS binds WTO members (Annex 1C)")
            bump("bound_by")

    # 4. adequacy (EU → third countries, GDPR Art. 45)
    if "EU" in world.entities:
        for c in sorted(_ADEQUACY & states):
            world.connect("EU", Connection.EQUIVALENT_TO, c,
                          basis="GDPR Art. 45 adequacy decision (US: EU-US Data Privacy "
                                "Framework, certified organisations; CA: commercial/PIPEDA) "
                                "— verify current status")
            bump("equivalent_to")

    # 5. inter-instrument lineage
    for child_needles, parent_needles, basis in [
        (("wct",), ("berne",), "special agreement under Berne (WCT Art. 1)"),
        (("wppt",), ("berne",), "Berne-family neighbouring-rights treaty"),
        (("trips",), ("berne",), "TRIPS Art. 9 incorporates Berne Arts. 1–21"),
        (("convention 108+",), ("convention 108", "data protection"), "amending protocol (CETS 223)"),
        (("uk gdpr",), ("general data protection",), "retained EU law post-Brexit"),
        (("cpra",), ("ccpa",), "CPRA amends and extends the CCPA"),
    ]:
        child = _find(world, *child_needles)
        parent = _find(world, *parent_needles)
        if child and parent and child != parent:
            world.connect(child, Connection.DESCENDS_FROM, parent, basis=basis)
            bump("descends_from")

    # 6. standards → instruments (presumption of conformity)
    for std_needles, inst_needles, basis in [
        (("m/593",), ("ai act",), "Commission standardisation request M/593 → harmonised standards"),
        (("319 401",), ("eidas",), "ETSI EN 319 4xx trust-service standards under eIDAS"),
    ]:
        std = _find(world, *std_needles)
        target = _find(world, *inst_needles)
        if std and target and std != target:
            world.connect(std, Connection.PRESUMES_CONFORMITY, target, basis=basis)
            bump("presumes_conformity")

    stats["total_edges"] = len(world.edges)
    return stats


def build_enriched_world():
    """Convenience: loader + relational pass in one call."""
    from .world_corpus_loader import build_world
    w = build_world()
    stats = enrich(w)
    return w, stats

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Contract intake — one document in, one ContractInstance out.

The end-to-end deterministic pipeline the click-through workbench (P3) sits
on: take contract text, extract what Phase-1 can extract *confidently*, leave
everything else visibly empty, and assemble the full execution surface:

    text ──> ContractIntake
               ├─ contract type        (keyword classification; "" = untyped)
               ├─ parties              (parenthetical defined-term roles)
               ├─ governing law        (clause pattern → jurisdiction code)
               ├─ effective date       (ISO date near an effective/signing cue)
               ├─ defined terms        (defined_terms.extract_defined_terms)
               ├─ clause rules         (rule_extractor + attach_predicates)
               └─ ContractInstance + spans placed + obligations instantiated

Cold-start posture (the production reality: contracts come from users, the
dev corpus is templates): every extraction carries a confidence; everything
below its floor is dropped to "not extracted" — `ContractInstance.
missing_fields()` is the intake card's gap report — and the whole document
always lands, at worst as `contract-untyped` with full text preserved. The
LLM seam (`rule_extractor_llm`) can widen coverage later; it feeds the same
assembly and the same gates. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .instance import ContractInstance, ContractRegistry, PartyRef
from ..defined_terms import DefinedTermsRegistry, extract_defined_terms
from ..obligation_runtime import ObligationRegistry
from workspaces.adapters.solver.predicate import attach_predicates
from ..rule_extractor import RuleFacet, extract_rules
from ..rule_registry import RuleRegistry
from workspaces.adapters.solver.temporal import Date, TemporalError

__all__ = ["ContractIntake", "intake_contract", "ingest_contract"]


# ── contract-type classification (keyword, conservative) ──────────────────────

_TYPE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # amendment first: an amendment TO a licence quotes licence vocabulary,
    # but its own type is amendment (ties resolve to the earlier entry).
    ("amendment", ("amendment no", "nachtrag", "addendum", "zum vertrag vom",
                   "zum lizenzvertrag", "wird wie folgt neu gefasst",
                   "is amended as follows")),
    # NOTE: cues are CONCEPT words only — statute strings ("art. 28") are
    # jurisdiction content and live in packs (neutrality audit 2026-06-05).
    ("dpa", ("data processing agreement", "auftragsverarbeitung",
             "processor", "auftragsverarbeiter")),
    ("nda", ("non-disclosure", "confidentiality agreement",
             "geheimhaltungsvereinbarung", "verschwiegenheit")),
    ("licence", ("license agreement", "licence agreement", "lizenzvertrag",
                 "grant of license", "grant of licence", "licensor", "lizenzgeber")),
    ("msa", ("master services agreement", "master service agreement",
             "rahmenvertrag", "statement of work")),
    ("employment", ("employment agreement", "arbeitsvertrag")),
)


_DE_CUES = re.compile(
    r"\b(?:der|die|das|und|oder|nicht|muss|wird|werden|zwischen|"
    r"Vertrag|gem(?:ä|ae)ß|personenbezogene|Auftragsverarbeiter|"
    r"Verantwortlicher|Daten|sowie|gemäss)\b", re.I)
_EN_CUES = re.compile(
    r"\b(?:the|and|or|shall|must|will|between|agreement|hereby|"
    r"party|including|pursuant|whereas)\b", re.I)


def detect_language(text: str) -> str:
    """Return 'de' or 'en' by function-word frequency. Deterministic, stdlib.
    Used only to set the ``language`` field when the caller did not assert one;
    an explicit caller value is always honoured. Tie/empty falls back to 'en'."""
    return "de" if len(_DE_CUES.findall(text)) > len(_EN_CUES.findall(text)) else "en"


def _language_confidence(text: str) -> float:
    """How sure the language guess is: the cue-count margin over the total,
    floored at 0.5 (a coin-flip when the signal is thin), capped at 0.95."""
    de, en = len(_DE_CUES.findall(text)), len(_EN_CUES.findall(text))
    total = de + en
    if total == 0:
        return 0.5
    return round(min(0.95, 0.5 + 0.5 * abs(de - en) / total), 2)


def classify_contract_type(text: str) -> tuple[str, float]:
    """Best-scoring type with >=2 signal hits, else ("", 0.0) — untyped is a
    representable state, not an error."""
    low = text.lower()
    best, best_hits = "", 0
    for ctype, signals in _TYPE_SIGNALS:
        hits = sum(1 for s in signals if s in low)
        if hits > best_hits:
            best, best_hits = ctype, hits
    if best_hits >= 2:
        return best, min(0.6 + 0.1 * best_hits, 0.9)
    return "", 0.0


# ── party extraction ──────────────────────────────────────────────────────────

_ROLE_WORDS = frozenset({
    "processor", "controller", "licensor", "licensee", "supplier", "customer",
    "client", "contractor", "employer", "employee", "disclosing-party",
    "receiving-party", "recipient", "discloser", "provider", "vendor",
    "counterparty", "partner", "distributor", "reseller", "landlord", "tenant",
    "lender", "borrower",
    "auftragsverarbeiter", "verantwortlicher", "lizenzgeber", "lizenznehmer",
    "auftraggeber", "auftragnehmer", "auftraggeberin", "auftragnehmerin",
    "vermieter", "mieter", "verleiher", "entleiher",
})

# "between X and Y" header (EN + DE)
_BETWEEN = re.compile(
    r"\b(?:between|zwischen)\s+(?P<a>[A-ZÄÖÜ][^,()\n]{2,80}?)\s*(?:\(|,)"
    r".{0,200}?\b(?:and|und)\s+(?P<b>[A-ZÄÖÜ][^,()\n]{2,80}?)\s*(?:\(|,|\n)",
    re.S)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:60]


# Bare legal-form tokens are not names. A party whose extracted "name" is only
# its legal form (OCR ate the rest) is dropped — a missing party is visible, a
# party called "ApS" is garbage.
_LEGAL_FORMS = frozenset({
    "gmbh", "ag", "kg", "se", "ug", "ohg", "gbr", "ev", "inc", "ltd", "llc",
    "llp", "plc", "bv", "nv", "sa", "sarl", "sas", "aps", "as", "ab", "oy",
    "co", "corp", "company",
})


def extract_parties(text: str) -> list[PartyRef]:
    """Parties from parenthetical defined terms whose term is a known role
    word — '… ACME GmbH (the "Processor")' is a party assertion the document
    itself makes; a name without a role term is not enough to assert a party."""
    out: list[PartyRef] = []
    seen: set[str] = set()
    for dt in extract_defined_terms(text):
        role = _slugify(dt.term)
        if role not in _ROLE_WORDS:
            continue
        # OCR repair on the name side only: rejoin hyphen line-breaks, fold
        # whitespace runs (scanned PDFs shatter both).
        name = re.sub(r"-\s*\n\s*", "", dt.definition)
        name = re.sub(r"\s+", " ", name).strip().rstrip(",.;")
        # the defining text for a parenthetical is the preceding name phrase;
        # take the trailing capitalised run as the party name
        m = re.search(r"([A-ZÄÖÜ][\w&.ÄÖÜäöüß-]*(?:\s+[A-ZÄÖÜ&][\w&.ÄÖÜäöüß-]*)*)\s*$", name)
        if m:
            name = m.group(1)
        code = _slugify(name)
        if not code or code in seen:
            continue
        if all(tok in _LEGAL_FORMS for tok in code.split("-")):
            continue                    # legal form without a name = no party
        seen.add(code)
        out.append(PartyRef(entity_code=code, role=role, name=name))
    return out


# ── governing law ─────────────────────────────────────────────────────────────

_LAW_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"laws?\s+of\s+(?:the\s+)?Federal\s+Republic\s+of\s+Germany|"
                r"deutschem\s+Recht|Recht\s+der\s+Bundesrepublik\s+Deutschland|"
                r"German\s+law", re.I), "DE"),
    (re.compile(r"laws?\s+of\s+(?:the\s+Republic\s+of\s+)?Austria|"
                r"österreichischem\s+Recht", re.I), "AT"),
    (re.compile(r"laws?\s+of\s+Switzerland|Schweizer(?:isches)?\s+Recht", re.I), "CH"),
    (re.compile(r"laws?\s+of\s+France", re.I), "FR"),
    (re.compile(r"laws?\s+of\s+(?:England|England\s+and\s+Wales)", re.I), "UK"),
    (re.compile(r"laws?\s+of\s+(?:the\s+State\s+of\s+)?(?:Delaware|California|New\s+York)", re.I), "US"),
    (re.compile(r"laws?\s+of\s+(?:the\s+)?Netherlands", re.I), "NL"),
    (re.compile(r"laws?\s+of\s+Ireland", re.I), "IE"),
)
_LAW_CUE = re.compile(r"governed\s+by|governing\s+law|unterliegt|anwendbares\s+Recht|"
                      r"gilt\s+.{0,30}Recht", re.I)


def extract_governing_law(text: str) -> tuple[Optional[str], float]:
    """Jurisdiction code from a governing-law clause: requires BOTH a cue
    phrase and a recognised jurisdiction in the same vicinity.

    Conflict abstention: if the document carries governing-law clauses that
    point at DIFFERENT jurisdictions (split governing-law drafting, carve-outs
    for IP disputes, sloppy boilerplate merges), this returns ``(None, 0.0)``.
    Which law governs what is then a human question on the decision surface —
    picking the first match would be silently wrong."""
    found: set[str] = set()
    for m in _LAW_CUE.finditer(text):
        window = text[max(0, m.start() - 80): m.end() + 160]
        for rx, code in _LAW_PATTERNS:
            if rx.search(window):
                found.add(code)
    if len(found) == 1:
        return next(iter(found)), 0.9
    return None, 0.0


# ── effective date ────────────────────────────────────────────────────────────

_DATE_TOKEN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_DE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\b")
_DATE_EN_LONG = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),?\s+(\d{4})\b")
_DATE_EN_DAYFIRST = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b")
_DATE_DE_LONG = re.compile(
    r"\b(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember)\s+(\d{4})\b")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}
_MONTHS_DE = {m: i + 1 for i, m in enumerate(
    ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
     "September", "Oktober", "November", "Dezember"])}
_EFFECTIVE_CUE = re.compile(
    r"effective\s+(?:as\s+of|date|on)|comes?\s+into\s+(?:force|effect)|"
    r"tritt\s+am|mit\s+Wirkung\s+(?:zum|vom)|in\s+Kraft", re.I)


def _date_near(text: str, pos: int, span: int = 200) -> Optional[Date]:
    window = text[max(0, pos - 40): pos + span]
    m = _DATE_TOKEN.search(window)
    if m:
        try:
            return Date(m.group(1))
        except TemporalError:
            pass
    m = _DATE_EN_LONG.search(window)
    if m:
        try:
            return Date(f"{int(m.group(3)):04d}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}")
        except TemporalError:
            pass
    m = _DATE_EN_DAYFIRST.search(window)
    if m:
        try:
            return Date(f"{int(m.group(3)):04d}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}")
        except TemporalError:
            pass
    m = _DATE_DE_LONG.search(window)
    if m:
        try:
            return Date(f"{int(m.group(3)):04d}-{_MONTHS_DE[m.group(2)]:02d}-{int(m.group(1)):02d}")
        except TemporalError:
            pass
    m = _DATE_DE.search(window)
    if m:
        try:
            return Date(f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}")
        except TemporalError:
            pass
    return None


def extract_effective_date(text: str) -> tuple[Optional[Date], float]:
    """A typed date only when it sits next to an effectiveness cue. A document
    full of dates with no cue yields None — the intake card says so."""
    for m in _EFFECTIVE_CUE.finditer(text):
        d = _date_near(text, m.start())
        if d is not None:
            return d, 0.9
    return None, 0.0


# ── meta-clause filter ────────────────────────────────────────────────────────
# A duty binds a party's conduct. Clauses whose grammatical subject is the
# agreement itself (governing law, entire agreement) or an abstract quantum
# (liability caps) are contract-meta: real clauses, not conduct duties — they
# must not enter the obligation runtime as duties.

_META_SUBJECT = re.compile(
    r"\b(?:this\s+)?(?:agreement|contract|vereinbarung|vertrag)\b|"
    r"\bliabilit(?:y|ies)\b|\bhaftung\b|\bdispute\b", re.I)
_META_ACTION = re.compile(r"\bgoverned\s+by\b|\bunterliegt\b", re.I)


def _is_meta_clause(f: RuleFacet) -> bool:
    return bool(_META_SUBJECT.search(f.subject or "")
                or _META_ACTION.search(f.action or ""))


# ── mandatory-content check (generic engine; checklists are PACK DATA) ───────
# Extraction answers "what does the document say"; completeness doctrine asks
# "what must it say that it doesn't". The CONCEPT (a contract type can have
# prescribed minimum content) is universal → the engine lives here. WHAT is
# prescribed, and by which statute, is jurisdiction content → it lives in
# data files (`data/packs/`), loaded and supplied by a caller, an ND, or a
# jurisdiction pack. The substrate applies NO checklist on its own. Cue
# absence is a FINDING for the decision surface, never a verdict.

REFERENCE_PACKS_DIR = Path(__file__).resolve().parent.parent / "data" / "packs"


def check_mandatory_content(text: str,
                            checklist: tuple[tuple[str, tuple[str, ...]], ...],
                            name: str = "") -> dict:
    """Generic engine: which checklist items have NO recognisable cue in the
    text. Returns {name, present: [...], not_found: [...]} — 'not_found'
    means exactly that: not found by cue, to be confirmed or located by the
    reviewer. The checklist (and its legal authority) comes from the caller."""
    low = text.lower()
    present, missing = [], []
    for item, cues in checklist:
        (present if any(c in low for c in cues) else missing).append(item)
    return {"name": name, "present": present, "not_found": missing}


def load_checklist(path) -> tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]]:
    """Load one checklist data file → (contract_type, name, checklist).
    Pack data is JSON: {name, contract_type, items: [{item, cues}]}."""
    import json as _json
    d = _json.loads(Path(path).read_text(encoding="utf-8"))
    checklist = tuple((it["item"], tuple(it["cues"])) for it in d["items"])
    return d["contract_type"], d["name"], checklist


# ── Fundstelle (pinpoint) derivation ──────────────────────────────────────────
# A span without its citation point is half a span: "the processor shall
# notify…" must be addressable as "§ 2 (2)" or "Clause 3.1", not byte 1042.
# Labels are taken only from structural headings at line starts — deterministic
# and verifiable against the document. Sentence numbering (S. 1, S. 2) is not
# derived in Phase-1: rule extraction skips non-operative sentences, so a
# counted index could be silently wrong — the worst kind of Fundstelle. The
# label level is reliable; the Satz level waits for a segmentation that earns it.

_SECTION_LABEL = re.compile(
    r"(?m)^\s*(?P<sect>§\s*\d+[a-z]?|Artikel\s+\d+|Article\s+\d+|"
    r"Ziffer\s+\d+|Clause\s+\d+(?:\.\d+)*|\d+(?:\.\d+)+|\d+\s*\.(?=\s+\S))")
_PARA_LABEL = re.compile(r"(?m)^\s*(?P<para>\(\d+\))")


def derive_pinpoint(text: str, start: Optional[int]) -> str:
    """The nearest structural label(s) governing position ``start``:
    '§ 2 (4)', 'Clause 3.1', '4.', or '' when the document has no structure
    before that point (no label is better than a guessed one)."""
    if start is None or start < 0:
        return ""
    sect = sect_end = None
    for m in _SECTION_LABEL.finditer(text, 0, start + 1):
        if m.start() <= start:
            sect, sect_end = re.sub(r"\s+", " ", m["sect"].strip()), m.end()
    para = None
    if sect is not None:
        for m in _PARA_LABEL.finditer(text, sect_end, start + 1):
            if m.start() <= start:
                para = m["para"]
    if sect is None:
        return ""
    return f"{sect} {para}" if para else sect


# ── the pipeline ──────────────────────────────────────────────────────────────

@dataclass
class ContractIntake:
    """Everything the intake produced, with per-field confidence — the
    S1 intake card renders exactly this."""

    instance: ContractInstance
    confidences: dict[str, float] = field(default_factory=dict)
    defined_terms: list[dict] = field(default_factory=list)
    rules: list[RuleFacet] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"instance": self.instance.to_dict(),
                "confidences": self.confidences,
                "defined_terms": self.defined_terms,
                "rules": [r.to_dict() for r in self.rules],
                "missing": self.missing}


def intake_contract(text: str, *, contract_id: str = "",
                    language: Optional[str] = None,
                    checklists: Optional[dict] = None) -> ContractIntake:
    """Pure extraction: text → ContractIntake. No persistence, no audit —
    that's :func:`ingest_contract`'s job. Everything sub-floor is left empty.

    ``language``: pass 'de'/'en' to assert it; leave None to auto-detect
    (function-word frequency). A wrong stored language silently mis-routes
    downstream consumers, so the field is never blindly defaulted to 'en'.

    ``checklists``: optional ``{contract_type: (name, checklist)}`` from a
    jurisdiction pack / ND / caller. The substrate applies no jurisdiction's
    mandatory-content standard on its own (jurisdiction-neutral by default)."""
    if language is None:
        language = detect_language(text)
    doc_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    cid = contract_id or ("contract-" + doc_hash[7:19])

    ctype, c_conf = classify_contract_type(text)
    parties = extract_parties(text)
    law, law_conf = extract_governing_law(text)
    eff, eff_conf = extract_effective_date(text)
    terms = extract_defined_terms(text)
    # Ungated extraction: the per-sentence normative fingerprint exists for
    # mixed/unknown content; a document in the contract intake is normative
    # (same reasoning as place_legal_text for statutes). The fingerprint was
    # suppressing real clauses in plain contract prose, esp. German.
    rules = [f for f in extract_rules(text, gated_by_fingerprint=False)
             if not _is_meta_clause(f)]
    attach_predicates(rules, language=language)
    from ..hohfeld import attach_incidents
    attach_incidents(rules, roles=_ROLE_WORDS)

    facets: dict = {"intake": "phase-1"}
    if checklists and ctype in checklists:
        name, checklist = checklists[ctype]
        facets["mandatory_content"] = check_mandatory_content(
            text, checklist, name=name)
    inst = ContractInstance(
        contract_id=cid, version=1,
        contract_type=ctype, parties=tuple(parties),
        effective_date=eff, governing_law=law,
        jurisdiction_anchors=(law,) if law else (),
        document_hash=doc_hash, language=language,
        facets=facets)
    return ContractIntake(
        instance=inst,
        confidences={"contract_type": c_conf,
                     # evidence-derived, not a constant: a two-sided contract
                     # with both roles found is strong; one party is weak;
                     # none is 0. (Every party here is role-labelled by
                     # construction — see extract_parties.)
                     "parties": (0.9 if len(parties) >= 2
                                 else 0.6 if parties else 0.0),
                     "governing_law": law_conf,
                     "effective_date": eff_conf,
                     # detection confidence for the language field itself:
                     # margin between DE and EN cue counts, normalised.
                     "language": _language_confidence(text)},
        defined_terms=[t.to_dict() for t in terms],
        rules=rules,
        missing=inst.missing_fields())


def ingest_contract(folder, text: str, *, contract_id: str = "",
                    language: Optional[str] = None, source_document: str = "",
                    actor: str = "ingest", checklists: Optional[dict] = None,
                    log_root=None) -> dict[str, Any]:
    """The full intake: extract, register the ContractInstance (projected onto
    the world map), register defined terms, place every clause as a versioned
    span-norm, instantiate obligations. Returns the assembled summary the
    workbench renders. Every sub-store is idempotent, so re-ingesting the same
    bytes is a no-op with the same ids."""
    intake = intake_contract(text, contract_id=contract_id, language=language,
                             checklists=checklists)
    inst = intake.instance

    contracts = ContractRegistry(folder, log_root=log_root)
    from ..legal_corpus import EntityRegistry
    ents = EntityRegistry(folder, log_root=log_root)
    reg_out = contracts.register(inst, actor=actor, entity_registry=ents)

    terms_reg = DefinedTermsRegistry(folder, log_root=log_root)
    terms_out = terms_reg.register_from_text(inst.ref, text, actor=actor)

    spans = RuleRegistry(folder, log_root=log_root)
    placed = []
    for f in intake.rules:
        span_text = (f.raw_sentence or "").strip()
        if not span_text:
            continue
        idx = text.find(span_text)
        r = spans.place_span(
            span_text, source_document=source_document or inst.contract_id,
            start=idx if idx >= 0 else None,
            end=(idx + len(span_text)) if idx >= 0 else None,
            kind="clause", facet=f, source=actor,
            pinpoint=derive_pinpoint(text, idx if idx >= 0 else None),
            document_hash=inst.document_hash, document_version=inst.version)
        placed.append(r)

    obligations = ObligationRegistry(folder, log_root=log_root)
    # hand the runtime each placed span enriched with its predicate struct
    rules_for_runtime = []
    facet_by_text = {(f.raw_sentence or "").strip(): f for f in intake.rules}
    for r in placed:
        f = facet_by_text.get(r["span"]["text"])
        norm = dict(r["norm"])
        if f is not None and f.condition_struct is not None:
            norm["condition_struct"] = f.condition_struct
        rules_for_runtime.append({"id": r["id"], "norm": norm})
    oblig_out = obligations.instantiate(inst, rules_for_runtime, actor=actor)

    return {"contract": dict(reg_out, ref=inst.ref),
            "confidences": intake.confidences,
            "missing": intake.missing,
            "defined_terms": terms_out,
            "spans_placed": [{"id": r["id"], "status": r["status"]} for r in placed],
            "obligations": oblig_out}

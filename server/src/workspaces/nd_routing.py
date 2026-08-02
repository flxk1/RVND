# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Classifier → ND dispatch wiring — Phase B2.

After an Inbox file has been read into a base ``ExtractedFile``, the routing
layer classifies it and fans the content out to whichever Nightingale-D
workspaces claim it. Each matching ND extracts its own typed
``ProblemSolutionPair`` shapes; all of them get folded back into the
``ExtractedFile.pairs`` list and written to the folder's memory by the
``InboxWatcher``.

Conceptual layers:

.. code-block:: text

    file in Inbox
        │
        ▼
    base extractor (DefaultExtractor)            ← B1: one default pair, file metadata
        │
        ▼
    classifier (DefaultClassifier or custom)     ← B2: primary_type + facets
        │
        ▼
    NDRouter.dispatch(content, classification)   ← B2: fan-out
        │
        ├─→ nd-math       (if classification matches its handles_types/facets)
        ├─→ nd-ai-act     (...)
        └─→ nd-contracts  (...)
        │
        ▼
    Σ all pairs → WorkspaceMemory.remember()           ← writes to the folder's scoped memory

NDs themselves stay independent — they're matched by declarative
``handles_types`` / ``handles_facets`` lists, so the router never needs to
know about specific ND implementations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Classification + Classifier
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    """The classifier's read on a piece of content.

    Returned by :class:`Classifier.classify`. Consumed by :class:`NDRouter`
    to pick which NDs are dispatched.
    """

    primary_type: str                   # "contract" | "math" | "code" | "letter" | "policy" | "unknown"
    facets: list[str] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class Classifier(Protocol):
    """The protocol every classifier implements.

    A classifier reads (potentially partial) document content + metadata
    and returns a typed read. Implementations can be:

    - Pure keyword matching (DefaultClassifier — what we ship).
    - Local-LLM-backed (Phi-3.5 etc., via the privacy-lock-gated path).
    - Wrappers around the ``workspace-doc-extractor:doc-extractor-classifier``
      skill (full multi-facet classification).
    """

    def classify(
        self,
        content: str,
        *,
        file_path: str | None = None,
        mime_type: str = "",
    ) -> Classification:
        ...


# ---------------------------------------------------------------------------
# DefaultClassifier — keyword + mime heuristics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Normative / rule fingerprint
# ---------------------------------------------------------------------------
#
# Detection design (precision-first):
#
# A fragment is normative if it contains OPERATIVE deontic content — content
# that creates, modifies, or refers to an obligation, permission, prohibition
# or right that binds a regulated subject. The challenge is precision: many
# non-normative texts use modals ("you may want to") and many normative texts
# do not always pair a subject with a modal in one sentence (definitional
# clauses, conditional clauses).
#
# We score multiple independent SIGNALS and require either:
#   (a) a strong subject + deontic-modal pair, OR
#   (b) ≥ 2 mid-strength structural signals.
#
# Anti-signals (preamble, journalistic register, academic commentary)
# subtract — they catch the false-positive cases where surface vocabulary
# matches but the text is talking ABOUT rules rather than stating one.

_NORMATIVE_SIGNALS: dict[str, tuple[re.Pattern[str], float]] = {
    # SIGNAL 1: subject + deontic-modal pair (EN). Strong evidence on its own.
    # Matches: legal-style subject noun phrase + shall/must/may-not/shall-not.
    # The subject set is curated to legal/regulatory subjects; this rules out
    # casual "He shall return" and "I must remember".
    "deontic_subject_en": (re.compile(
        r"\b(?:"
        # Regulated-entity subjects (plural-tolerant; jurisdiction-neutral)
        r"providers?|controllers?|processors?"
        r"|operators?|deployers?|importers?"
        r"|distributors?|users?|data\s+subjects?"
        # Public bodies and authorities
        r"|member\s+states?|supervisory\s+authorit(?:y|ies)|authorit(?:y|ies)"
        r"|the\s+commission|the\s+council|the\s+parliament"
        r"|the\s+(?:competent\s+)?authority"
        # Organisations and undertakings
        r"|organi[sz]ations?|institutions?|undertakings?|banks?|firms?"
        r"|entit(?:y|ies)|manufacturers?|agenc(?:y|ies)"
        # Generic contract parties
        r"|the\s+party|the\s+parties|each\s+party|either\s+party"
        r"|neither\s+party"
        r"|licensees?|licensors?|employees?|employers?|compan(?:y|ies)"
        r"|tenants?|landlords?|buyers?|sellers?|vendors?|customers?|suppliers?|recipients?"
        # Reference noun phrases
        r"|the\s+(?:[a-z]+\s+)?(?:obligation|right|duty)"
        r"|ai\s+system|ai\s+systems|gpai|general-purpose\s+ai"
        r"|personal\s+data|the\s+(?:data\s+)?subject"
        r")"
        # Allow up to 14 words OR commas/parentheticals between subject and modal.
        r"(?:[\s,;:()\[\]\-]+[\w\-]+){0,14}?[\s,;:()\[\]\-]+"
        r"(?:shall(?:\s+not|\s+be\s+\w+able|\s+be\s+liable)?|must(?:\s+not)?|may\s+not"
        r"|may[\s,]+(?:by|in|on|after|under|with|through)"  # "the Commission may, by means of, adopt"
        r"|is\s+(?:required|prohibited|entitled|empowered)|are\s+(?:required|prohibited|entitled|empowered)"
        r"|has\s+the\s+right|have\s+the\s+right)\b",
        re.IGNORECASE | re.DOTALL), 0.55),

    # SIGNAL 2: subject + deontic-modal pair (DE). Same strength.
    # German legal text uses many forms — covered here:
    #   "müssen sicherstellen" / "muss" — present indicative obligation
    #   "dürfen … nicht / keine" — prohibition
    #   "hat … zu (verb)" / "haben … (zu verb | umzusetzen)" — duty
    #   "ist/sind verpflichtet" — duty
    #   "Wer X (Y), kann/wird ..." — passive deontic (UrhG/BGB style)
    "deontic_subject_de": (re.compile(
        r"(?:"
        # Subject + modal forms. Word gaps use [\w\-]+ to handle hyphenated
        # German compounds (KI-Systeme, Arbeitsverhältnisse). Punctuation
        # between words is tolerated.
        r"\b(?:"
        r"anbieter|verantwortliche|auftragsverarbeiter|nutzer"
        r"|mitarbeiter|arbeitgeber|arbeitnehmer|lizenznehmer|lizenzgeber"
        r"|mitgliedstaaten|aufsichtsbehörde|der\s+schuldner|der\s+gläubiger"
        r"|ki[- ]systeme?|der\s+verantwortliche|die\s+verantwortlichen"
        r")"
        r"(?:\s+[\w\-]+){0,12}?\s+"
        r"(?:müssen|muss|(?:dürfen|darf)(?:\s+[\w\-]+){0,8}?\s+(?:nicht|keine?|keinen)"
        r"|hat(?:\s+[\w\-]+){1,8}?\s+(?:zu\s+\w+|umzusetzen)"
        r"|haben(?:\s+[\w\-]+){1,8}?\s+(?:zu\s+\w+|umzusetzen)"
        r"|ist\s+verpflichtet|sind\s+verpflichtet)\b"
        # Passive-deontic UrhG/BGB pattern: "Wer X, kann/wird Y" — multiple
        # commas + intervening subclauses tolerated.
        r"|\bwer(?:[\s,;\-]+[\w\-]+){2,24}?[\s,;\-]+(?:kann|wird|ist)\s+[\w\-]+"
        r")",
        re.IGNORECASE | re.DOTALL), 0.55),

    # SIGNAL 3: article-numbering structure characteristic of statutes.
    # Two patterns: "Article 6(1)(b)" / "Art. 28(2)" (EU) and "§ 4" (DE).
    "article_numbering": (re.compile(
        r"(?:\bArt(?:icle|\.)\s*\d+(?:\(\d+\))?(?:\([a-z]\))?"
        r"|§\s*\d+(?:\s*Abs\.\s*\d+)?(?:\s+\w+G\b)?)",
        re.IGNORECASE), 0.20),

    # SIGNAL 4: statutory-definition clause. "X means …" / "ist eine Information, die" /
    # "For the purposes of this Regulation". High-precision for normative
    # definitions (binding interpretation).
    "definitional": (re.compile(
        r"(?:"
        r"\bfor\s+the\s+purposes\s+of\s+this\s+(?:regulation|directive|agreement|act)\b"
        r"|\bmeans\s+any\s+(?:information|natural\s+person|processing|act)\b"
        r"|['\"][A-Za-z][^'\"]{1,40}['\"]\s+means\b"
        r"|\bist\s+eine\s+\w+,?\s+die\b"
        r")",
        re.IGNORECASE), 0.30),

    # SIGNAL 5: proviso / exception operators. Operative-clause modifiers.
    "proviso": (re.compile(
        r"\b(?:"
        r"notwithstanding|provided\s+that|subject\s+to|without\s+prejudice\s+to"
        r"|except\s+(?:as|where|when|that)|in\s+accordance\s+with\s+this"
        r"|unbeschadet|sofern\s+nicht|vorbehaltlich|im\s+sinne\s+dies(?:er|es)"
        r")\b",
        re.IGNORECASE), 0.25),

    # SIGNAL 6: contract-operative connectives. Distinct from contract caption boilerplate.
    "contract_operative": (re.compile(
        r"\b(?:"
        r"hereby\s+(?:agrees|grants|undertakes|covenants|represents)"
        r"|in\s+consideration\s+of\s+the\s+(?:foregoing|payment)"
        r"|the\s+(?:licensee|licensor|party|company|employee)\s+(?:shall|must|may\s+not|hereby)"
        r"|payable\s+\w+\s+within\s+\d+\s+\w+\s+days"
        r")\b",
        re.IGNORECASE), 0.30),

    # SIGNAL 7: right-grant pattern. "shall have the right to …"
    "right_grant": (re.compile(
        r"\b(?:shall|are|is)\s+(?:entitled\s+to|empowered\s+to|granted\s+the\s+right)"
        r"|\bhas?\s+the\s+right\s+to\b"
        r"|\bist\s+berechtigt\b|\bhat\s+das\s+recht\b",
        re.IGNORECASE), 0.30),

    # SIGNAL 8: operative-verb statement ABOUT an article ("Article X
    # provides that / requires X to Y / prohibits / sets out / obliges").
    # Combined with an article reference this is a strong operative-content
    # signal even though the rule is being stated indirectly. Both "requires
    # that" and transitive "requires X to Y" forms.
    "operative_verb": (re.compile(
        r"\b(?:Art(?:icle|\.)\s*\d+(?:\(\d+\))?(?:\([a-z]\))?)"
        r"\s+(?:[\w\-]+\s+){0,3}?"
        r"(?:provides\s+that"
        r"|requires\s+(?:that|(?:[\w\-]+\s+){1,18}?to\s+\w+)"
        r"|obliges\s+(?:[\w\-]+\s+){1,12}?to\s+\w+"
        r"|prohibits|sets\s+out|establishes|imposes"
        r"|allows|permits|mandates|stipulates|lays\s+down)\b",
        re.IGNORECASE), 0.30),

    # SIGNAL 9: definitions-block opener — strong operative content.
    "definitions_block": (re.compile(
        r"\b(?:the\s+following\s+definitions\s+apply"
        r"|definitions(?:\s+used)?\s+in\s+this\s+(?:regulation|act|agreement|directive)"
        r"|in\s+this\s+(?:regulation|act|agreement|directive),?\s+the\s+following)\b",
        re.IGNORECASE), 0.30),
}


# Anti-signals — subtract from the score when present. These catch fragments
# that LOOK normative on the surface but are actually preamble, journalism,
# or academic commentary about rules.
_NORMATIVE_ANTI_SIGNALS: dict[str, tuple[re.Pattern[str], float]] = {
    # Preamble markers — almost never operative.
    "preamble": (re.compile(
        r"\b(?:whereas|in\s+witness\s+whereof|by\s+and\s+between"
        r"|this\s+agreement\s+is\s+entered\s+into\s+as\s+of)\b",
        re.IGNORECASE), 0.45),

    # Recital register — "it is appropriate to" / "this Regulation respects" /
    # "such as is recognised".
    "recital": (re.compile(
        r"\b(?:it\s+is\s+(?:appropriate|necessary|desirable)\s+to\s+(?:lay\s+down|harmonise|ensure)"
        r"|recognises\s+the\s+right|respects\s+the\s+fundamental"
        r"|is\s+a\s+fundamental\s+right)\b",
        re.IGNORECASE), 0.45),

    # Journalistic / temporal-narrative register — past tense, named events.
    "journalism": (re.compile(
        r"\b(?:voted\s+(?:to|on)|approved\s+the\s+\w+\s+(?:in|on)"
        r"|after\s+lengthy\s+\w+|has\s+been\s+(?:the\s+subject\s+of|debated|criticised)"
        r"|industry\s+groups\s+argued|civil-society\s+groups\s+pushed"
        r"|lobbying|negotiations\s+between)\b",
        re.IGNORECASE), 0.50),

    # Academic commentary about rules. "Scholars have debated", "addressed this question".
    "commentary": (re.compile(
        r"\b(?:scholars\s+have\s+\w+|commentators\s+(?:have|note|observe)"
        r"|addressed\s+this\s+question|the\s+\w+\s+ruling\s+(?:held|established)"
        r"|in\s+its\s+\d{4}\s+ruling|the\s+court\s+(?:held|noted|considered))\b",
        re.IGNORECASE), 0.45),

    # Bio / personal narrative register. First-person pronouns + descriptive verbs.
    "personal_narrative": (re.compile(
        r"\b(?:I\s+(?:think|believe|want|plan)|we\s+(?:should|might|could)\s+(?:plan|try|go|fly)"
        r"|the\s+kids\s+are|weather\s+permitting)\b",
        re.IGNORECASE), 0.50),

    # Marketing copy. "Trusted by", "Get started", "free trial".
    "marketing": (re.compile(
        r"\b(?:trusted\s+by|get\s+started|free\s+trial|leading\s+\w+|sign\s+up\s+today)\b",
        re.IGNORECASE), 0.40),
}


# Threshold above which a fragment is classified as normative. Tuned against
# the labeled corpus in tests/fixtures/normative_corpus.py.
NORMATIVE_THRESHOLD = 0.45


def score_normative(content: str) -> tuple[float, dict[str, bool]]:
    """Compute the normative score and which signals fired.

    Returns ``(score, signals_dict)`` where ``signals_dict`` maps every
    signal name (including anti-signals, prefixed with ``"-"``) to True/False.
    """
    signals: dict[str, bool] = {}
    score = 0.0
    for name, (pattern, weight) in _NORMATIVE_SIGNALS.items():
        if pattern.search(content):
            signals[name] = True
            score += weight
        else:
            signals[name] = False
    for name, (pattern, weight) in _NORMATIVE_ANTI_SIGNALS.items():
        key = f"-{name}"
        if pattern.search(content):
            signals[key] = True
            score -= weight
        else:
            signals[key] = False
    return (max(0.0, min(1.0, score)), signals)


# Compiled patterns for the default classifier's non-normative document types.
_TYPE_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    # primary_type, regex, confidence boost
    ("math", re.compile(
        r"(?:\b(?:theorem|lemma|proof|corollary|qed)\b|\\begin\{equation\}"
        r"|\$[^\$]+\$|\\int|\\sum|\\sqrt)",
        re.IGNORECASE), 0.85),
    ("letter", re.compile(
        r"^\s*(?:Dear|To whom it may concern|Sincerely|Yours truly)\s*,",
        re.IGNORECASE | re.MULTILINE), 0.7),
    ("code", re.compile(
        r"\b(?:def\s+\w+\s*\(|class\s+\w+\s*[:\(]|import\s+\w+|function\s+\w+)",
        re.MULTILINE), 0.75),
]


# Facet patterns — "what subject areas does this touch". Multiple may match.
_FACET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("music-rights", re.compile(
        r"\b(?:ISRC|ISWC|UrhG|DSM directive|mechanical royal|sync licence"
        r"|master rights|publishing rights|neighbouring rights|GEMA|PRO)\b",
        re.IGNORECASE)),
    ("gdpr", re.compile(
        r"\b(?:GDPR|Art\.\s*\d+\s+GDPR|data subject|controller|processor"
        r"|DPIA|DPO|lawful basis|legitimate interest)\b",
        re.IGNORECASE)),
    ("ai-act", re.compile(
        r"\b(?:AI Act|Regulation 2024/1689|high-risk AI|GPAI|Annex III"
        r"|conformity assessment|Art\.\s*\d+\s+AI Act)\b",
        re.IGNORECASE)),
    ("license", re.compile(
        r"\b(?:licence|license|licensing|grant.{0,30}right|royalty|territory"
        r"|field of use|sublicens)\b",
        re.IGNORECASE)),
    ("trademark", re.compile(
        # Trademark-specific terms only. Bare "mark" and "registration" caused
        # false positives on ordinary legal prose ("trade marks" vs "registration
        # obligations" in the AI Act). Require trademark-distinctive vocabulary.
        r"\b(?:trade\s?marks?|Nice\s+class|EUTMR|MarkenG|EUIPO"
        r"|trade\s?mark\s+(?:registration|opposition|infringement)"
        r"|likelihood\s+of\s+confusion)\b",
        re.IGNORECASE)),
    ("employment", re.compile(
        r"\b(?:employment agreement|employee|employer|salary|compensation"
        r"|non-compete|severance|vesting)\b",
        re.IGNORECASE)),
]


class DefaultClassifier:
    """Keyword + mime-hint classifier. Phase-1 implementation.

    No external dependencies. Quick to evaluate. Replace with a local-LLM
    classifier (or the full ``doc-extractor-classifier`` skill) when more
    nuance is needed.
    """

    classifier_id = "default-classifier"
    classifier_version = "0.1.0"

    def classify(
        self,
        content: str,
        *,
        file_path: str | None = None,
        mime_type: str = "",
    ) -> Classification:
        # 1. Normative fingerprint runs first. Operative deontic content is
        #    the load-bearing classification for the legal/regulatory work
        #    this plugin is built for — if it fires, it wins.
        normative_score, normative_signals = score_normative(content)
        if normative_score >= NORMATIVE_THRESHOLD:
            best_type = "normative"
            best_score = normative_score
        else:
            best_type = "unknown"
            best_score = 0.0

        # 2. Other primary types — only override normative if their score is
        #    strictly higher (math/code/letter beat a noisy normative match).
        for type_name, pattern, boost in _TYPE_PATTERNS:
            matches = len(pattern.findall(content))
            if matches:
                score = min(1.0, boost + 0.05 * (matches - 1))
                if score > best_score:
                    best_score = score
                    best_type = type_name

        # 3. Mime-type hint for code files.
        if file_path:
            lower = file_path.lower()
            if lower.endswith((".py", ".js", ".ts", ".rs", ".go", ".java", ".c", ".cpp")):
                if best_type in ("unknown", "normative") and best_score < 0.85:
                    best_type = "code"
                    best_score = max(best_score, 0.85)

        # 4. Facets: every regex that hits adds a facet.
        facets = []
        for facet_name, pattern in _FACET_PATTERNS:
            if pattern.search(content):
                facets.append(facet_name)

        # Confidence:
        #   - clear primary_type → best_score from that match
        #   - unknown primary_type but facets matched → 0.7 (facet regex hits
        #     are themselves high-signal — don't penalise NDs for type-uncertainty
        #     when their facet IS clearly present)
        #   - unknown + no facets → 0.3 (low confidence — likely irrelevant)
        if best_type != "unknown":
            confidence = best_score
        elif facets:
            confidence = 0.7
        else:
            confidence = 0.3

        return Classification(
            primary_type=best_type,
            facets=facets,
            confidence=confidence,
            metadata={
                "normative_score": normative_score,
                "normative_signals": {k: v for k, v in normative_signals.items() if v},
                "classifier": self.classifier_id,
                "classifier_version": self.classifier_version,
                "mime_type": mime_type,
            },
        )


# ---------------------------------------------------------------------------
# ND dispatcher protocol
# ---------------------------------------------------------------------------


class NDDispatcher(Protocol):
    """The thin adapter each ND exposes to the router.

    NDs implement their full :class:`~nd_template.NDIngest` machinery
    internally. This dispatcher protocol is the SURFACE the router
    interacts with — declarative-match (``handles_types`` /
    ``handles_facets``) + an ``extract()`` that returns pair dicts ready
    for :meth:`WorkspaceMemory.remember`.

    Implementing this protocol does NOT require the full nd-template
    package — any class with the right attributes works.
    """

    nd_id: str
    """Stable identifier: ``"nd-math"`` / ``"nd-ai-act"`` / etc."""

    handles_types: list[str]
    """Primary types this ND claims. If a classification's primary_type is
    in this list, the ND is a candidate."""

    handles_facets: list[str]
    """Facets this ND claims. If any facet from a classification is in this
    list, the ND is also a candidate."""

    confidence_floor: float
    """Minimum classification confidence at which this ND will engage. Lower
    confidence classifications are skipped to avoid noise."""

    def can_handle(self, classification: Classification) -> bool:
        ...

    def extract(
        self,
        content: str,
        classification: Classification,
        *,
        source_document: str | None = None,
    ) -> list[dict[str, Any]]:
        ...


# ---------------------------------------------------------------------------
# Base ND class — most NDs can subclass this rather than implement the
# Protocol from scratch.
# ---------------------------------------------------------------------------


class BaseNDDispatcher:
    """Convenience base class for NDs that don't need custom dispatch logic.

    Subclasses set the four class attributes (``nd_id``, ``handles_types``,
    ``handles_facets``, ``confidence_floor``) and implement ``extract()``.
    ``can_handle()`` is provided.
    """

    nd_id: str = ""
    handles_types: list[str] = []
    handles_facets: list[str] = []
    confidence_floor: float = 0.7

    def can_handle(self, classification: Classification) -> bool:
        if classification.confidence < self.confidence_floor:
            return False
        if classification.primary_type in self.handles_types:
            return True
        if any(f in self.handles_facets for f in classification.facets):
            return True
        return False

    def extract(
        self,
        content: str,
        classification: Classification,
        *,
        source_document: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("subclasses must implement extract()")


# ---------------------------------------------------------------------------
# NDRouter
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """The router's report after fanning out a document."""

    classification: Classification
    nds_engaged: list[str]
    nds_skipped: list[str]
    pairs_by_nd: dict[str, list[dict[str, Any]]]
    contract_report: Any = None      # set when dispatch(enforce_contract=True)

    @property
    def total_pairs(self) -> int:
        return sum(len(v) for v in self.pairs_by_nd.values())

    @property
    def all_pairs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pairs in self.pairs_by_nd.values():
            out.extend(pairs)
        return out


class NDRouter:
    """Holds registered NDs; dispatches a classified document to all that claim it.

    Usage:

    .. code-block:: python

        router = NDRouter()
        router.register(MathND())
        router.register(ContractND())

        classification = classifier.classify(content)
        result = router.dispatch(content, classification, source_document=file_path)
        for pair in result.all_pairs:
            mem.remember(pair, channel="document")
    """

    def __init__(self) -> None:
        self._dispatchers: list[NDDispatcher] = []

    def register(self, dispatcher: NDDispatcher) -> None:
        """Add an ND to the routing pool. Re-registering replaces by nd_id."""
        existing_idx = next(
            (i for i, d in enumerate(self._dispatchers) if d.nd_id == dispatcher.nd_id),
            None,
        )
        if existing_idx is not None:
            self._dispatchers[existing_idx] = dispatcher
        else:
            self._dispatchers.append(dispatcher)

    def unregister(self, nd_id: str) -> bool:
        """Remove an ND from the routing pool. Returns True if it was registered."""
        for i, d in enumerate(self._dispatchers):
            if d.nd_id == nd_id:
                del self._dispatchers[i]
                return True
        return False

    def registered(self) -> list[str]:
        """Return the list of currently-registered ND ids."""
        return [d.nd_id for d in self._dispatchers]

    def dispatch(
        self,
        content: str,
        classification: Classification,
        *,
        source_document: str | None = None,
        enforce_contract: bool = False,
        risk_class: str = "B",
    ) -> DispatchResult:
        """Fan the content out to every ND whose ``can_handle()`` returns True.

        When ``enforce_contract`` is True, every emitted pair is run through the
        norm-theory contract (:mod:`workspaces.norm_contract`). A VIOLATION raises
        ``ContractViolation`` — the emission is refused at the dispatch seam rather
        than allowed to flow downstream; escalations are surfaced on the result.
        Default False keeps existing callers unchanged; switch on for class-C
        (Verwaltungsakt) folders."""
        engaged: list[str] = []
        skipped: list[str] = []
        pairs_by_nd: dict[str, list[dict[str, Any]]] = {}
        for d in self._dispatchers:
            try:
                claims = d.can_handle(classification)
            except Exception:
                claims = False
            if not claims:
                skipped.append(d.nd_id)
                continue
            try:
                pairs = d.extract(content, classification,
                                  source_document=source_document) or []
            except Exception:
                # An ND that throws is logged as skipped — never crash the
                # ingest. Audit the failure separately.
                skipped.append(d.nd_id)
                continue
            engaged.append(d.nd_id)
            pairs_by_nd[d.nd_id] = list(pairs)
        result = DispatchResult(
            classification=classification,
            nds_engaged=engaged,
            nds_skipped=skipped,
            pairs_by_nd=pairs_by_nd,
        )
        if enforce_contract:
            from .norm_contract import gate  # lazy: stdlib-only, no cycle
            all_pairs = [p for ps in pairs_by_nd.values() for p in ps]
            # Raises ContractViolation on any VIOLATION; returns a report whose
            # escalations the caller routes to Oversight.
            result.contract_report = gate(all_pairs, risk_class=risk_class)
        return result


# ---------------------------------------------------------------------------
# RoutingExtractor — drop-in replacement for the InboxWatcher's extractor
# ---------------------------------------------------------------------------


class RoutingExtractor:
    """Wraps a base extractor; classifies; dispatches to NDs; folds all pairs.

    Drop-in replacement for :class:`DefaultExtractor` in :class:`InboxWatcher`.
    The base extractor (typically ``DefaultExtractor``) captures the file
    metadata pair; the classifier reads the same content; the router fans
    out to matching NDs; all pair dicts are concatenated into the returned
    ``ExtractedFile.pairs``.

    The result is what the watcher writes to ``WorkspaceMemory`` — typed pairs
    from NDs alongside the base file-metadata pair.
    """

    def __init__(
        self,
        base_extractor: Any,
        classifier: Classifier | None = None,
        router: NDRouter | None = None,
    ):
        # Late import to avoid circular dependency at import time.
        from .inbox_watcher import DefaultExtractor

        self.base = base_extractor or DefaultExtractor()
        self.classifier = classifier or DefaultClassifier()
        self.router = router or NDRouter()

    def extract(self, file_path: str, folder_context: str):
        # Late import again (ExtractedFile is in inbox_watcher).
        from .inbox_watcher import ExtractedFile

        base_result = self.base.extract(file_path, folder_context)

        # Classify against the previewed content. For binary files the preview
        # is the "(binary file, ... bytes, mime=...)" placeholder — that's
        # OK; the classifier will fall back to unknown.
        classification = self.classifier.classify(
            base_result.content_preview,
            file_path=file_path,
            mime_type=base_result.mime_type,
        )

        result = self.router.dispatch(
            base_result.content_preview,
            classification,
            source_document=file_path,
        )

        # Merge base pairs + every ND-extracted pair.
        merged_pairs = list(base_result.pairs) + result.all_pairs

        # ---- Lock-at-ingest pass ---------------------------------------
        # Every pair gets lock-classified before landing in memory.
        # The result has a ``lock`` audit block + a ``clean`` block
        # (pre-scrubbed safe view) so safe-context queries can read
        # pre-computed data instead of re-scanning at query time.
        try:
            from .lock_classify import lock_classify_pair
            merged_pairs = [
                lock_classify_pair(p, folder_context) for p in merged_pairs
            ]
        except Exception:
            # If lock_classify is unavailable, fall through unenriched.
            # The query path will live-scrub as a defense layer.
            pass

        return ExtractedFile(
            file_path=base_result.file_path,
            file_size=base_result.file_size,
            file_hash=base_result.file_hash,
            mime_type=base_result.mime_type,
            content_preview=base_result.content_preview,
            pairs=merged_pairs,
        )


def make_full_extractor():
    """Build the production extractor stack — FormatAwareExtractor (PDF/DOCX/
    Pages/plain-text reader) wrapped in a RoutingExtractor with the default
    domain NDs (gdpr / ai-act / music-rights / contracts) and the legal
    mental-model extractors (definitions / article-references / doc-summary).

    Shared by the CLI ``workspaces ingest`` path and the MCP ``ingest_path`` /
    ``scan_folder`` tools so both surfaces extract real document text and
    fan out to NDs — rather than the metadata-only ``DefaultExtractor``.

    Lazy imports throughout so importing this module never requires pypdf,
    python-docx, or the mcp SDK. If a format reader's dependency is missing,
    FormatAwareExtractor degrades to a metadata stub for that file.
    """
    from .format_extractors import FormatAwareExtractor
    from .domain_nds import register_default_domain_nds
    from .legal_extractors import register_legal_mental_model_extractors
    from .deontic import register_deontic_nd
    from .crossref_extractor import register_crossref_nd
    from .decisions.extractor import register_decision_nd
    from .instrument_obligation_extractor import register_required_artifact_nd
    from .use_case_nd import register_use_case_nd

    base = FormatAwareExtractor()
    router = NDRouter()
    register_default_domain_nds(router)
    register_legal_mental_model_extractors(router)
    # NotebookLM-grade legal analysis layer: deontic formulae, cross-document
    # references (AI Act → GDPR), reader-facing decisions, and the artifacts
    # (contracts/policies/registers/assessments) the instrument requires.
    register_deontic_nd(router)
    register_crossref_nd(router)
    register_decision_nd(router)
    register_required_artifact_nd(router)
    # Use-case / POC descriptions → canonical facets (the join target for
    # subsuming a system under the ingested duties). Self-gates on use-case cues.
    register_use_case_nd(router)
    return RoutingExtractor(base_extractor=base, router=router)

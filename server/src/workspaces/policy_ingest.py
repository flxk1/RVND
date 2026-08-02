# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Policy ingest → digital twin.

A user pastes their AI policy; Rvnd builds the governance graph + paths + the
host to-do list around it. The deterministic core uses deontic cues so the same
policy always yields the same twin (gated by a golden fixture, LLM off); the
local-LLM cascade is an optional enrichment, never the source of truth.

Pipeline (the loomground-skill litmus): extract → classify express/policy/host →
draft a v0.5 .lg patch from the express set → validate fail-closed
(loomground_lang) → return the twin. It DECLARES governance; it does not certify
compliance (no score) and nothing is written to the chain — the caller confirms,
and that confirm is the auto-instrumented write.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import genre_router as _gr
from . import loomground_lang as L

_ACTOR = "ai_system"

#: Optional process-wide LLM proposer, consulted only when a caller opts in with
#: ``use_llm=True`` (e.g. the local model). Off by default → deterministic, reproducible.
_DEFAULT_PROPOSER = None


def set_default_proposer(fn) -> None:
    """Register a process-wide LLM proposer for ``use_llm=True`` calls. The proposer is
    ``fn(policy_text, context) -> list[dict]``; every proposal it returns is fenced the same
    way as the deterministic output (grounded + well-formed + Loomground-validated)."""
    global _DEFAULT_PROPOSER
    _DEFAULT_PROPOSER = fn

# ---------------------------------------------------------------------------
# Deontic cue vocabulary (shared so the redress CUE, the overturn flag, and the
# residual backstop can never drift apart. A redress/obligation cue only counts
# inside a sentence that actually
# GRANTS or COMMANDS; a descriptive past-tense / adjectival use ("contested
# environments", "was approved by the board", "reversal handling") is not an
# obligation and must not trigger extraction or the backstop.
# ---------------------------------------------------------------------------

#: the sentence is deontic — grants a right or issues a command (gates redress).
_REDRESS_CONTEXT_RE = re.compile(
    r"\b(may|can|shall|must|should|will|right to|entitled to|subject to|"
    r"have the right)\b")
#: reversal / overturn family — one definition, reused by the redress cue and the
#: overturn=True flag, so "annulled"/"overturned"/"reversed" extract consistently.
_REVERSAL = (r"overturn(?:ed|s|ing)?|overrul(?:e|ed|es|ing)?|"
             r"revers(?:e|es|ed|ing|al)|set aside|annul(?:led|s|ling)?")
_OVERTURN_RE = re.compile(r"\b(?:" + _REVERSAL + r")\b")
#: cue that the sentence is about redress (appeal / contest / reversal / human review)
_REDRESS_CUE_RE = re.compile(
    r"\b(?:appeals?|appealed|appealing|contest(?:ed|ing|s)?|"
    r"human (?:review|intervention)|right to (?:a )?(?:human )?review|"
    + _REVERSAL + r")\b")
#: residual reservation/prohibition/redress cue — an express primitive the
#: extractors missed (e.g. a compound "logged and approved"). Surfaced for review
#: only in a deontic-modal sentence (see _MODAL_RE), never on a bare past-tense.
_RESIDUAL_CUE_RE = re.compile(
    r"\b(?:approv\w+|sign[- ]?off|authori[sz]\w+|prohibit\w*|forbidden|"
    r"not permitted|four[- ]eyes|dual[- ]control|"
    r"appeals?|appealed|appealing|contest(?:ed|ing|s)?|"
    r"human (?:review|intervention)|" + _REVERSAL + r")\b")
#: obligation modal — required for any sentence to reach the unmapped backstop
#: (no modal ⇒ not an obligation; fixes the "was approved" past-tense false-positive).
_MODAL_RE = re.compile(r"\b(?:must|shall|should|requires?|may|can)\b")
#: negation / exemption cue — a sentence that negates or exempts from an approval
#: rule ("do not require approval by X", "exempt from the four-eyes principle",
#: "no longer subject to dual control") states the absence of a reservation and
#: must never draft one. Guards only the reserve cues whose shape does not already
#: exclude negation; the sentence falls through to the residual backstop instead.
_NEG_EXEMPT_RE = re.compile(
    r"\b(?:not|never|no longer|exempt(?:ed|ion|ions)?|"
    r"waiv(?:e|ed|es|er|ers|ing)|except(?:ed|ion|ions)?)\b|n't\b")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")
    return s[:48] or "x"


def _singular(tok: str) -> str:
    """Naive singularize so a plural inflection maps to the same gate kind as its
    singular ("automated_decisions" -> "automated_decision"). Conservative: spares
    -ss/-us/-is/-as/-os so "process"/"status"/"analysis"/"bias"/"chaos" survive."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is", "as", "os")):
        return tok[:-1]
    return tok


def _action(clause: str) -> str:
    """Reduce a clause to its governed-action phrase (a kind id). Singular/plural
    inflections collapse to one canonical kind so a reservation and its redress
    attach to the same gate."""
    c = clause.strip().lower()
    c = re.sub(r"^(the|all|any|a|an|each|our)\s+", "", c)
    c = re.sub(r"\b(systems?|processes?|activit(?:y|ies))\b", "", c)
    return "_".join(_singular(t) for t in _slug(c).split("_") if t) or "x"


#: a role ends at the role noun — a trailing statute / qualifier clause is not part of it.
_ROLE_TAIL_RE = re.compile(
    r"\s+(?:under|pursuant to|per\b|as (?:required|set out|provided|laid)|in accordance with|"
    r"according to|in line with|in compliance with|as per)\b.*$")


def _role(raw: str) -> str:
    """Slug a captured role, stopping at the role noun: drop a trailing statute / qualifier
    clause ("… under GDPR Article 22"), a coordinated tail ("… and AI Act Article 14"), and a
    leading article — so the role is ``data_protection_officer``, never
    ``data_protection_officer_under_gdpr_article_22``."""
    r = _ROLE_TAIL_RE.sub("", raw.strip())
    r = re.split(r"[,;]| and | or ", r)[0]
    r = re.sub(r"^(?:the|a|an)\s+", "", r.strip())
    return _slug(r)


#: number words as policies write approval counts ("two of the following").
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
              "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _count(tok: str) -> int | None:
    """An approval count from a digit or number-word token; None if neither."""
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok)


#: temporal nouns, calendar names, and deadline idioms — words that mark a
#: schedule phrase, never a role noun phrase ("two weeks after deployment"
#: states a schedule, not two approvers; "approval by Friday" states a deadline,
#: not an approver).
_TIME_WORDS = frozenset((
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "week", "weeks", "month", "months", "quarter", "quarters", "year", "years",
    "after", "before", "prior", "following", "later", "earlier",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "today", "tomorrow", "yesterday", "noon", "midnight", "eod", "cob",
    "end", "close", "latest", "soonest", "deadline", "am", "pm", "due", "date"))

#: bare clock time — "5pm", "6 p.m.", "17:00". A letters-only word scan cannot
#: see digit-led tokens, so clock times get their own abstention pattern: an
#: approver phrase containing one states when approval is due, not who approves.
_CLOCK_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)(?:\b|\Z)"
                       r"|\b\d{1,2}:\d{2}\b")


def _role_like(part: str) -> str | None:
    """Slug a captured approver phrase only when it reads as a role noun phrase:
    one to four plain-alphabetic words, none a participle ("retained for two
    years" is a retention clause, not an approver) and none a temporal noun or
    sequencer ("weeks after deployment" is a schedule, not an approver). None
    means the capture is not a role and the caller falls back or abstains."""
    p = _ROLE_TAIL_RE.sub("", part.strip())
    p = re.sub(r"^(?:the|a|an)\s+", "", p).strip()
    words = p.split()
    if not 1 <= len(words) <= 4:
        return None
    for w in words:
        if not re.fullmatch(r"[a-z][a-z-]*", w):
            return None
        if w in _TIME_WORDS:
            return None
        if len(w) > 4 and (w.endswith("ed") or w.endswith("ing")):
            return None
    return _slug(p)


def _reserve_target(raw: str) -> tuple[str, list[str]] | None:
    """Resolve a captured approver phrase to a reserve target plus its member roles.

    The grammar (loomground_lang) accepts three targets: a single role, exactly two
    ``and``-joined roles, and an ``N of {r1, r2, ...}`` quorum (canonical spelling:
    one space after ``of`` and after each comma, none at the braces). Quorums are
    drafted conservatively — only when the text states a count over a readable role
    list, or coordinates two or more distinct role noun phrases; three or more
    ``and``-joined roles become an all-of quorum (the grammar caps ``and`` at two
    hands). Anything else resolves to the single leading role, as a bare "must be
    approved by X" always has — unless the capture contains a schedule word
    ("approval by end of day", "by Friday at the latest" state when approval is
    due, not who approves). None means the target is a counted quorum whose role
    list is unreadable or unsatisfiable, or a schedule phrase — the caller
    abstains and the sentence falls through to the residual backstop instead of
    drafting a wrong target."""
    t = _ROLE_TAIL_RE.sub("", raw.strip())
    m = re.match(r"^(?:any\s+|at\s+least\s+)?(\d+|one|two|three|four|five|six|"
                 r"seven|eight|nine|ten)\s+of\b[\s:]*(?:the\s+following)?[\s:]*"
                 r"(?:roles|approvers|reviewers|officers)?[\s:]*(.+)$", t)
    if m:
        n = _count(m.group(1))
        parts = [p for p in re.split(r",|\band\b|\bor\b", m.group(2)) if p.strip()]
        roles = [_role_like(p) for p in parts]
        if (n is None or None in roles or len(roles) < 2
                or len(set(roles)) != len(roles) or not 1 <= n <= len(roles)):
            return None
        return f"{n} of {{{', '.join(roles)}}}", roles
    if re.search(r"\band\b", t) and not re.search(r"\bor\b", t):
        parts = [p for p in re.split(r",|\band\b", t) if p.strip()]
        roles = [_role_like(p) for p in parts]
        if None not in roles and len(set(roles)) == len(roles) and len(roles) >= 2:
            if len(roles) == 2:
                return f"{roles[0]} and {roles[1]}", roles
            return f"{len(roles)} of {{{', '.join(roles)}}}", roles
    # counted approvers without "of" — "two independent auditors" states a
    # two-hand rule over one role class, the passive mirror of "Two officers
    # must approve". Counts above two over one class are not expressible in the
    # grammar and abstain; a count of one is a plain single role.
    m = re.match(r"^(?:at\s+least\s+)?(\d+|one|two|three|four|five|six|seven|"
                 r"eight|nine|ten)\s+(?!of\b)(?:different\s+|distinct\s+|"
                 r"separate\s+|independent\s+)?(.+)$", t)
    if m:
        n = _count(m.group(1))
        role = _role_like(m.group(2))
        if n is not None and role is not None:
            role = "_".join(_singular(w) for w in role.split("_") if w)
            if n == 1:
                return role, [role]
            if n == 2:
                return f"{role} and {role}", [role]
        return None
    # single-role fallback — abstain on a schedule phrase: "by end of day" /
    # "by Friday at the latest" / "by 5pm" bind a deadline to the approval,
    # not an agent, and must never draft a phantom approver role.
    if any(w in _TIME_WORDS for w in re.findall(r"[a-z]+(?:-[a-z]+)*", t.lower())):
        return None
    if _CLOCK_RE.search(t.lower()):
        return None
    role = _role(raw)
    return role, [role]


#: temporal window bound in-sentence to a reservation — "within 30 days",
#: "no later than 72 hours", "after a 14-day cooling-off period". Captures a
#: count and a unit the grammar can express (minutes/hours/days; weeks convert
#: to days). Months and years are not expressible as a reservation window and
#: do not match — such a clause stays a host hand-off.
_WINDOW_RE = re.compile(
    r"\b(?:within|no later than|not later than|after)\s+(?:a |an |the )?"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)[\s-]+"
    r"(minute|hour|day|week)s?\b")

#: the action proceeds when the window elapses — only an explicit statement
#: reads this way ("deemed approved", "automatically granted", "proceeds by
#: default"); absent one, the on-elapse disposition defaults to halt
#: (fail-closed: silence never waves an action through).
_ELAPSE_PROCEED_RE = re.compile(
    r"\b(?:deemed|automatically|considered)\s+(?:approved|granted)\b"
    r"|\bproceeds?\s+(?:by default|automatically)\b"
    r"|\bapproval is presumed\b")


def _window(low: str) -> tuple[str, str] | None:
    """The sentence's temporal window as ``(duration, on_elapse)``, or None.

    ``duration`` is in the grammar's units (``15m``, ``72h``, ``30d``); weeks
    convert to days. ``on_elapse`` is fail-closed: ``halt`` unless the text
    states the action proceeds on expiry (_ELAPSE_PROCEED_RE)."""
    m = _WINDOW_RE.search(low)
    if not m:
        return None
    n = _count(m.group(1))
    if n is None:
        return None
    unit = m.group(2)
    if unit == "week":
        n, unit = n * 7, "day"
    return f"{n}{unit[0]}", "proceed" if _ELAPSE_PROCEED_RE.search(low) else "halt"


def _strip_window(raw: str) -> str:
    """Cut a temporal window and everything after it off a captured phrase —
    the window binds to the reservation as its duration clause, never to the
    role or the action kind ("approved by the officer within 30 days" reserves
    for the officer; the 30 days become the duration)."""
    m = _WINDOW_RE.search(raw)
    return raw[:m.start()] if m else raw


def _reservation(kind: str, by: str, low: str, src: str) -> dict[str, Any]:
    """A reservation record, carrying the sentence's temporal window when one
    is stated. ``duration`` and ``on_elapse`` are set together or not at all —
    the netlist emits the clause only when both are present."""
    rec: dict[str, Any] = {"kind": kind, "by": by, "_src": src}
    w = _window(low)
    if w is not None:
        rec["duration"], rec["on_elapse"] = w
    return rec


def _reserve_express(rec: dict[str, Any]) -> str:
    """The express string for a reservation, in the concrete surface grammar:
    the duration clause spells ``duration <d> : <on-elapse>`` with the colon as
    its own token (the compact ``<d>:<on-elapse>`` does not parse)."""
    s = f"reserve {rec['kind']} by {rec['by']}"
    if rec.get("duration"):
        s += f" duration {rec['duration']} : {rec['on_elapse']}"
    return s


def _subject(clause: str) -> str:
    """The governed subject = the leading noun phrase, before any inner modal or coordination
    ("offer letters may be drafted by agents but" → "offer letters"). Simple subjects (no
    inner modal) are unchanged, so this only disambiguates compound sentences."""
    head = re.split(r"\b(?:may|can|will|should|shall|must)\b|\bbut\b|;", clause, maxsplit=1)[0]
    return _action(head if head.strip() else clause)


def _same_sentence(quote: str, src: str) -> bool:
    """True when an LLM proposal's verbatim quote and a deterministic primitive's source
    sentence are the same provision (one contains the other, whitespace/case-insensitive).
    This is the overlap signal: a grounded proposal SUPERSEDES the same-sentence deterministic
    extraction rather than sitting beside it."""
    q = re.sub(r"\s+", " ", quote or "").strip().lower()
    sv = re.sub(r"\s+", " ", src or "").strip().lower()
    return bool(q and sv and (q in sv or sv in q))


def _supersede_srcs(quote_raw: str, rules: list[dict]) -> set[str]:
    """The source sentence(s) a grounded proposal may EVICT — fail-closed. Eviction deletes
    genuine deterministic rules, so it happens only when the quote identifies exactly one
    provision: if the quote (e.g. a short common phrase like "approved by") matches the source
    sentences of SEVERAL distinct provisions, it evicts nothing — the proposal may still be
    admitted beside them, but it never wipes rules it does not unambiguously correct."""
    srcs = {re.sub(r"\s+", " ", r.get("_src", "")).strip().lower()
            for r in rules
            if r.get("origin") != "llm" and _same_sentence(quote_raw, r.get("_src", ""))}
    return srcs if len(srcs) == 1 else set()


def _risk(low: str) -> str:
    if re.search(r"\b(critical|safety[- ]critical)\b", low):
        return "critical"
    if re.search(r"\b(high[- ]risk|sensitive|biometric|high stakes)\b", low):
        return "high"
    if re.search(r"\bmedium[- ]risk\b", low):
        return "medium"
    return "low"


def _parse_proposals(reply: str) -> list[dict[str, Any]]:
    """Pull the first JSON array of proposals out of a model reply. A reply that does not
    parse is an abstention (──> []), never a fabricated rule."""
    m = re.search(r"\[.*\]", reply or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (ValueError, json.JSONDecodeError):
        return []
    return [x for x in arr if isinstance(x, dict)]


def local_llm_proposer(policy_text: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Built-in OPT-IN proposer: routes to the configured LOCAL model via
    ``local_llm.complete`` (OpenAI-compatible, env ``WORKSPACE_LOCAL_LLM_URL``; no cloud).
    Returns express-primitive proposals for the deontic fence to grade (grounded +
    well-formed + Loomground-validated). No endpoint / bad reply → [] (deterministic)."""
    from . import local_llm
    found = ", ".join(str(r.get("kind")) for r in (ctx.get("reservations") or [])) or "(none)"
    prompt = (
        "From the AI policy below, extract governance rules it STATES that are NOT already "
        "found. Return ONLY a JSON array; each item: "
        '{"declaration":"reserve" or "prohibit","kind":"<short_snake_slug>",'
        '"by":"<role; for reserve only>","quote":"<span copied VERBATIM from the policy>"}. '
        "Copy the quote word-for-word from the policy; invent nothing.\n\n"
        f"Already found (do not repeat): {found}\n\nPolicy:\n{policy_text}\n\nJSON:"
    )
    try:
        res = local_llm.complete(prompt, max_tokens=400)
    except Exception:
        return []
    return _parse_proposals(res.get("response", "")) if res.get("ok") else []


def ingest(policy_text: str, *, use_llm: bool = False, llm_proposer=None) -> dict[str, Any]:
    """Return the digital twin: validated patch, projection, paths, classification,
    host hand-offs, and applied=False (human confirmation required).

    LLM is opt-in: with ``use_llm=False`` (default) this is purely deterministic and
    reproducible. With ``use_llm=True`` and a proposer wired (``llm_proposer=`` or
    :func:`set_default_proposer`), the proposer may recover express primitives the cues
    missed — but every proposal passes TWO fail-closed gates before it is admitted:
    GROUNDED (its ``quote`` occurs verbatim in the policy — no hallucinated rules) and
    WELL-FORMED (required fields); the whole patch is then Loomground-validated below.
    The model proposes; the gate disposes."""
    if not isinstance(policy_text, str) or not policy_text.strip():
        return {"ok": False, "errors": ["policy_text is empty"]}

    # GENRE GUARD — a court judgment INTERPRETS norms; it does not enact them. It must not reach
    # the express compiler: a holding lowered to a `.lg` gate would be a phantom enforcement
    # node manufactured from a court's reasoning. Quarantine it and hand it to the interpreter
    # side, where a holding becomes an authority-weighted *reading* of the provisions it
    # construes (escalation-gated), never an obligation. No patch is drafted.
    genre = _gr.detect_genre(policy_text)
    if genre == "case-law":
        return {
            "ok": True,
            "applied": False,
            "quarantined": True,
            "genre": genre,
            "routed_to": "interpreter",
            "note": "This is a court decision, not a policy instrument. A judgment interprets "
                    "norms; it does not enact them, so no governance patch is drafted. Route it "
                    "to the interpreter (reasoning_walker): its holding becomes an "
                    "authority-weighted reading of the provisions it construes — escalation-"
                    "gated, never an obligation.",
            "patch": None,
            "classification": {"express": [], "policy": [], "host": [], "unmapped": []},
        }

    sents = re.split(r"(?<=[.;])\s+|\n+", policy_text)
    gates: dict[str, dict[str, Any]] = {}
    humans: set[str] = set()
    reservations: list[dict[str, Any]] = []
    prohibitions: list[dict[str, Any]] = []
    obligations: list[dict[str, Any]] = []
    redress: list[dict[str, Any]] = []
    express: list[str] = []
    policy: list[str] = []
    host: list[str] = []
    unmapped: list[str] = []

    def gate(kind: str, low: str) -> None:
        gates.setdefault(kind, {})["risk"] = _risk(low)

    for raw in sents:
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        # express_matched tracks only express-primitive extraction (reserve /
        # prohibit / obligation / redress). host/policy classification is
        # ADDITIVE and must not suppress the unmapped backstop (P1): a compound
        # sentence like "decisions must be logged and approved by the board" is
        # a host hand-off ("logged") and carries an approval reservation that the
        # reserve regex misses — that residual must still be surfaced for review.
        express_matched = False

        # reserve — "<action> must be reviewed/approved by <target>" / "requires <role>
        # approval" / "requires approval by/from <target>". The approver phrase may be
        # a quorum target (_reserve_target): "X and Y" reserves two hands, a counted
        # "N of <roles>" reserves a quorum. Extraction abstains when a counted quorum's
        # role list is unreadable or unsatisfiable, or when the complement is a
        # deadline rather than an agent ("requires approval by end of day") — the
        # sentence then reaches the residual backstop rather than drafting a wrong
        # target. A temporal window stated in the same sentence ("within 30 days",
        # "no later than 72 hours") attaches to the reservation as its duration
        # clause (_reservation); on elapse the action halts unless the text states
        # it proceeds ("deemed approved").
        m = re.search(r"(.+?)\s+(?:must|shall)\s+be\s+(?:reviewed|approved|signed[- ]off|authori[sz]ed|co[- ]?determined|consented|agreed|countersigned|ratified)\s+by\s+(?:a |an |the )?([a-z0-9].*?)(?:\.|$)", low)
        if not m:
            m = re.search(r"(.+?)\s+requires?\s+(?:a |an |the )?([a-z][\w -]*?)\s+(?:approval|sign[- ]off|review)\b", low)
        if not m and not _NEG_EXEMPT_RE.search(low):
            # "requires approval by/from <target>" reads a reservation only in the
            # affirmative — "do not require approval by X" states an exemption
            # (_NEG_EXEMPT_RE) and must fall through to the residual backstop.
            m = re.search(r"(.+?)\s+requires?\s+(?:the\s+)?(?:approval|sign[- ]off|review|authori[sz]ation)\s+(?:by|from)\s+(?:a |an |the )?([a-z0-9].*?)(?:\.|$)", low)
        if m:
            target = _reserve_target(_strip_window(m.group(2)))
            if target is not None:
                kind = _subject(m.group(1)); by, members = target
                gate(kind, low); humans.update(members)
                rec = _reservation(kind, by, low, s)
                reservations.append(rec)
                express.append(_reserve_express(rec)); express_matched = True

        # reserve via COUNTED APPROVERS — "Two officers must approve <X>" states a
        # two-hand rule over one role class. The grammar's only two-hand form for a
        # single class is the conjunction "<role> and <role>" ("2 of {role}" is
        # unsatisfiable at validate); counts above two over one class are not
        # expressible and fall through to the residual backstop. The counted
        # capture must read as a role noun phrase (_role_like) — "two weeks after
        # deployment" counts time, not approvers, and drafts nothing.
        if not express_matched:
            m = re.search(r"^(?:at least\s+)?(\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?!of\b)(?:different\s+|distinct\s+|separate\s+)?([a-z][a-z -]*?)\s+must\s+(?:approve|review|authori[sz]e|countersign|sign\s+off\s+on)\s+(.+?)(?:\.|$)", low)
            if m and _count(m.group(1)) == 2:
                role = _role_like(m.group(2))
                if role is not None:
                    role = "_".join(_singular(t) for t in role.split("_") if t)
                    kind = _action(_strip_window(m.group(3))); by = f"{role} and {role}"
                    gate(kind, low); humans.add(role)
                    rec = _reservation(kind, by, low, s)
                    reservations.append(rec)
                    express.append(_reserve_express(rec)); express_matched = True

        # reserve via NAMED TWO-HAND RULE — "dual control" / "the four-eyes principle"
        # state a count of two without naming a role; drafted as two hands of the
        # generic reviewer class (the same fallback role as the negation-conditional).
        # A negated or exempting mention ("exempt from the four-eyes principle",
        # "no longer subject to dual control") names the rule to lift it and must
        # not draft a reservation.
        if not express_matched and not _NEG_EXEMPT_RE.search(low):
            m = re.search(r"(.+?)\s+(?:is|are|must|shall|requires?)\b[^.]*?\b(?:four[- ]eyes|dual[- ]control)\b", low)
            if m:
                kind = _subject(m.group(1)); by = "human-reviewer and human-reviewer"
                gate(kind, low); humans.add("human-reviewer")
                rec = _reservation(kind, by, low, s)
                reservations.append(rec)
                express.append(_reserve_express(rec)); express_matched = True

        # reserve via NEGATION-CONDITIONAL — "<X> shall not <verb> without/unless <Y>"
        # or "No <X> without <Y>" means Y is REQUIRED for X: a reservation, not a
        # blanket prohibition. Must run before prohibit so the conditional is not
        # mis-extracted as a ban (fail-open: a ban under-governs vs a person-required
        # reserve only when the action is actually allowed-with-oversight — here it is).
        if not express_matched:
            subj = cond = None
            m = re.search(r"(.+?)\s+(?:shall|must|may|can|will)\s*not\s+.+?\s+(?:without|unless|except\s+(?:with|when|where|upon))\s+(.+?)(?:\.|$)", low)
            if m:
                subj, cond = m.group(1), m.group(2)
            else:
                m = re.search(r"\bno\s+(.+?)\s+without\s+(.+?)(?:\.|$)", low)
                if m:
                    subj, cond = m.group(1), m.group(2)
            if cond is not None:
                kind = _action(subj)
                rm = re.search(r"\b(?:by|from)\s+(?:a |an |the )?([a-z][\w -]*)", cond)
                if not rm:
                    # "<role> approval/sign-off/review/consent" — role precedes the act noun
                    rm = re.search(r"(?:a |an |the )?([a-z][\w -]*?)\s+(?:approval|sign[- ]off|review|consent|authori[sz])", cond)
                target = _reserve_target(_strip_window(rm.group(1))) if rm else None
                by, members = target if target else ("human-reviewer", ["human-reviewer"])
                gate(kind, low); humans.update(members)
                rec = _reservation(kind, by, low, s)
                reservations.append(rec)
                express.append(_reserve_express(rec)); express_matched = True

        # prohibit — capture the action after the modal ("shall not <verb…>"),
        # falling back to the subject for "<X> is prohibited"
        if not express_matched:
            m = re.search(r"\b(?:shall not|must not|may not|cannot|is not permitted to|are not permitted to)\s+(.+?)(?:\.|$)", low)
            kind = _action(m.group(1)) if m else None
            if not kind:
                m2 = re.search(r"(.+?)\s+(?:is|are)\s+(?:prohibited|forbidden|not permitted)\b", low)
                kind = _action(m2.group(1)) if m2 else None
            if kind:
                gate(kind, low); prohibitions.append({"kind": kind, "_src": s})
                express.append(f"prohibit {kind}"); express_matched = True

        # obligation — AI-interaction / synthetic-content disclosure only when the
        # text is about disclosing AI involvement (a generic "notify" is not an
        # AI-disclosure declaration — it falls through to host/unmapped)
        if (not express_matched and re.search(r"(disclos|inform|notify|make clear)", low)
                and re.search(r"\b(ai|a\.i\.|artificial intelligence|automated|chatbot|interact|synthetic|generated|machine[- ]generated)\b", low)):
            gate("ai_interaction", low)
            obligations.append({"obligation": "ai-interaction-disclosure", "on": "ai_interaction"})
            express.append("obligation ai-interaction-disclosure on ai_interaction"); express_matched = True

        # redress — appeal / contest / human intervention / reversal. Cues are
        # STEMMED (P2) so inflected forms ("appealed", "annulled") are caught, and
        # GATED on a deontic context (_REDRESS_CONTEXT_RE) so a descriptive use
        # ("contested environments", "reversal handling") does not extract a
        # phantom redress. overturn=True only when the text grants reversal.
        if (not express_matched and _REDRESS_CUE_RE.search(low)
                and _REDRESS_CONTEXT_RE.search(low)):
            am = re.search(r"((?:automated|algorithmic) decision(?:[- ]making)?|[a-z]+ decision)", low)
            kind = _slug(am.group(1)) if am else _action(re.sub(r"\b(may|can|shall|must|have the right to).*$", "", low) or low)
            ov = bool(_OVERTURN_RE.search(low))
            gate(kind, low)
            redress.append({"kind": kind, "by": "appeals", "overturn": ov, "within": None})
            humans.add("appeals")
            express.append(f"redress {kind} by appeals"); express_matched = True

        # host hand-offs — compute / aggregate / measure-time / persist / communicate.
        # A temporal window (_WINDOW_RE) is a schedule the host measures: with no
        # reservation in the sentence to bind it as a duration clause it lands
        # here, never in express. ADDITIVE: classify, but do not gate the backstop
        # below.
        host_matched = bool(re.search(r"\b(within\s+\d+\s+(?:hours?|days?)|logg?(?:ed|ing)?|monitor|retain|delete after|watermark|rate[- ]limit|aggregate|quarterly|annually|encrypt|report to)\b", low)
                            or _WINDOW_RE.search(low))
        if host_matched:
            host.append(s)

        # policy values — thresholds / autonomy choices. ADDITIVE (see above).
        policy_matched = bool(re.search(r"\b(threshold|percent|%|autonomy level|risk appetite|\btier\b)\b", low))
        if policy_matched:
            policy.append(s)

        # unmapped backstop — surface for human review when NO express primitive
        # was extracted, the sentence is a deontic OBLIGATION (a modal is present —
        # a bare past-tense like "was approved" is not an obligation), and either:
        #   (P1) a reservation/prohibition/redress CUE is present that slipped past
        #        extraction — even if host/policy also matched (the co-located
        #        obligation must not be silently swallowed by a host keyword); or
        #   (P2) nothing else mapped the sentence (incl. a bare-"may" obligation).
        if (not express_matched and _MODAL_RE.search(low) and (
                _RESIDUAL_CUE_RE.search(low)
                or not (host_matched or policy_matched))):
            unmapped.append(s)

    # ---- optional, opt-in LLM enrichment (fenced) ----
    # opt-in proposer resolution: an explicit arg wins, then a registered default, then the
    # built-in local-model route. use_llm=True therefore means "use the local LLM by choice".
    proposer = (llm_proposer if llm_proposer is not None
                else _DEFAULT_PROPOSER if _DEFAULT_PROPOSER is not None
                else local_llm_proposer)
    # CAPABILITY GATE: the AMBIENT local-model route runs only if a capable model is registered;
    # otherwise degrade to deterministic and REPORT the loss (never silent). An explicitly-injected
    # proposer (`llm_proposer=` or `set_default_proposer`) is the caller's deliberate choice and is
    # not second-guessed. Doctrine: a task runs on the local model only if it's capable.
    capability = None
    if use_llm and proposer is local_llm_proposer:
        from . import model_capability as _mc
        cap = _mc.for_task("extraction")
        capability = cap.as_dict()
        if not cap.capable:
            proposer = None                       # no capable local model → deterministic
    llm_ran = False
    if use_llm and proposer is not None:
        llm_ran = True
        low_text = policy_text.lower()
        try:
            proposals = proposer(policy_text, {"reservations": list(reservations),
                                               "prohibitions": list(prohibitions),
                                               "unmapped": list(unmapped)}) or []
        except Exception:
            proposals = []
        for prop in proposals:
            if not isinstance(prop, dict):
                continue
            # GROUNDING fence — the proposal must cite a span that is verbatim in the policy.
            quote = str(prop.get("quote", "")).strip().lower()
            if len(quote) < 8 or quote not in low_text:
                continue
            quote_raw = str(prop.get("quote", ""))
            decl = prop.get("declaration")
            if decl == "reserve" and prop.get("kind") and prop.get("by"):
                kind, role = _action(str(prop["kind"])), _role(str(prop["by"]))
                # Supersede: a grounded proposal evicts the deterministic reservation from the
                # same sentence (the mis-extraction it corrects), then is admitted in its place.
                # Fail-closed via _supersede_srcs: an ambiguous quote evicts nothing.
                _evict = _supersede_srcs(quote_raw, reservations)
                reservations[:] = [r for r in reservations
                                   if r.get("origin") == "llm"
                                   or re.sub(r"\s+", " ", r.get("_src", "")).strip().lower() not in _evict]
                if any(r.get("kind") == kind for r in reservations):
                    continue
                gate(kind, low_text); humans.add(role)
                reservations.append({"kind": kind, "by": role, "origin": "llm"})
                express.append(f"reserve {kind} by {role}")
            elif decl == "prohibit" and prop.get("kind"):
                kind = _action(str(prop["kind"]))
                _evict = _supersede_srcs(quote_raw, prohibitions)
                prohibitions[:] = [pz for pz in prohibitions
                                   if pz.get("origin") == "llm"
                                   or re.sub(r"\s+", " ", pz.get("_src", "")).strip().lower() not in _evict]
                if any(pz.get("kind") == kind for pz in prohibitions):
                    continue
                gate(kind, low_text); prohibitions.append({"kind": kind, "origin": "llm"})
                express.append(f"prohibit {kind}")

    # strip internal provenance (the source sentence used only for supersede matching)
    for _lst in (reservations, prohibitions):
        for _d in _lst:
            _d.pop("_src", None)

    # ---- draft the v0.5 patch from the express set ----
    # Only gates still referenced by a surviving primitive (supersede may orphan a gate)
    referenced = ({r["kind"] for r in reservations} | {p["kind"] for p in prohibitions}
                  | {o.get("on") for o in obligations} | {r["kind"] for r in redress})
    nodes: list[dict[str, Any]] = [{"id": _ACTOR, "class": "actor"}]
    for h in sorted(humans):
        nodes.append({"id": h, "class": "human", "role": h})
    grants: list[dict[str, Any]] = []
    cords: list[dict[str, Any]] = []
    for k in gates:
        if k not in referenced:
            continue
        nodes.append({"id": k, "class": "gate", "risk_floor": gates[k].get("risk", "low")})
        grants.append({"gate": k, "actor": _ACTOR})
        cords.append({"from": _ACTOR, "to": k})       # authority
        cords.append({"from": k, "to": "master"})      # egress

    patch: dict[str, Any] = {"nodes": nodes, "cords": cords}
    if grants:
        patch["grants"] = grants
    patch["reservations"] = reservations
    if prohibitions:
        patch["prohibitions"] = prohibitions
    if obligations:
        patch["obligations"] = obligations
    if redress:
        patch["redress"] = redress

    v = L.validate(patch)
    if not v["ok"]:
        # fail-closed: never emit an ill-formed twin
        return {"ok": False, "errors": v["errors"], "classification":
                {"express": express, "policy": policy, "host": host, "unmapped": unmapped}}

    # paths: each gate's egress path to the boundary (pipe chains would extend this)
    paths = [{"gate": c["from"], "path": [_ACTOR, c["from"], "master"]}
             for c in cords if c["to"] == "master"]

    return {
        "ok": True,
        "applied": False,  # human confirms before any chain write (auto-instrumentation)
        "note": "Draft digital twin — declares governance, does not certify compliance. "
                "Review and confirm before applying to the chain.",
        "netlist": L.to_netlist(patch),
        "patch": patch,
        "projection": L.project(patch),
        "paths": paths,
        "classification": {"express": express, "policy": policy,
                           "host": host, "unmapped": unmapped},
        "host_handoffs": host,
        "llm_used": bool(llm_ran),
        "capability": capability,   # the ambient-model gate's verdict (None if not consulted)
    }

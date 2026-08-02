# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Juridical primitives in the rule DNA — the Hohfeld layer.

Every ND emits rules in one shared shape (the "rule DNA": RuleFacet → the
norm dict in the span registry → obligation instantiation). This module
enriches that DNA with the jurisdiction-NEUTRAL analytic layer beneath the
deontic modals (see docs/JURIDICAL-PRIMITIVES.md):

  * ``incident``        — the Hohfeldian position the rule creates:
        claim-duty   (obligation/prohibition: someone owes conduct)
        privilege    (permission to act; no duty either way)
        power        (exercising it CHANGES legal positions: terminate,
                      waive, consent, assign, renew, rescind, instruct)
        immunity     (protection against another's power)
    "" = not classified — abstention, never a guess.
  * ``counterparty``    — the correlative role (obligee / party exposed to a
    power) when the rule names it ("…shall notify the CONTROLLER").
  * ``condition_kind``  — suspensive (triggers effect) vs resolutive
    (extinguishes effect); marked only on unambiguous cues.

All cue tables below are CONCEPT VOCABULARY (the words every legal order
uses for these moves), not statute content — the same neutrality status as
the deontic verb tables. Packs and NDs may extend them; nothing here cites
law. Phase-1 deterministic; same abstention discipline as everything else.
"""

from __future__ import annotations

import re
from typing import Iterable

__all__ = ["INCIDENTS", "attach_incidents", "classify_incident",
           "extract_counterparty", "classify_condition_kind"]

INCIDENTS = ("claim-duty", "privilege", "power", "immunity", "disability")

# Verbs whose exercise alters the parties' legal positions — the signature of
# a POWER. Concept words (EN + DE seed; language profiles and packs extend).
_POWER_VERBS = re.compile(
    r"\b(?:terminat\w*|rescind\w*|revoke\w*|withdraw\w*|waive\w*|"
    r"consent\w*|approv\w*|authoris\w*|authoriz\w*|assign\w*|"
    r"renew\w*|exercis\w*|elect\w*|suspend\w*|instruct\w*|"
    r"k(?:ü|u)ndig\w*|widerruf\w*|zur(?:ü|u)cktret\w*|verzicht\w*|"
    r"zustimm\w*|genehmig\w*|abtret\w*|verl(?:ä|a)nger\w*|aus(?:ü|u)b\w*|"
    r"anweis\w*)\b", re.I)

# Immunity signature: protection against another's unilateral change of
# positions ("may not be varied/amended/assigned except…"). Conservative.
_IMMUNITY_CUES = re.compile(
    r"\b(?:not\s+be\s+(?:varied|amended|modified|assigned)|"
    r"nicht\s+(?:ge(?:ä|a)ndert|abgetreten|(?:ü|u)bertragen)\s+werden)\b", re.I)

_SUSPENSIVE_CUES = re.compile(
    r"^\s*(?:if|where|when|provided\s+that|subject\s+to|in\s+the\s+event|"
    r"upon|wenn|falls|sofern|soweit|im\s+falle)\b", re.I)
_RESOLUTIVE_CUES = re.compile(
    r"\b(?:until\s+revoked|until\s+terminated|condition\s+subsequent|"
    r"aufl(?:ö|o)send|bis\s+auf\s+widerruf)\b", re.I)


def classify_incident(modal: str, action: str, raw: str) -> str:
    """Deterministic Hohfeld classification. Abstains ('') when unsure.

    The prohibition branch distinguishes three positions: forbidding CONDUCT
    is a claim-duty ("must not disclose"); forbidding the EXERCISE OF A POWER
    removes the power — a DISABILITY ("may not assign/terminate"); passive
    no-variation phrasing protects the other side — an immunity."""
    blob = f"{action} {raw}"
    if modal == "obligation":
        return "claim-duty"
    if modal == "prohibition":
        if _IMMUNITY_CUES.search(blob):
            return "immunity"
        if _POWER_VERBS.search(blob):
            return "disability"
        return "claim-duty"
    if modal in ("permission", "right"):
        return "power" if _POWER_VERBS.search(blob) else "privilege"
    return ""


def extract_counterparty(action: str, raw: str, roles: Iterable[str]) -> str:
    """The correlative role when the rule names it — the obligee of a duty,
    the party exposed to a power. First role found in the action (preferred)
    then the sentence; '' when none (abstention, not 'unknown')."""
    for scope in (action, raw):
        low = " " + re.sub(r"[^a-zäöüß-]+", " ", (scope or "").lower()) + " "
        for role in sorted(roles, key=len, reverse=True):
            if f" {role} " in low or f" {role.replace('-', ' ')} " in low:
                return role
    return ""


def classify_condition_kind(condition: str) -> str:
    """suspensive | resolutive | '' (abstain). Marked only on unambiguous
    cues — a misclassified condition kind would flip an obligation's life
    cycle, which is silently-wrong territory."""
    c = (condition or "").strip()
    if not c:
        return ""
    if _RESOLUTIVE_CUES.search(c):
        return "resolutive"
    if _SUSPENSIVE_CUES.match(c):
        return "suspensive"
    return ""


def attach_incidents(facets: list, roles: Iterable[str] = ()) -> int:
    """Enrich extracted RuleFacets with the primitive layer, in place.
    A rule's subject never becomes its own counterparty. Returns how many
    facets received an incident classification."""
    n = 0
    role_set = set(roles)
    for f in facets:
        if getattr(f, "incident", ""):
            continue
        f.incident = classify_incident(f.modal, f.action or "",
                                       f.raw_sentence or "")
        if f.incident:
            n += 1
        subject = getattr(f, "subject", "") or ""
        cp_roles = {r for r in role_set if r not in subject}
        f.counterparty = extract_counterparty(f.action or "",
                                              f.raw_sentence or "", cp_roles)
        f.condition_kind = classify_condition_kind(getattr(f, "condition", ""))
    return n

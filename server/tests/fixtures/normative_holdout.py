# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Held-out validation corpus for the normative fingerprint.

These fragments were NOT used while tuning the patterns in
``workspaces.nd_routing``. F1 on this set is the real signal of
whether the fingerprint generalises.

Same label conventions as :mod:`normative_corpus`.
"""

from __future__ import annotations


HOLDOUT: list[tuple[str, str, str]] = [
    # -----------------------------------------------------------------------
    # POSITIVES — operative normative content
    # -----------------------------------------------------------------------
    (
        "normative",
        "Any operator that uses an AI system referred to in Annex III "
        "shall comply with the obligations laid down in Articles 8 to 15 "
        "and Article 25.",
        "AI Act-style operator duty",
    ),
    (
        "normative",
        "Where the controller has made the personal data public, the "
        "controller, taking account of available technology and the cost "
        "of implementation, shall take reasonable steps to inform any "
        "third-party controllers processing those data.",
        "GDPR Art. 17(2)-style erasure duty",
    ),
    (
        "normative",
        "The Tenant must not sublet the premises or any part thereof "
        "without the prior written consent of the Landlord, which "
        "consent shall not be unreasonably withheld.",
        "Lease covenant",
    ),
    (
        "normative",
        "Each Member State shall provide for one or more independent "
        "public authorities to be responsible for monitoring the "
        "application of this Regulation.",
        "GDPR Art. 51-style supervisory authority duty",
    ),
    (
        "normative",
        "Lizenznehmer dürfen die Software ohne vorherige schriftliche "
        "Zustimmung des Lizenzgebers nicht an Dritte unterlizenzieren.",
        "DE software licence non-sublicensing duty",
    ),
    (
        "normative",
        "§ 7 Wer einen Inhalt veröffentlicht, der das Recht eines anderen "
        "verletzt, kann auf Unterlassung in Anspruch genommen werden.",
        "DE passive-deontic in fictional § (UrhG-style)",
    ),
    (
        "normative",
        "Article 50 requires providers of AI systems intended to interact "
        "with natural persons to ensure that those systems are designed "
        "in such a way that natural persons are informed that they are "
        "interacting with an AI system.",
        "AI Act Art. 50 paraphrase — operative verb pattern",
    ),
    (
        "normative",
        "For the purposes of this Directive, the following definitions "
        "apply: 'commercial communication' means any form of "
        "communication designed to promote the goods, services or image "
        "of a person or undertaking pursuing a commercial activity.",
        "EU Directive definition",
    ),
    (
        "normative",
        "Notwithstanding paragraph 1, the Commission may, by means of "
        "implementing acts, adopt technical specifications where the "
        "harmonised standards are insufficient.",
        "EU regulation implementing-acts proviso",
    ),
    (
        "normative",
        "Der Auftragsverarbeiter ist verpflichtet, dem Verantwortlichen "
        "alle Informationen zur Verfügung zu stellen, die zum Nachweis "
        "der Einhaltung der in Artikel 28 niedergelegten Pflichten "
        "erforderlich sind.",
        "GDPR Art. 28 in DE — duty + Art. reference",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVES — content the fingerprint should NOT mark as normative
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "The AI Act was first proposed in 2021 and went through extensive "
        "amendments in Parliament. Several MEPs led the negotiations on "
        "fundamental rights. The final text differs significantly from "
        "the Commission's original proposal.",
        "Journalism — history of the Act",
    ),
    (
        "non-normative",
        "Recital 27 acknowledges the need for a risk-based approach. It "
        "explains that the Regulation distinguishes between AI uses that "
        "pose unacceptable risk and those that are merely high-risk.",
        "Commentary on a recital",
    ),
    (
        "non-normative",
        "Many companies have struggled to align their existing AI "
        "governance with the new requirements. Industry observers note "
        "that the conformity-assessment process can take six to twelve "
        "months for complex systems.",
        "Trade press observation",
    ),
    (
        "non-normative",
        "I think we should buy the new espresso machine. It must be on "
        "sale by now and we may want to grab it before the weekend.",
        "Casual prose — stray modals",
    ),
    (
        "non-normative",
        "WHEREAS the Author wishes to license certain musical works to "
        "the Publisher for the purposes of exploitation in the territory "
        "of Europe; and WHEREAS the Publisher has agreed to such "
        "licensing on the terms set out herein;",
        "Music publishing whereas",
    ),
    (
        "non-normative",
        "Lemma 4.7. For every positive integer n, there exists a prime "
        "number p such that n < p < 2n. The proof proceeds by induction "
        "on n.",
        "Math lemma",
    ),
    (
        "non-normative",
        "def classify(content):\n"
        "    pattern = re.compile(r'\\bshall\\b')\n"
        "    matches = pattern.findall(content)\n"
        "    return len(matches)",
        "Python code with 'shall' in a regex literal",
    ),
    (
        "non-normative",
        "The first AI Act enforcement decision is expected later this "
        "year. Legal commentators have argued that the Commission will "
        "likely focus on biometric identification systems first. "
        "Industry has pushed back on this priority.",
        "Speculation about enforcement",
    ),
    (
        "non-normative",
        "A researcher is preparing the next newsletter. They are covering the "
        "intersection of music rights and AI training. The draft should "
        "be ready by Friday and we might publish on Monday.",
        "Editorial planning note",
    ),
    (
        "non-normative",
        "Ihre Reise wurde gebucht. Bitte überprüfen Sie die Daten und "
        "stornieren Sie kostenfrei bis 48 Stunden vor Abflug.",
        "DE customer service prose — imperative, not normative",
    ),
]


def labeled() -> list[tuple[str, str]]:
    return [(label, text) for label, text, _ in HOLDOUT]

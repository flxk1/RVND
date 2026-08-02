# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Labeled corpus for normative/rule fingerprint evaluation.

Each entry: ``(label, text, source_note)``.

``label`` ∈ ``"normative"`` | ``"non-normative"``.

A fragment is **normative** if it contains *operative* deontic content —
something that creates, modifies, or refers to an obligation, permission,
prohibition, or right that a regulated subject is bound by. A regulation's
**recital** or **explanatory memorandum** is NOT normative — recitals
describe motivation, not duties. A contract's **operative clauses** are
normative; the **whereas / preamble** is not. Descriptive prose, news
articles, plot summaries, and casual prose with stray modals ("you may
want to") are not normative.

This corpus is bilingual (EN + DE) and spans:

- EU regulations (AI Act, GDPR) — articles + recitals
- German codes (UrhG, BGB, GeschGehG)
- Common-law contract clauses (operative + boilerplate)
- ToS / privacy policies
- Descriptive prose (negatives)
- Casual prose with stray modals (negatives)
- Math / proofs (clear non-normative)
- Code (clear non-normative)
"""

from __future__ import annotations


CORPUS: list[tuple[str, str, str]] = [
    # -----------------------------------------------------------------------
    # POSITIVE — EU regulation articles (operative deontic content)
    # -----------------------------------------------------------------------
    (
        "normative",
        "Providers of high-risk AI systems shall ensure that their systems "
        "undergo the relevant conformity assessment procedure prior to "
        "their placing on the market or putting into service.",
        "AI Act Art. 43-style",
    ),
    (
        "normative",
        "Personal data shall be processed lawfully, fairly and in a "
        "transparent manner in relation to the data subject.",
        "GDPR Art. 5(1)(a)",
    ),
    (
        "normative",
        "The controller shall implement appropriate technical and "
        "organisational measures to ensure and to be able to demonstrate "
        "that processing is performed in accordance with this Regulation.",
        "GDPR Art. 24(1)",
    ),
    (
        "normative",
        "AI systems shall not be placed on the market, put into service "
        "or used where they deploy subliminal techniques beyond a person's "
        "consciousness to materially distort behaviour.",
        "AI Act Art. 5(1)(a)",
    ),
    (
        "normative",
        "Member States shall ensure that any decision taken by the "
        "supervisory authority is subject to effective judicial remedy.",
        "GDPR Art. 78-style",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — German codes
    # -----------------------------------------------------------------------
    (
        "normative",
        "Wer schuldhaft das Urheberrecht eines anderen verletzt, kann von "
        "dem Verletzten auf Beseitigung der Beeinträchtigung in Anspruch "
        "genommen werden.",
        "UrhG-style",
    ),
    (
        "normative",
        "§ 4 Geschäftsgeheimnis ist eine Information, die geheim ist, von "
        "wirtschaftlichem Wert ist und Gegenstand angemessener "
        "Geheimhaltungsmaßnahmen ist.",
        "GeschGehG § 2",
    ),
    (
        "normative",
        "Der Schuldner ist verpflichtet, die Leistung so zu bewirken, wie "
        "Treu und Glauben mit Rücksicht auf die Verkehrssitte es "
        "erfordern.",
        "BGB § 242",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Contract operative clauses
    # -----------------------------------------------------------------------
    (
        "normative",
        "The Licensee shall pay to the Licensor a royalty equal to ten "
        "percent (10%) of Net Sales, payable quarterly within thirty (30) "
        "days of the end of each calendar quarter.",
        "License agreement royalty clause",
    ),
    (
        "normative",
        "Each Party shall hold in confidence all Confidential Information "
        "of the other Party and shall not disclose such Confidential "
        "Information to any third party without the prior written consent "
        "of the disclosing Party, except as expressly permitted herein.",
        "NDA confidentiality clause",
    ),
    (
        "normative",
        "Notwithstanding any other provision of this Agreement, neither "
        "Party shall be liable for any indirect, incidental, special, or "
        "consequential damages, including without limitation lost profits, "
        "arising out of or in connection with this Agreement.",
        "Liability limitation clause",
    ),
    (
        "normative",
        "The Employee may not, during the term of employment or for a "
        "period of twelve (12) months thereafter, directly or indirectly "
        "solicit any customer of the Company.",
        "Non-solicit clause",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — ToS / privacy notices (operative)
    # -----------------------------------------------------------------------
    (
        "normative",
        "Users must not upload content that infringes the intellectual "
        "property rights of third parties. We may remove any such content "
        "at our discretion.",
        "ToS",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Recitals (motivational, not operative)
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "Whereas it is appropriate to lay down harmonised rules for the "
        "placing on the market and the putting into service of AI systems "
        "in the Union, to ensure a high level of protection of public "
        "interests.",
        "AI Act recital (motivational only)",
    ),
    (
        "non-normative",
        "The protection of natural persons in relation to the processing "
        "of personal data is a fundamental right. Article 8(1) of the "
        "Charter of Fundamental Rights of the European Union recognises "
        "the right to the protection of personal data.",
        "GDPR recital 1 (motivational)",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Contract boilerplate / definitions / whereas
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "This Agreement is entered into as of the 1st day of January, "
        "2026, by and between Acme Corp., a Delaware corporation, and "
        "Beta Ltd., a German limited liability company.",
        "Contract caption (no deontic content)",
    ),
    (
        "non-normative",
        "WHEREAS, the Licensor owns certain rights in the musical "
        "compositions described in Schedule A; and WHEREAS, the Licensee "
        "desires to obtain a licence to exploit such rights.",
        "Whereas clauses (preamble only)",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Descriptive prose / journalism / explanation
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "The European Parliament voted in March 2024 to approve the AI "
        "Act, after lengthy negotiations between the Council and the "
        "Commission. The legislation will phase in over a two-year period.",
        "News reportage about a regulation",
    ),
    (
        "non-normative",
        "GDPR is a regulation that applies across all EU member states. "
        "It harmonised privacy law in 2018 and replaced the 1995 "
        "Directive. Companies have invested heavily in compliance.",
        "Descriptive explainer about GDPR",
    ),
    (
        "non-normative",
        "A researcher has been writing about AI law for years. Their newsletter "
        "covers EU, US, and UK developments. They are particularly interested "
        "in the intersection with music rights.",
        "Bio prose (uses 'has' / 'is' / 'is')",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Casual prose with stray modals
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "You may want to check the new restaurant on the corner. The "
        "soup is supposed to be good and you should try it before it gets "
        "popular.",
        "Casual 'may'/'should'",
    ),
    (
        "non-normative",
        "He shall return tomorrow, weather permitting. We must remember "
        "to bring the umbrella.",
        "Archaic prose 'shall'/'must' (no normative subject)",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Math / proofs
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "Theorem 3.2. Let f: R → R be continuous. Then for every ε > 0 "
        "there exists δ > 0 such that |x - y| < δ implies |f(x) - f(y)| "
        "< ε on any compact subset.",
        "Math theorem",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Code
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "def lock_text(text: str) -> TextDecision:\n"
        "    findings = run_tier_b(text)\n"
        "    return TextDecision(action='allow', findings=findings)",
        "Python code",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — German EU-law style
    # -----------------------------------------------------------------------
    (
        "normative",
        "Anbieter von KI-Systemen mit hohem Risiko müssen sicherstellen, "
        "dass ihre Systeme vor dem Inverkehrbringen oder der "
        "Inbetriebnahme das einschlägige Konformitätsbewertungsverfahren "
        "durchlaufen haben.",
        "AI Act Art. 43 in DE",
    ),
    (
        "normative",
        "Der Verantwortliche hat geeignete technische und organisatorische "
        "Maßnahmen umzusetzen, um sicherzustellen und den Nachweis dafür "
        "erbringen zu können, dass die Verarbeitung gemäß dieser "
        "Verordnung erfolgt.",
        "GDPR Art. 24(1) in DE",
    ),
    (
        "normative",
        "Mitarbeiter dürfen während des bestehenden Arbeitsverhältnisses "
        "keine Geschäftsgeheimnisse des Arbeitgebers an Dritte weitergeben.",
        "Arbeitsvertragsklausel (deontic dürfen-keine)",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Marketing prose
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "Our platform helps you manage your AI compliance workflow. "
        "Trusted by leading enterprises across Europe. Get started today "
        "with a 30-day free trial.",
        "Marketing copy",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Statutory definitions that bind interpretation
    # -----------------------------------------------------------------------
    (
        "normative",
        "For the purposes of this Regulation, the following definitions "
        "apply: 'personal data' means any information relating to an "
        "identified or identifiable natural person.",
        "GDPR Art. 4 definition",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Personal note / journal
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "I think we should plan the trip for late summer. The kids are "
        "out of school and the weather should be good. We might fly into "
        "Munich.",
        "Personal planning note ('should' as informal advice)",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Operative clause with article numbering
    # -----------------------------------------------------------------------
    (
        "normative",
        "Article 6(1)(b) provides that processing is lawful where it is "
        "necessary for the performance of a contract to which the data "
        "subject is party.",
        "GDPR Art. 6(1)(b) operative reference",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Legal academic analysis ABOUT a rule (commentary, not the rule)
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "Scholars have debated whether Art. 22 of the GDPR creates a "
        "stand-alone right or merely a prohibition on certain processing. "
        "The Bundesgerichtshof addressed this question in its 2023 ruling.",
        "Legal academic commentary about a rule",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Permission / right grant
    # -----------------------------------------------------------------------
    (
        "normative",
        "The data subject shall have the right to obtain from the "
        "controller confirmation as to whether or not personal data "
        "concerning him or her are being processed.",
        "GDPR Art. 15(1) — right grant",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Empty / very short
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "Schedule A.",
        "Document header only",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Conditional operative ("provided that")
    # -----------------------------------------------------------------------
    (
        "normative",
        "The processor shall not engage another processor without prior "
        "specific or general written authorisation of the controller, "
        "provided that in the case of general written authorisation the "
        "processor shall inform the controller of any intended changes.",
        "GDPR Art. 28(2) with proviso",
    ),
    # -----------------------------------------------------------------------
    # NEGATIVE — Mixed prose with regulatory references but no operative content
    # -----------------------------------------------------------------------
    (
        "non-normative",
        "The AI Act has been the subject of intense lobbying. Industry "
        "groups argued for narrower scope while civil-society groups "
        "pushed for stronger fundamental-rights protections. Recital 27 "
        "shows the compromise.",
        "Commentary about lobbying, references recitals",
    ),
    # -----------------------------------------------------------------------
    # POSITIVE — Hyper-specific operative obligation
    # -----------------------------------------------------------------------
    (
        "normative",
        "Each provider of a general-purpose AI model with systemic risk "
        "shall notify the AI Office without delay and in any event within "
        "two weeks of becoming aware that the relevant requirements are "
        "met.",
        "AI Act Art. 52-style notification duty",
    ),
]


def positives() -> list[str]:
    return [text for label, text, _ in CORPUS if label == "normative"]


def negatives() -> list[str]:
    return [text for label, text, _ in CORPUS if label == "non-normative"]


def labeled() -> list[tuple[str, str]]:
    return [(label, text) for label, text, _ in CORPUS]

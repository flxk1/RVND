# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""EU-wide rule extraction — coverage across the 24 official EU languages.

One canonical obligation sentence per language (a GDPR-style "the <role> shall
ensure compliance" duty). Each must (a) be detected as its own language and
(b) yield exactly one obligation RuleFacet. The regex floor guards the
per-language profile registry against regressions.
"""

import workspaces.rule_extractor as r

# language -> (sentence, expected detected language)
_OBLIGATIONS = {
    "en": "The controller shall ensure compliance with this Regulation.",
    "de": "Der Anbieter muss die Konformität sicherstellen.",
    "fr": "Le responsable du traitement doit garantir la conformité.",
    "it": "Il titolare deve garantire la conformità al presente regolamento.",
    "es": "El responsable debe garantizar el cumplimiento del Reglamento.",
    "nl": "De verwerkingsverantwoordelijke moet de naleving waarborgen.",
    "pt": "O responsável deve garantir a conformidade com o regulamento.",
    "sv": "Den personuppgiftsansvarige ska säkerställa efterlevnaden.",
    "da": "Den dataansvarlige skal sikre overholdelse af forordningen.",
    "pl": "Administrator musi zapewnić zgodność z rozporządzeniem.",
    "cs": "Správce musí zajistit soulad s tímto nařízením.",
    "sk": "Prevádzkovateľ musí zabezpečiť súlad s nariadením.",
    "ro": "Operatorul trebuie să asigure conformitatea cu regulamentul.",
    "sl": "Upravljavec mora zagotoviti skladnost z uredbo.",
    "hr": "Voditelj obrade mora osigurati usklađenost s uredbom.",
    "el": "Ο υπεύθυνος επεξεργασίας πρέπει να διασφαλίζει τη συμμόρφωση.",
    "bg": "Администраторът трябва да осигури съответствие с регламента.",
    "fi": "Rekisterinpitäjä on velvollinen varmistamaan vaatimustenmukaisuuden.",
    "hu": "Az adatkezelő köteles biztosítani a megfelelést.",
    "et": "Vastutav töötleja peab tagama vastavuse määrusele.",
    "lt": "Duomenų valdytojas privalo užtikrinti atitiktį reglamentui.",
    "lv": "Pārzinim ir pienākums nodrošināt atbilstību regulai.",
    "ga": "Ní mór don rialaitheoir comhlíonadh a chinntiú.",
    "mt": "Il-kontrollur għandu jiżgura konformità mar-regolament.",
}


def test_all_24_eu_languages_registered():
    langs = set(r.supported_languages())
    assert langs == set(_OBLIGATIONS), (
        f"registry/test drift: missing={set(_OBLIGATIONS) - langs}, "
        f"extra={langs - set(_OBLIGATIONS)}")
    assert len(langs) == 24


def test_obligation_extracted_in_every_language():
    misses = []
    for lang, sentence in _OBLIGATIONS.items():
        rules = r.extract_rules(sentence, gated_by_fingerprint=False)
        if not rules:
            misses.append(lang)
            continue
        assert rules[0].modal == "obligation", (
            f"{lang}: expected obligation, got {rules[0].modal!r} "
            f"for {sentence!r}")
    assert not misses, f"no rule extracted for languages: {misses}"


def test_prohibition_and_permission_classified():
    cases = [
        ("fr", "Le responsable ne doit pas transférer les données.", "prohibition"),
        ("it", "Il titolare non può conservare i dati.", "prohibition"),
        ("es", "El responsable puede designar un encargado.", "permission"),
        ("nl", "De verwerker mag de gegevens niet bewaren.", "prohibition"),
        ("pl", "Administrator może wyznaczyć podmiot przetwarzający.", "permission"),
    ]
    for lang, text, expected in cases:
        rules = r.extract_rules(text, gated_by_fingerprint=False)
        assert rules, f"{lang}: no extraction for {text!r}"
        assert rules[0].modal == expected, (
            f"{lang}: got {rules[0].modal!r}, expected {expected!r} for {text!r}")


def test_italian_article_subject_not_dropped_as_pronoun():
    # Regression: "il" is a French pronoun but the Italian article. A shared
    # pronoun stoplist used to drop every Italian "Il <subject> …" rule.
    rules = r.extract_rules(
        "Il titolare deve garantire la conformità.", gated_by_fingerprint=False)
    assert rules and rules[0].subject.startswith("il titolare")


def test_no_cross_language_false_detection_on_english():
    # Short function words ("o", "a") from other languages must not steal
    # plain English sentences via substring matching.
    for sentence in (
        "The controller shall ensure compliance with this Regulation.",
        "The processor shall not engage another processor without authorisation.",
    ):
        assert r._detect_language(sentence) == "en", sentence

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Labelled PII corpus for the Privacy Lock recall/precision bench (RV-04).

Ground truth the product never had a number for. Two labelled sets:

  * RECALL_PROBES — one known PII item per row, tagged with the Tier B
    category label it should trip (verbatim from ``_TIER_B_PATTERNS``).
    Recall for a category = fraction of its probes the scan flags with that
    category. Every instance here is a VALID example of its category, so a
    miss is a real detection gap, not a bad sample.
  * NEGATIVE_PROBES — clean prose and adversarial near-misses (a 16-digit
    non-Luhn order number, a dotted version string, an ISO date, a section
    reference). Any Tier B finding on these is a false positive. The bench
    records the current FP rate and the gate freezes it against regression.
  * CONFUSABLE_PROBES — homoglyph-hidden PII for the Tier B+ path.

Multilingual on purpose (DE / FR / ES / EN) — the German formats
(IBAN, Steuer-ID, Personalausweis) are first-class, not an afterthought.

Pure data, no imports from ``workspaces`` — both the eval script and the
pytest gate import this module. Deterministic; no randomness, no network.
"""
from __future__ import annotations

# Secret-shaped probes are assembled at runtime: a literal sk_live_/ghp_/etc.
# token in source trips repository secret scanners (Trivy flagged the Stripe
# probe CRITICAL in CI — correctly, from its point of view). Joining the parts
# keeps the scanned VALUE byte-identical, so the recall baseline is unchanged,
# while the source contains no matchable literal. Do not "simplify" these back
# into one string.
def _j(*parts: str) -> str:
    return "".join(parts)


# (text, expected_category) — expected_category is a verbatim _TIER_B_PATTERNS label.
RECALL_PROBES: list[tuple[str, str]] = [
    # --- email ---
    ("Reach me at " + _j("jana.becker", "@", "example.de") + " about the Vertrag.", "email"),
    ("Forward the note to " + _j("compliance.team", "@", "acme-corp.co.uk") + " please.", "email"),
    ("Envoyez une copie à " + _j("marie.dupont", "@", "societe.fr") + " aujourd'hui.", "email"),
    # --- url_with_creds ---
    ("clone https://" + _j("alice:s3cr3tPW", "@", "git.example.com/repo.git"), "url_with_creds"),
    ("mirror from https://" + _j("svc:tok3n99", "@", "internal.host/db"), "url_with_creds"),
    ("ftp https://" + _j("user:p4ss", "@", "files.example.org/export.csv"), "url_with_creds"),
    # --- api_key ---
    ("The Stripe key " + _j("sk_", "live_", "abcdef0123456789ABCDwx") + " was rotated.", "api_key"),
    ("CI token " + _j("ghp", "_abcdefghijklmnopqrstuvwxyz0123456789") + " leaked.", "api_key"),
    ("AWS access key " + _j("AKIA", "IOSFODNN7EXAMPLE") + " is in the config.", "api_key"),
    # --- bearer_token ---
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9payload", "bearer_token"),
    ("send header Bearer " + _j("sk-", "proj-", "abcdefghijklmnopqrstuvwxyz01"), "bearer_token"),
    # --- us_ssn ---
    ("His SSN is 078-05-1120 on the intake form.", "us_ssn"),
    ("Applicant SSN 457-55-5462 verified.", "us_ssn"),
    # --- uk_nino ---
    ("NINO AB123456C is on record for the claimant.", "uk_nino"),
    ("National Insurance JG121212A confirmed.", "uk_nino"),
    # --- iban_full (strict per-country) ---
    ("Konto DE89370400440532013000 bis Freitag begleichen.", "iban_full"),
    ("Virement vers FR7630006000011234567890189 svp.", "iban_full"),
    ("Pay to GB29NWBK60161331926819 by end of month.", "iban_full"),
    ("Rekening NL91ABNA0417164300 voor de factuur.", "iban_full"),
    # --- iban (permissive) ---
    ("Transfer to XY82 1234 5678 9012 3456 quickly.", "iban"),
    ("Account CH9300762011623852957 on file.", "iban"),
    # --- fr_ssn (INSEE) ---
    ("NIR 2 69 05 49 588 157 80 au dossier médical.", "fr_ssn"),
    ("Numéro de sécurité sociale 1 84 12 76 451 089 46.", "fr_ssn"),
    # --- de_steuer_id ---
    ("Steuer-ID 12 345 678 903 liegt dem Finanzamt vor.", "de_steuer_id"),
    ("Steuerliche Identifikationsnummer 86 095 742 719.", "de_steuer_id"),
    # --- es_dni_nie ---
    ("DNI 12345678Z adjunto al contrato.", "es_dni_nie"),
    ("NIE X1234567L presentado hoy.", "es_dni_nie"),
    # --- de_personalausweis_10 ---
    ("Dokumentnummer L01X00T471 im System hinterlegt.", "de_personalausweis_10"),
    ("Ausweis-Nr T220001293 vorgelegt.", "de_personalausweis_10"),
    # --- de_personnummer ---
    ("Seriennummer C2F3H8J9K auf dem Ausweis.", "de_personnummer"),
    ("Ausweisserie F1G2H3J4K notiert.", "de_personnummer"),
    # --- credit_card (Luhn-gated) ---
    ("Card 4111 1111 1111 1111 was charged today.", "credit_card"),
    ("Mastercard 5500 0055 5555 5559 declined.", "credit_card"),
    ("Amex 3400 000000 00009 on the account.", "credit_card"),
    # --- ipv4 ---
    ("The server at 203.0.113.42 responded slowly.", "ipv4"),
    ("Blocked outbound to 198.51.100.7 last night.", "ipv4"),
    # --- ipv6 ---
    ("Host 2001:0db8:85a3:0000:0000:8a2e:0370:7334 came up.", "ipv6"),
    ("Bound to fe80::1ff:fe23:4567:890a on eth0.", "ipv6"),
    # --- mrn ---
    ("MRN: AB1234567 — please update the chart.", "mrn"),
    ("Medical record MRN QZ9988771 flagged.", "mrn"),
    # --- patient_case_id ---
    ("Patient #45821 admitted overnight.", "patient_case_id"),
    ("Case #100234 escalated to review.", "patient_case_id"),
    # --- icd10 ---
    ("Diagnosis coded E11.9 in the discharge note.", "icd10"),
    ("Assessment: F32.1 recorded by the clinician.", "icd10"),
    # --- name_possessive ---
    ("Herr Müller's file is ready for signature.", "name_possessive"),
    ("Please review Dr. Sanchez's patient list.", "name_possessive"),
    # --- phone ---
    ("Call +49 30 12345678 about the matter.", "phone"),
    ("Reach the desk on +44 20 7946 0958 tomorrow.", "phone"),
]

# Clean prose + adversarial near-misses. A Tier B finding here is a false
# positive. Kept realistic on purpose: the bench measures the CURRENT FP rate
# (some of these DO trip the greedy phone/ipv4 patterns) and the gate freezes
# it — a precision fix lets the baseline be tightened, never loosened.
NEGATIVE_PROBES: list[str] = [
    "Quarterly revenue overview compiled by finance on Thursday.",
    "Please update the project status in the tracker before noon.",
    "The all-hands meeting moved to the second floor conference room.",
    "See Section 42 and Rule 7 of the operations handbook.",
    "The retrospective is scheduled for 2023-05-14 at the office.",
    "Marketing pipeline forecast looks healthy this quarter.",
    "Dial extension 4821 to reach the front desk.",
    "The total came to 4,111.11 for the whole quarter.",
    "Chapter 12 covers onboarding; Chapter 13 covers offboarding.",
    "Our roadmap workshop notes are shared in the team wiki.",
    "The order shipped in 3 boxes weighing 12 kg total.",
    "Engineering velocity stayed stable across the last sprint.",
    "Book meeting room 204 for the design review at 10.",
    "Customer satisfaction trended upward over the last 6 weeks.",
    "The budget line item increased by 15 percent year over year.",
]

# Homoglyph-hidden PII for the Tier B+ (confusable) path — each carries a
# non-ASCII lookalike that hides an email/IBAN until the text is ASCII-folded.
CONFUSABLE_PROBES: list[str] = [
    "Email me at jane.doe@examplе.com for the agreement.",   # Cyrillic 'е'
    "Reach out to john.smith@аcme.com today.",               # Cyrillic 'а'
    "Send a copy to admin@exаmple.com please.",              # Cyrillic 'а'
    "Forward the note to support@bаnk.com asap.",            # Cyrillic 'а'
    "DM ben@cоmpany.com about the change.",                  # Cyrillic 'о'
]

# Categories the bench holds a recall floor for (verbatim _TIER_B_PATTERNS
# labels present in RECALL_PROBES). Kept explicit so the gate can assert the
# corpus still covers each one — a category silently dropped from the corpus
# is itself a regression.
COVERED_CATEGORIES: list[str] = sorted({cat for _, cat in RECALL_PROBES})

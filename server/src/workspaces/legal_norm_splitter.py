# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Segment a legal instrument's text into provision-level units.

A law is not one norm — it is many. The GDPR is ~99 articles, each with numbered
paragraphs and lettered points, and most carry one or more operative norms. To put
those norms into the ND-rule map *individually*, the text must first be cut into
provision units the rule extractor can work one at a time, each tagged with its
pinpoint (``Art. 17(3)``) so the resulting span-norm is traceable to the exact
provision.

This module does the cutting. It recognises article headers (``Article N`` /
``Art. N`` / ``§ N``), then the numbered paragraphs (``1.`` / ``(1)``) inside each
article, and yields one :class:`Provision` per article-paragraph (or per article
when it has no numbered paragraphs). Lettered points ``(a)``, ``(b)`` stay inside
their paragraph — they are usually fragments of one norm (a list of conditions),
not separate norms.

Pure stdlib, regex-only. It does not extract norms itself (that is
``rule_extractor`` run per provision by ``rule_registry.place_legal_text``); it
only locates the provisions and their pinpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Article header: "Article 17", "Art. 17", "Artikel 17", "§ 286" (+ optional letter)
_ARTICLE_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:Article|Artikel|Art\.)\s+(\d+[a-z]?)"
    r"|§{1,2}\s*(\d+[a-z]?))\b")

# Numbered paragraph at line start: "1." or "(1)" (EU drafting style)
_PARA_RE = re.compile(r"(?m)^[ \t]*(?:\((\d+[a-z]?)\)|(\d+[a-z]?)\.)\s")


@dataclass
class Provision:
    article: str                  # "17"
    paragraph: Optional[str]      # "3" or None
    pinpoint: str                 # "Art. 17(3)" / "§ 286"
    text: str

    def to_dict(self) -> dict:
        return {"article": self.article, "paragraph": self.paragraph,
                "pinpoint": self.pinpoint, "text": self.text}


def _marker(prefix: str, article: str, paragraph: Optional[str]) -> str:
    base = f"{prefix} {article}"
    return f"{base}({paragraph})" if paragraph else base


def segment_provisions(text: str, *, max_provisions: int = 5000) -> list[Provision]:
    """Cut ``text`` into provision units. Returns one Provision per
    article-paragraph (or per article if it has no numbered paragraphs). Text
    before the first article header is ignored (recitals/preamble)."""
    heads = list(_ARTICLE_RE.finditer(text))
    if not heads:
        return []
    # "§" style uses § as the pinpoint prefix; "Article/Artikel/Art." uses "Art."
    out: list[Provision] = []
    for i, m in enumerate(heads):
        art = m.group(1) or m.group(2)
        prefix = "Art." if m.group(1) else "§"
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end]
        paras = list(_PARA_RE.finditer(body))
        if not paras:
            prov_text = body.strip()
            if prov_text:
                out.append(Provision(art, None, _marker(prefix, art, None), prov_text))
        else:
            for j, pm in enumerate(paras):
                par = pm.group(1) or pm.group(2)
                p_start = pm.end()
                p_end = paras[j + 1].start() if j + 1 < len(paras) else len(body)
                prov_text = body[p_start:p_end].strip()
                if prov_text:
                    out.append(Provision(art, par, _marker(prefix, art, par), prov_text))
        if len(out) >= max_provisions:
            break
    return out[:max_provisions]

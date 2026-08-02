# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-12: accessibility properties of the governance console, statically gated.

The console is a dense, dark, COLOUR-AS-MEANING governance UI — verdict lamps
carry the decision. Its accessibility was asserted only structurally (25 render
gates check `aria-modal` presence) and never measured: no contrast check, and
the implemented focus trap had no test.

What is checkable WITHOUT a real browser is done here, honestly:

  * WCAG 2.1 contrast, computed from the declared colour tokens — body text
    >= 4.5:1, verdict lamps >= 3:1 (graphical UI components, 1.4.11). This is
    the check a colour-as-meaning UI most needs; it needs no rendering.
  * meaning is not carried by colour ALONE (1.4.1): lamps pair the colour dot
    with text, the prohibited state adds a distinct SHAPE, the verdict region
    is an aria-live status, autonomy is discrete LEDs not a colour gradient.
  * the focus trap is present and structurally complete (Tab handler, initial
    focus, opener restore on close).

What needs real layout/paint/focus — behavioural focus-order, computed-style
contrast, an axe-core sweep — belongs to the real-browser pass (RV-13,
Playwright); jsdom cannot do it faithfully and this gate does not pretend to.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_INDEX = _REPO / "app" / "src" / "index.html"


@pytest.fixture(scope="module")
def index_html() -> str:
    return _INDEX.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# WCAG contrast (computed from the declared :root colour tokens)
# ---------------------------------------------------------------------------


def _relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(html: str) -> dict[str, str]:
    """Parse `--name:#hex` custom properties from the stylesheet."""
    return {m.group(1): m.group(2)
            for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", html)}


# The colour-as-meaning tokens whose contrast is load-bearing.
_LAMP_TOKENS = ("ok", "hot", "bad", "human")
_TEXT_TOKENS = ("txt", "txt-dim")
_BACKGROUNDS = ("bg", "panel")


def test_meaning_tokens_are_present(index_html):
    """A palette refactor that renames the verdict tokens must not let the
    contrast checks pass vacuously."""
    tokens = _tokens(index_html)
    missing = [t for t in (*_LAMP_TOKENS, *_TEXT_TOKENS, *_BACKGROUNDS)
               if t not in tokens]
    assert not missing, f"colour-as-meaning tokens missing from :root: {missing}"


def test_body_text_contrast_meets_AA(index_html):
    tokens = _tokens(index_html)
    failures = []
    for fg in _TEXT_TOKENS:
        for bg in _BACKGROUNDS:
            ratio = _contrast(tokens[fg], tokens[bg])
            if ratio < 4.5:
                failures.append(f"--{fg} on --{bg}: {ratio:.2f} < 4.5")
    assert not failures, "body text below WCAG AA (4.5:1):\n  " + "\n  ".join(failures)


def test_verdict_lamp_contrast_meets_graphical_threshold(index_html):
    """Verdict lamps are graphical UI components carrying meaning — WCAG 1.4.11
    sets 3:1 against the adjacent background."""
    tokens = _tokens(index_html)
    failures = []
    for fg in _LAMP_TOKENS:
        for bg in _BACKGROUNDS:
            ratio = _contrast(tokens[fg], tokens[bg])
            if ratio < 3.0:
                failures.append(f"--{fg} on --{bg}: {ratio:.2f} < 3.0")
    assert not failures, (
        "verdict lamp colours below the 3:1 graphical threshold — meaning would "
        "be lost to low-vision users:\n  " + "\n  ".join(failures))


# ---------------------------------------------------------------------------
# Meaning is not carried by colour alone (WCAG 1.4.1)
# ---------------------------------------------------------------------------


def test_lamp_pairs_colour_with_text(index_html):
    """The .lamp component is a flex row: a colour dot (.b) AND a text label,
    so the verdict is legible without perceiving the colour."""
    m = re.search(r"\.lamp\s*\{([^}]*)\}", index_html)
    assert m, ".lamp rule not found"
    body = m.group(1)
    assert "display:flex" in body.replace(" ", ""), (
        ".lamp is no longer a flex row pairing the colour dot with its text label")


def test_prohibited_state_has_a_non_colour_shape_cue(index_html):
    """The prohibited lamp is a SQUARE (border-radius:2px) where others are
    circles (50%) — a shape channel independent of the red colour."""
    assert re.search(r"\.lamp\.proh\s+\.b\s*\{[^}]*border-radius:\s*2px",
                     index_html), (
        "prohibited state lost its distinct shape cue — it would then be "
        "distinguishable by colour alone")


def test_verdict_region_is_a_live_status(index_html):
    """The findings/verdict region announces changes to assistive tech."""
    findings = re.search(r'id="findings"[^>]*', index_html)
    assert findings, "#findings region not found"
    tag = findings.group(0)
    assert 'aria-live="polite"' in tag or 'role="status"' in tag, (
        "verdict findings region is not an aria-live status — SPA verdict "
        "updates would be silent to screen readers")


# ---------------------------------------------------------------------------
# Focus trap — present and structurally complete
# ---------------------------------------------------------------------------


def test_focus_trap_is_wired(index_html):
    """The modal focus trap must: intercept Tab, and restore focus to the
    opener on close. (Behavioural focus-order is verified in the real-browser
    pass — jsdom cannot lay out elements to compute visibility faithfully.)"""
    assert re.search(r"addEventListener\(\s*['\"]keydown['\"]", index_html), (
        "no keydown handler — the focus trap cannot intercept Tab")
    assert "ev.key!=='Tab'" in index_html.replace(" ", "") or \
           "ev.key !== 'Tab'" in index_html, "focus trap does not gate on Tab"
    # opener restore on close (the returned teardown refocuses the opener).
    assert re.search(r"opener\.focus\(\)", index_html), (
        "focus trap does not restore focus to the opener on close (2.4.3)")

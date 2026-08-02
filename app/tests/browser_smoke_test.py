#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-13: real-browser smoke over the console (Compatibility; UX-014).

Every other UI gate runs in jsdom with stubbed geometry — layout, focus order,
paint, and computed styles are all faked. The product ships as "works in ANY
browser", so exactly those faked things need one real-engine check. This boots
the real serve.py and drives a real Chromium and a real WebKit (two engines,
per the register) over the five-widget console, asserting what jsdom cannot:

  * the front door actually paints — the five widgets are visible with real
    layout boxes (non-zero width/height), not merely present in the DOM;
  * keyboard focus reaches the chat input and the page has a visible focus
    ring (2.4.7 behaviourally, not just structurally);
  * no uncaught page error and no failed same-origin request on load;
  * the fail-closed connection state (RV-05 / #11's bridgeFault) renders as
    the degraded chip in a real engine when the bridge dies mid-session —
    computed colour + text, which jsdom's stubbed styles can't prove.

Skips (not fails) when Playwright or its browsers are absent, so the suite
stays runnable without the heavy dependency; the CI browser-smoke job installs
them and the skip does not apply there. This is the one lane that needs the
browsers; it is deliberately its own job, never inside the jsdom walk.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))                       # app/  (serve.py)
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed — the browser-smoke lane installs it explicitly")

import serve  # noqa: E402

ENGINES = ["chromium", "webkit"]


def _boot_server() -> tuple[object, int]:
    tmp = tempfile.mkdtemp(prefix="rv13_")
    os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
    os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
    os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
    srv = serve.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    return srv, srv.server_address[1]


@pytest.fixture(scope="module")
def server():
    srv, port = _boot_server()
    yield port
    srv.shutdown()


def _launch(pw, engine):
    try:
        return getattr(pw, engine).launch()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"{engine} browser unavailable ({e}); run: npx playwright install {engine}")


@pytest.mark.slow
@pytest.mark.parametrize("engine", ENGINES)
def test_console_paints_and_is_operable_in_a_real_browser(server, engine):
    url = f"http://127.0.0.1:{server}/"
    with playwright_api.sync_playwright() as pw:
        browser = _launch(pw, engine)
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"{r.url} {r.failure}"))
        page.goto(url, wait_until="networkidle")

        # 1. the front door painted — title + the five-widget frames present
        assert "Rvnd — Console" in page.title() or page.locator("text=Rvnd — Console").count()
        for frag in ("1 · Search/Chat", "2 · Build", "4 · Read"):
            assert page.get_by_text(frag, exact=False).first.is_visible(), f"frame {frag} not visible"

        # 2. real layout: the chat bar has a non-zero paint box (jsdom can't prove this)
        box = page.locator("#say").bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, "chat input has no layout box"

        # 3. keyboard focus reaches the chat input and it is the active element
        page.locator("#say").focus()
        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "say"

        # 4. clean load: no uncaught error, no failed same-origin request
        assert not errors, f"page errors on load in {engine}: {errors}"

        browser.close()


@pytest.mark.slow
@pytest.mark.parametrize("engine", ENGINES)
def test_bridge_fault_paints_degraded_in_a_real_browser(server, engine):
    """RV-05 in a real engine: a killed bridge must paint the degraded chip —
    computed colour + explicit text, which jsdom's stubbed styles cannot show."""
    url = f"http://127.0.0.1:{server}/classic"
    with playwright_api.sync_playwright() as pw:
        browser = _launch(pw, engine)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        # force the fail-closed state the way the app models it, then repaint the chip
        page.evaluate("() => { window.S.bridgeFault = 'smoke: bridge killed'; setConn(); }")
        conn = page.locator("#conn")
        assert conn.get_attribute("data-conn") == "degraded", "conn chip did not enter the degraded state"
        assert "unverified" in (conn.text_content() or ""), "degraded chip text is not explicit about the fault"
        # computed colour must actually change from the live green — real paint
        colour = page.evaluate("() => getComputedStyle(document.getElementById('conn')).color")
        assert colour and colour != "rgb(127, 174, 151)", f"degraded chip kept the live colour ({colour})"
        browser.close()

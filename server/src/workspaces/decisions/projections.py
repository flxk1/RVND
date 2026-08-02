# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision projections — reference cards for chat hosts, and the conformance
profile any projection must pass before it may carry a decision.

A projection renders a pending decision in a host's native card format (Teams
Adaptive Card, Slack Block Kit). Unlike the notification — which never carries
content — a decidable card must show the question and option labels, and those
land on the platform's servers. That is a deliberate, DECLARED egress: the
builder passes the Lock with exactly those fields in scope, and a folder whose
policy refuses gets no card — the deep-link-only notification remains. The
card's submit data carries the folder context so the response endpoint can
find the queue; the deep-link flow leaks no path — choosing the card flow is
choosing that trade, recorded.

The conformance profile holds a projection to the workbench's invariants:
options in server order, nothing pre-selected, no recommendation injected, a
required free-text rationale. A non-conformant projection is refused with the
failing clause named. Internal by design: consumed by the outbox, the response
endpoint and the verifier, not an operator surface of its own.
"""
from __future__ import annotations

import json
from typing import Any, Optional

CARD_FIELDS = ["query", "esc_reason", "decision_id", "folder_context",
               "options", "link_token"]


def _gate_card(folder: str, payload: dict) -> dict[str, Any]:
    from ..mcp_impl import lock_egress_check
    gate = lock_egress_check(tool="decision-card", arguments=payload,
                             task_scope=list(CARD_FIELDS),
                             folder_context=str(folder))
    return gate


def card_payload(folder: str, entry: dict, link_token: str) -> dict[str, Any]:
    """The gated content a card may carry: question, escalation reason and
    option id/label/conclusion — never grounds, consequences or rationale
    text. Refused whole when the Lock refuses; never silently trimmed."""
    surface = entry.get("surface", {})
    payload = {"query": surface.get("query", ""),
               "esc_reason": surface.get("esc_reason", ""),
               "decision_id": entry.get("decision_id", ""),
               "folder_context": str(folder),
               "link_token": link_token,
               "options": [{"id": o.get("id"), "label": o.get("label"),
                            "conclusion": o.get("conclusion")}
                           for o in surface.get("options", [])]}
    gate = _gate_card(folder, payload)
    if gate.get("action") != "allow":
        return {"ok": False, "error": "the Lock refused the card egress —"
                                      " the deep-link notification remains"
                                      " the only projection: "
                                      + str(gate.get("reason", ""))}
    return {"ok": True, "payload": payload}


# ---------------------------------------------------------------------------
# Reference builders — one card per host format, built ONLY from a gated
# payload. Option order is the payload order; nothing is pre-selected; no
# option carries emphasis; the rationale input is free text and required.
# ---------------------------------------------------------------------------
def teams_card(payload: dict) -> dict[str, Any]:
    body: list[dict] = [
        {"type": "TextBlock", "text": payload["query"], "wrap": True,
         "size": "Medium", "weight": "Bolder"},
    ]
    if payload.get("esc_reason"):
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True,
                     "text": "here because: " + payload["esc_reason"]})
    body.append({"type": "Input.ChoiceSet", "id": "chosen_option_id",
                 "style": "expanded", "isRequired": True,
                 "choices": [{"title": f"{o['label']} — {o['conclusion']}",
                              "value": o["id"]}
                             for o in payload["options"]]})
    body.append({"type": "Input.Text", "id": "rationale", "isMultiline": True,
                 "isRequired": True,
                 "placeholder": "your rationale — recorded with the decision"})
    return {"type": "AdaptiveCard", "version": "1.5", "body": body,
            "actions": [{"type": "Action.Submit",
                         "title": "Record the decision — signed",
                         "data": {"decision_id": payload["decision_id"],
                                  "folder_context": payload["folder_context"],
                                  "link_token": payload["link_token"]}}]}


def slack_blocks(payload: dict) -> dict[str, Any]:
    blocks: list[dict] = [
        {"type": "section",
         "text": {"type": "mrkdwn", "text": "*" + payload["query"] + "*"}},
    ]
    if payload.get("esc_reason"):
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "here because: " + payload["esc_reason"]}]})
    blocks.append({"type": "input", "block_id": "choice",
                   "label": {"type": "plain_text", "text": "The defensible options"},
                   "element": {"type": "radio_buttons",
                               "action_id": "chosen_option_id",
                               "options": [{"text": {"type": "plain_text",
                                                     "text": f"{o['label']} — {o['conclusion']}"},
                                            "value": o["id"]}
                                           for o in payload["options"]]}})
    blocks.append({"type": "input", "block_id": "rationale",
                   "label": {"type": "plain_text",
                             "text": "Your rationale — recorded with the decision"},
                   "element": {"type": "plain_text_input",
                               "action_id": "rationale", "multiline": True}})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "action_id": "record",
         "text": {"type": "plain_text", "text": "Record the decision — signed"},
         "value": json.dumps({"decision_id": payload["decision_id"],
                              "folder_context": payload["folder_context"],
                              "link_token": payload["link_token"]})}]})
    return {"blocks": blocks}


# ---------------------------------------------------------------------------
# Conformance profile — a projection is refused with the failing clause named.
# ---------------------------------------------------------------------------
def _extract(card: dict) -> dict[str, Any]:
    """Normalise either card format into {order, preselected, emphasised,
    free_rationale_required}."""
    if card.get("type") == "AdaptiveCard":
        choice = next((b for b in card.get("body", [])
                       if b.get("type") == "Input.ChoiceSet"), {})
        rat = next((b for b in card.get("body", [])
                    if b.get("type") == "Input.Text"), {})
        return {"order": [c.get("value") for c in choice.get("choices", [])],
                "preselected": choice.get("value") or None,
                "emphasised": [a.get("title") for a in card.get("actions", [])
                               if a.get("style") == "positive"],
                "free_rationale_required": bool(rat.get("isRequired"))
                and bool(rat.get("isMultiline"))}
    radio = next((b.get("element", {}) for b in card.get("blocks", [])
                  if b.get("type") == "input"
                  and b.get("element", {}).get("type") == "radio_buttons"), {})
    rat = next((b.get("element", {}) for b in card.get("blocks", [])
                if b.get("type") == "input"
                and b.get("element", {}).get("type") == "plain_text_input"), {})
    return {"order": [o.get("value") for o in radio.get("options", [])],
            "preselected": (radio.get("initial_option") or {}).get("value"),
            "emphasised": [e.get("action_id") for b in card.get("blocks", [])
                           if b.get("type") == "actions"
                           for e in b.get("elements", [])
                           if e.get("style") == "primary"],
            "free_rationale_required": bool(rat)}


def verify_projection(surface: dict, card: dict) -> dict[str, Any]:
    """The decision-projection profile: PASS, or the failing clauses named."""
    got = _extract(card)
    want = [o.get("id") for o in surface.get("options", [])]
    failures: list[str] = []
    if got["order"] != want:
        failures.append(f"server-order: options must render as served"
                        f" ({want}), got {got['order']}")
    if got["preselected"]:
        failures.append(f"no-preselection: option {got['preselected']!r} is"
                        f" pre-selected")
    if got["emphasised"]:
        failures.append(f"no-recommendation: emphasis on {got['emphasised']}"
                        f" reads as a recommendation")
    if not got["free_rationale_required"]:
        failures.append("rationale-required: the card must require free-text"
                        " rationale (origination, not a button)")
    return {"ok": not failures, "failures": failures}


# ---------------------------------------------------------------------------
# Response unwrapping — a platform post-back becomes the normalised response.
# ---------------------------------------------------------------------------
def unwrap_response(content_type: str, body: bytes) -> Optional[dict[str, Any]]:
    """{decision_id, folder_context, link_token, chosen_option_id, rationale,
    reconfirm_code?} from a normalised JSON POST, a Teams Action.Submit, or a
    Slack interactivity payload. None when the shape is unrecognisable."""
    try:
        if "application/x-www-form-urlencoded" in (content_type or ""):
            from urllib.parse import parse_qs
            payload = json.loads(parse_qs(body.decode())["payload"][0])
            state = payload.get("state", {}).get("values", {})
            out = dict(json.loads(payload["actions"][0]["value"]))
            out["chosen_option_id"] = (state.get("choice", {})
                                       .get("chosen_option_id", {})
                                       .get("selected_option", {}).get("value", ""))
            out["rationale"] = (state.get("rationale", {})
                                .get("rationale", {}).get("value", ""))
            return out
        data = json.loads(body or b"{}")
        if "data" in data and isinstance(data["data"], dict):     # Teams submit
            out = dict(data["data"])
            out.setdefault("chosen_option_id", data.get("chosen_option_id", ""))
            out.setdefault("rationale", data.get("rationale", ""))
            # Adaptive Card inputs arrive as siblings of data on most channels
            for k in ("chosen_option_id", "rationale", "reconfirm_code"):
                if not out.get(k) and data.get(k):
                    out[k] = data[k]
            return out
        if "link_token" in data:                                   # normalised
            return data
    except Exception:                                              # noqa: BLE001
        return None
    return None

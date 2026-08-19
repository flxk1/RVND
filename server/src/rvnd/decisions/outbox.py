# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision outbox — the minimised notification reaches each holder's channel.

For one pending decision: resolve the holders (competence via the party
roster; an unrestricted decision goes to every registered human except the
raiser), mint each holder their own single-use action link, and deliver
title + deep link — never the question, options or grounds — to their
registered channels (``email:`` / ``slack:`` / ``webhook:``). Every outgoing
message passes the Lock's egress gate first; every per-channel result is
recorded on the chain and kept on the entry, failures included — a
notification that silently vanished would defeat the routing.

Senders are injectable for tests; the defaults refuse honestly when their
transport is unconfigured (``WORKSPACE_SMTP_HOST`` etc.). The deep link is
``WORKSPACE_CONSOLE_URL`` when declared, else the ``rvnd://`` scheme.
Internal by design: consumed by decision_open and the notify op, not an
operator surface of its own.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable, Optional

Sender = Callable[[str, dict], dict]      # (address, message) -> {ok, detail}


def _deep_link(decision_id: str, token: str) -> str:
    base = (os.environ.get("WORKSPACE_CONSOLE_URL") or "").rstrip("/")
    if base:
        return f"{base}/?decision={decision_id}&token={token}"
    return f"rvnd://decisions/{decision_id}?token={token}"


def _send_email(address: str, message: dict) -> dict:
    host = os.environ.get("WORKSPACE_SMTP_HOST")
    if not host:
        return {"ok": False, "detail": "smtp not configured (WORKSPACE_SMTP_HOST)"}
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = message["title"]
    msg["From"] = os.environ.get("WORKSPACE_SMTP_FROM", "rvnd@localhost")
    msg["To"] = address
    msg.set_content(f"{message['title']}\n\n{message['deep_link']}\n")
    try:
        with smtplib.SMTP(host, int(os.environ.get("WORKSPACE_SMTP_PORT", "25")),
                          timeout=10) as s:
            user = os.environ.get("WORKSPACE_SMTP_USER")
            if user:
                s.starttls()
                s.login(user, os.environ.get("WORKSPACE_SMTP_PASSWORD", ""))
            s.send_message(msg)
        return {"ok": True, "detail": "sent"}
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def _post_json(url: str, body: dict) -> dict:
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"ok": 200 <= r.status < 300, "detail": f"http {r.status}"}
    except Exception as e:                                      # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def _send_slack(address: str, message: dict) -> dict:
    return _post_json(address, {"text": f"{message['title']} — {message['deep_link']}"})


def _send_webhook(address: str, message: dict) -> dict:
    return _post_json(address, message)


SENDERS: dict[str, Sender] = {
    "email": _send_email, "slack": _send_slack, "webhook": _send_webhook,
}


def _holders(folder: str, competence: str, raised_by: str, log_root) -> list[dict]:
    from ..parties import list_parties
    roster = list_parties(folder, kind="human", competence=competence or "",
                          log_root=str(log_root) if log_root else None)
    return [p for p in roster.get("parties", [])
            if p.get("party_id") != raised_by and p.get("channels")]


def notify(folder: str, decision_id: str, *,
           log_root=None, senders: Optional[dict[str, Sender]] = None,
           actor: str = "system") -> dict[str, Any]:
    """Deliver the decision's minimised notification + a personal action link
    to every holder's channels. Records every per-channel result. Refuses
    before sending anything when the Lock refuses the egress."""
    from .queue import DecisionQueue
    q = DecisionQueue(folder, log_root=log_root)
    entry = q.get(decision_id)
    if entry is None or entry.get("state") != "open":
        return {"ok": False, "error": f"no open decision {decision_id!r}"}
    gate = q.notification(entry)
    if gate.get("egress") != "permitted":
        q._log("notify_refused", decision_id, actor,
               {"detail": gate.get("egress_detail", "")})
        return {"ok": False, "error": "the Lock refused this egress — nothing"
                                      f" was sent: {gate.get('egress_detail', '')}",
                "sent": []}
    holders = _holders(folder, entry.get("competence", ""),
                       entry.get("raised_by", ""), log_root)
    table = senders or SENDERS
    results: list[dict] = []
    for holder in holders:
        minted = q.mint_link(decision_id, holder["party_id"], actor=actor)
        if not minted.get("ok"):
            results.append({"party_id": holder["party_id"], "channel": "",
                            "ok": False, "detail": minted.get("error", "")})
            continue
        message = {**gate["payload"],
                   "deep_link": _deep_link(decision_id, minted["token"])}
        for channel in holder.get("channels", []):
            kind, _, address = str(channel).partition(":")
            sender = table.get(kind)
            res = (sender(address, message) if sender
                   else {"ok": False, "detail": f"no sender for channel kind {kind!r}"})
            results.append({"party_id": holder["party_id"], "channel": kind,
                            "ok": bool(res.get("ok")),
                            "detail": str(res.get("detail", ""))})
    entry.setdefault("notifications", []).extend(results)
    entry["renotify_due"] = False
    q._flush()
    q._log("notified", decision_id, actor,
           {"sent": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "holders": len(holders)})
    return {"ok": True, "decision_id": decision_id, "holders": len(holders),
            "sent": results}

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision routing — pending decision surfaces find their competent human.

A pending decision is one persisted entry per escalation (a JSONL store under
the folder, every transition a signed chain event). Routing is three recorded
steps: the competence comes from the escalation itself, never an org chart;
holders resolve through the PartyResolver roster; dispatch is claim-based —
the first claim leases the decision for a TTL, a second claim is refused while
the lease holds, expiry releases it back to every holder. The actor who raised
the escalation can never be its decider (separation of duties). The
notification a transport may carry is minimised — a generic title, the
decision id and a deep link, never the question or the options — and must pass
the Lock's egress gate before it leaves the boundary.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..mutation_log import LogEvent, MutationLog

DEFAULT_CLAIM_TTL_S = 4 * 3600            # a working half-day, then it widens again


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _path(folder: str | Path) -> Path:
    return Path(folder) / "decisions" / "pending.jsonl"


class DecisionQueue:
    """Persisted, audited pending-decision store for one folder."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        from ..folder_context import resolve_folder_context

        self.folder = Path(resolve_folder_context(folder))
        self.log_root = Path(log_root) if log_root else None
        self.items: dict[str, dict] = {}
        p = _path(self.folder)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.items[r["decision_id"]] = r

    def _flush(self) -> None:
        p = _path(self.folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in self.items.values())
                     + ("\n" if self.items else ""), encoding="utf-8")

    def _log(self, op: str, decision_id: str, actor: str, extra: dict) -> Optional[str]:
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="system", folder_path=str(self.folder),
                pair_id=f"decision-queue:{decision_id}", channel="system",
                actor=actor or "system",
                extra={"kind": "decision." + op, "decision_id": decision_id, **extra}))
        except Exception:                                       # noqa: BLE001
            return None

    # ── lifecycle ─────────────────────────────────────────────────────────────
    RAISER_OPEN_CAP = 25          # open decisions one raiser may hold pending

    PRIORITIES = ("", "low", "normal", "high", "urgent")

    def open(self, surface: dict, *, raised_by: str, competence: str = "",
             claim_ttl_s: int = DEFAULT_CLAIM_TTL_S,
             escalate_to: str = "", escalate_after_s: int = 0,
             write_reconfirm: bool = False,
             idempotency_key: str = "", priority: str = "",
             decide_by: str = "", panel: Optional[dict] = None) -> dict[str, Any]:
        """Persist an escalation as a pending decision. The assignment basis is
        recorded with the entry: the competence and where it came from. An
        idempotency key makes re-raising safe (same key → the same decision,
        never a duplicate); the per-raiser cap stops a looping flow from
        flooding the humans — refused in words, recorded."""
        if not (surface or {}).get("options"):
            return {"ok": False, "error": "a pending decision needs a surface with options"}
        if not (raised_by or "").strip():
            return {"ok": False, "error": "the raising actor must be named —"
                                          " separation of duties needs it"}
        if (priority or "") not in self.PRIORITIES:
            return {"ok": False, "error": f"priority must be one of"
                                          f" {self.PRIORITIES[1:]}"}
        if panel is not None:
            seats = int(panel.get("seats", 0))
            rule = str(panel.get("rule", ""))
            m = int(panel.get("m", 0))
            if seats < 2:
                return {"ok": False, "error": "a panel needs at least 2 seats"}
            if rule not in ("unanimous", "m_concordant", "any_m"):
                return {"ok": False, "error": "panel rule must be unanimous,"
                                              " m_concordant or any_m"}
            if rule != "unanimous" and not (2 <= m <= seats):
                return {"ok": False, "error": f"rule {rule!r} needs m between"
                                              f" 2 and seats ({seats})"}
            panel = {"seats": seats, "rule": rule,
                     "m": seats if rule == "unanimous" else m,
                     "seat_claims": {}, "seat_records": []}
        key = (idempotency_key or "").strip()
        if key:
            existing = next((e for e in self.items.values()
                             if e.get("idempotency_key") == key), None)
            if existing is not None:
                return {"ok": True, "decision_id": existing["decision_id"],
                        "deduplicated": True, "state": existing["state"],
                        "notification": self.notification(existing)}
        open_by_raiser = sum(1 for e in self.items.values()
                             if e.get("state") == "open"
                             and e.get("raised_by") == raised_by.strip())
        if open_by_raiser >= self.RAISER_OPEN_CAP:
            self._log("open_refused", "", raised_by,
                      {"reason": "raiser cap", "open": open_by_raiser})
            return {"ok": False, "error": f"{raised_by.strip()!r} already holds"
                                          f" {open_by_raiser} open decisions —"
                                          " the flood guard refuses more until"
                                          " some are decided (a looping flow,"
                                          " not more human attention, is the"
                                          " likely cause)"}
        did = "dec-" + uuid.uuid4().hex[:10]
        entry = {"decision_id": did, "surface": dict(surface),
                 "competence": (competence or "").strip(),
                 "assignment_basis": ("competence " + competence.strip()
                                      if (competence or "").strip()
                                      else "unrestricted — any registered person"),
                 "raised_by": raised_by.strip(), "opened_at": _iso(_now()),
                 "claim_ttl_s": int(claim_ttl_s),
                 "escalate_to": (escalate_to or "").strip(),
                 "escalate_after_s": int(escalate_after_s),
                 "escalated_at": None,
                 "write_reconfirm": bool(write_reconfirm),
                 "idempotency_key": key,
                 "priority": (priority or "").strip(),
                 "decide_by": (decide_by or "").strip(),
                 "panel": panel,
                 "claimed_by": None, "claim_expires_at": None, "state": "open"}
        self.items[did] = entry
        self._flush()
        audit_id = self._log("opened", did, raised_by,
                             {"competence": entry["competence"],
                              "assignment_basis": entry["assignment_basis"]})
        return {"ok": True, "decision_id": did, "audit_id": audit_id,
                "notification": self.notification(entry)}

    @staticmethod
    def _on_elapse(entry: dict) -> str:
        """The declared direction when ``decide_by`` elapses — a deadline is
        never shown bare: the ladder widens the competence, or the decision
        stays with its holders and reads overdue."""
        if not entry.get("decide_by"):
            return ""
        if entry.get("escalate_to"):
            return f"widens to {entry['escalate_to']}"
        return "stays with its holders, flagged overdue"

    @staticmethod
    def panel_state(entry: dict) -> Optional[dict]:
        """Co-decision state WITHOUT content: seats, how many recorded, the
        rule. Seat choices and rationales stay sealed until resolution."""
        panel = entry.get("panel")
        if not panel:
            return None
        return {"seats": panel["seats"], "rule": panel["rule"],
                "m": panel["m"], "recorded": len(panel["seat_records"]),
                "recorded_by": sorted(r["party_id"]
                                      for r in panel["seat_records"])}

    def notification(self, entry: dict) -> dict[str, Any]:
        """The ONLY payload a transport may carry outward: generic title, id,
        deep link — no question, no options, no grounds. The Lock's egress gate
        rules on it; a refusal travels instead of the payload."""
        payload = {"title": "A decision waits",
                   "decision_id": entry["decision_id"],
                   "deep_link": f"rvnd://decisions/{entry['decision_id']}"}
        try:
            from ..mcp_impl import lock_egress_check
            # task_scope declares the exact fields this egress needs — the
            # gate strips anything beyond it, so a payload that ever grows a
            # content field is caught here, not at the transport
            gate = lock_egress_check(tool="decision-notification",
                                     arguments=payload,
                                     task_scope=["title", "decision_id",
                                                 "deep_link"],
                                     folder_context=str(self.folder))
            allowed = gate.get("action") == "allow"
            return {"payload": payload if allowed else None,
                    "egress": "permitted" if allowed else "refused",
                    "egress_detail": gate.get("reason", "")}
        except Exception as e:                                  # noqa: BLE001
            return {"payload": None, "egress": "refused",
                    "egress_detail": f"gate unavailable — fail closed: {e}"}

    def _maybe_escalate(self, entry: dict) -> None:
        if (entry.get("state") != "open" or entry.get("claimed_by")
                or entry.get("escalated_at") or not entry.get("escalate_to")):
            return
        from datetime import timedelta as _td
        deadline_passed = bool(entry.get("decide_by")) and \
            entry["decide_by"] <= _iso(_now())
        window_passed = False
        if entry.get("escalate_after_s"):
            try:
                opened = datetime.fromisoformat(entry.get("opened_at", ""))
                window_passed = _now() >= opened + _td(
                    seconds=int(entry["escalate_after_s"]))
            except ValueError:
                window_passed = False
        if not (deadline_passed or window_passed):
            return
        was = entry.get("competence", "")
        entry["competence"] = entry["escalate_to"]
        entry["escalated_at"] = _iso(_now())
        entry["assignment_basis"] = (f"competence {entry['escalate_to']}"
                                     f" — escalated from"
                                     f" {was or 'unrestricted'} after no claim")
        entry["renotify_due"] = True
        self._flush()
        self._log("escalated", entry["decision_id"], "system",
                  {"from": was, "to": entry["escalate_to"]})

    def _release_if_expired(self, entry: dict) -> None:
        exp = entry.get("claim_expires_at")
        if entry.get("claimed_by") and exp and exp <= _iso(_now()):
            self._log("claim_expired", entry["decision_id"], "system",
                      {"was_claimed_by": entry["claimed_by"]})
            entry["claimed_by"] = None
            entry["claim_expires_at"] = None
            self._flush()

    def pending(self, *, for_party: str = "") -> dict[str, Any]:
        """Open decisions, each with its routing state. ``for_party`` narrows
        to what that party may claim: competence held (via the resolver's
        roster) and not raised by them."""
        holders_cache: dict[str, set] = {}

        def may_claim(entry: dict, party_id: str) -> bool:
            if entry["raised_by"] == party_id:
                return False                       # separation of duties
            comp = entry.get("competence") or ""
            if not comp:
                return True
            if comp not in holders_cache:
                from ..parties import list_parties
                roster = list_parties(str(self.folder), competence=comp,
                                      log_root=str(self.log_root) if self.log_root else None)
                holders_cache[comp] = {p.get("party_id")
                                       for p in roster.get("parties", [])}
            return party_id in holders_cache[comp]

        out = []
        for entry in self.items.values():
            if entry.get("state") != "open":
                continue
            self._release_if_expired(entry)
            self._maybe_escalate(entry)
            if for_party and not may_claim(entry, for_party):
                continue
            out.append({k: entry[k] for k in
                        ("decision_id", "competence", "assignment_basis",
                         "raised_by", "opened_at", "claimed_by",
                         "claim_expires_at")}
                       | {"query": entry["surface"].get("query", ""),
                          "option_count": len(entry["surface"].get("options", [])),
                          "notified_ok": sum(1 for n in entry.get("notifications", []) if n.get("ok")),
                          "renotify_due": bool(entry.get("renotify_due")),
                          "priority": entry.get("priority", ""),
                          "decide_by": entry.get("decide_by", ""),
                          "overdue": bool(entry.get("decide_by"))
                          and entry["decide_by"] <= _iso(_now()),
                          "panel": self.panel_state(entry)})
        rank = {"urgent": 0, "high": 1, "normal": 2, "": 2, "low": 3}
        out.sort(key=lambda r: (rank.get(r["priority"], 2), r["opened_at"]))
        return {"ok": True, "pending": out}

    def get(self, decision_id: str) -> Optional[dict]:
        entry = self.items.get(decision_id)
        if entry:
            self._release_if_expired(entry)
            self._maybe_escalate(entry)
        return entry

    def claim(self, decision_id: str, actor: str,
              auth_rung: str = "") -> dict[str, Any]:
        """First claim leases the decision; a live lease refuses a second
        claimant by name. The raiser cannot claim their own escalation.
        ``auth_rung`` records how the claimant was authenticated."""
        entry = self.get(decision_id)
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": f"no open decision {decision_id!r}"}
        if not (actor or "").strip():
            return {"ok": False, "error": "a claim must name its actor"}
        if actor == entry["raised_by"]:
            return {"ok": False, "error": "separation of duties: the actor who"
                                          " raised this escalation cannot decide it"}
        panel = entry.get("panel")
        if panel:
            actor = actor.strip()
            if any(r["party_id"] == actor for r in panel["seat_records"]):
                return {"ok": False, "error": "this seat already recorded —"
                                              " a panel seat decides once"}
            now = _iso(_now())
            live = {p_ for p_, exp in panel["seat_claims"].items()
                    if exp > now and p_ != actor}
            taken = live | {r["party_id"] for r in panel["seat_records"]}
            if actor not in panel["seat_claims"] and \
                    len(taken) >= panel["seats"]:
                return {"ok": False, "error": f"all {panel['seats']} seats are"
                                              " taken — leases or records hold"
                                              " them"}
            panel["seat_claims"][actor] = _iso(
                _now() + timedelta(seconds=entry["claim_ttl_s"]))
            self._flush()
            audit_id = self._log("seat_claimed", decision_id, actor,
                                 {"claim_expires_at": panel["seat_claims"][actor],
                                  **({"auth_rung": auth_rung} if auth_rung else {})})
            return {"ok": True, "decision_id": decision_id,
                    "claimed_by": actor, "seat": True,
                    "claim_expires_at": panel["seat_claims"][actor],
                    "surface": entry["surface"],
                    "decide_by": entry.get("decide_by", ""),
                    "on_elapse": self._on_elapse(entry),
                    "panel": self.panel_state(entry), "audit_id": audit_id}
        if entry.get("claimed_by") and entry["claimed_by"] != actor:
            return {"ok": False, "error": f"already claimed by"
                                          f" {entry['claimed_by']!r} — the lease"
                                          f" holds until {entry['claim_expires_at']}"}
        entry["claimed_by"] = actor.strip()
        entry["claim_expires_at"] = _iso(_now() + timedelta(seconds=entry["claim_ttl_s"]))
        self._flush()
        self._invalidate_foreign_links(entry, actor.strip())
        audit_id = self._log("claimed", decision_id, actor,
                             {"claim_expires_at": entry["claim_expires_at"],
                              **({"auth_rung": auth_rung} if auth_rung else {})})
        return {"ok": True, "decision_id": decision_id,
                "claimed_by": entry["claimed_by"],
                "claim_expires_at": entry["claim_expires_at"],
                "surface": entry["surface"],
                "decide_by": entry.get("decide_by", ""),
                "on_elapse": self._on_elapse(entry),
                "audit_id": audit_id}

    def release(self, decision_id: str, actor: str) -> dict[str, Any]:
        entry = self.get(decision_id)
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": f"no open decision {decision_id!r}"}
        if entry.get("claimed_by") != actor:
            return {"ok": False, "error": "only the claimant may release"}
        entry["claimed_by"] = None
        entry["claim_expires_at"] = None
        self._flush()
        audit_id = self._log("released", decision_id, actor, {})
        return {"ok": True, "decision_id": decision_id, "audit_id": audit_id}

    # ── co-decision panel: seats record independently and SEALED — a seat
    # never sees another seat's choice or rationale before resolution. The
    # rules: unanimous = every seat, one voice, any difference splits early;
    # m_concordant = met the moment any option holds m concordant records,
    # split only when no option can still reach m; any_m = a quorum — the
    # first m records must agree, discord at quorum splits. A split never
    # averages: it escalates up the declared ladder with the sealed records
    # attached, or re-opens the panel where no ladder is declared.
    def record_seat(self, decision_id: str, party_id: str,
                    chosen_option_id: str, rationale: str,
                    auth_rung: str = "") -> dict[str, Any]:
        import hashlib
        entry = self.get(decision_id)
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": f"no open decision {decision_id!r}"}
        panel = entry.get("panel")
        if not panel:
            return {"ok": False, "error": "this decision has no panel — record"
                                          " it singly"}
        party_id = (party_id or "").strip()
        if not party_id:
            return {"ok": False, "error": "a seat record must name its actor"}
        if party_id == entry["raised_by"]:
            return {"ok": False, "error": "separation of duties: the actor who"
                                          " raised this escalation cannot"
                                          " hold a seat"}
        if not (rationale or "").strip():
            return {"ok": False, "error": "a seat records origination — the"
                                          " rationale cannot be empty"}
        if chosen_option_id not in {o.get("id")
                                    for o in entry["surface"].get("options", [])}:
            return {"ok": False, "error": f"option {chosen_option_id!r} is not"
                                          " on the surface"}
        if any(r["party_id"] == party_id for r in panel["seat_records"]):
            return {"ok": False, "error": "this seat already recorded — a"
                                          " panel seat decides once"}
        now = _iso(_now())
        live = {p_ for p_, exp in panel["seat_claims"].items()
                if exp > now and p_ != party_id}
        taken = live | {r["party_id"] for r in panel["seat_records"]}
        if party_id not in panel["seat_claims"] and \
                len(taken) >= panel["seats"]:
            return {"ok": False, "error": f"all {panel['seats']} seats are"
                                          " taken — leases or records hold"
                                          " them"}
        rec = {"party_id": party_id, "chosen_option_id": str(chosen_option_id),
               "rationale": str(rationale), "recorded_at": now,
               "auth_rung": auth_rung}
        rec["commitment"] = hashlib.sha256(json.dumps(
            rec, sort_keys=True).encode()).hexdigest()
        panel["seat_records"].append(rec)
        panel["seat_claims"].pop(party_id, None)
        self._flush()
        # the chain says WHO recorded (and the commitment), never yet WHAT
        self._log("seat_recorded", decision_id, party_id,
                  {"commitment": rec["commitment"],
                   **({"auth_rung": auth_rung} if auth_rung else {})})
        return self._resolve_panel(entry)

    def _resolve_panel(self, entry: dict) -> dict[str, Any]:
        panel = entry["panel"]
        records, m = panel["seat_records"], panel["m"]
        counts: dict[str, int] = {}
        for r in records:
            counts[r["chosen_option_id"]] = counts.get(r["chosen_option_id"], 0) + 1
        top = max(counts.values()) if counts else 0
        met = top >= m
        remaining = panel["seats"] - len(records)
        if panel["rule"] == "any_m":
            splits = len(records) >= m and not met
        else:
            splits = all(c + remaining < m for c in counts.values()) if counts else False
        did = entry["decision_id"]
        state = self.panel_state(entry)
        if met:
            winner = next(o for o, c in counts.items() if c == top)
            seat_ids = [self._log("seat_choice", did, r["party_id"],
                                  {"chosen_option_id": r["chosen_option_id"],
                                   "rationale": r["rationale"],
                                   "commitment": r["commitment"],
                                   **({"auth_rung": r["auth_rung"]}
                                      if r["auth_rung"] else {})})
                        for r in records]
            audit_id = self._log("panel_resolved", did, "panel",
                                 {"rule": panel["rule"], "m": m,
                                  "chosen_option_id": winner,
                                  "counts": counts, "seat_audit_ids": seat_ids})
            return {"ok": True, "resolved": True, "chosen_option_id": winner,
                    "decision_id": did, "panel": state,
                    "seat_audit_ids": seat_ids, "audit_id": audit_id}
        if splits:
            audit_id = self._log("panel_split", did, "panel",
                                 {"rule": panel["rule"], "m": m,
                                  "counts": counts,
                                  "seat_records": records})
            entry.setdefault("panel_history", []).append(
                {"records": records, "counts": counts, "split_at": _iso(_now())})
            panel["seat_records"] = []
            panel["seat_claims"] = {}
            entry["renotify_due"] = True
            escalated = False
            if entry.get("escalate_to") and not entry.get("escalated_at"):
                was = entry.get("competence", "")
                entry["competence"] = entry["escalate_to"]
                entry["escalated_at"] = _iso(_now())
                entry["assignment_basis"] = (
                    f"competence {entry['escalate_to']} — escalated from"
                    f" {was or 'unrestricted'} after a split panel")
                escalated = True
                self._log("escalated", did, "system",
                          {"from": was, "to": entry["escalate_to"],
                           "reason": "panel split"})
            self._flush()
            return {"ok": True, "resolved": False, "split": True,
                    "decision_id": did, "escalated": escalated,
                    "panel": self.panel_state(entry), "audit_id": audit_id,
                    "error": None,
                    "detail": "the panel split — no averaging; the decision"
                              + (" escalated up the declared ladder"
                                 if escalated else
                                 " re-opened for a fresh panel")
                              + " with all seat records attached to the chain"}
        self._flush()
        return {"ok": True, "resolved": False, "sealed": True,
                "decision_id": did, "panel": state}

    def close(self, decision_id: str, actor: str, *, choice_audit_id: str = "") -> None:
        entry = self.items.get(decision_id)
        if entry is None:
            return
        entry["state"] = "decided"
        entry["decided_by"] = actor
        entry["decided_at"] = _iso(_now())
        self._flush()
        self._log("decided", decision_id, actor, {"choice_audit_id": choice_audit_id})

    # ── action links: the registered channel is the credential ───────────────
    # A link token authenticates its holder as one party for one decision. It
    # is signed with the workspace keypair, single-use, short-lived, and dies
    # when a competing claim takes the card. Only the token's hash is stored;
    # the token itself exists once, in the message sent to the holder.
    def mint_link(self, decision_id: str, party_id: str, *,
                  ttl_s: int = 24 * 3600, actor: str = "system") -> dict[str, Any]:
        import hashlib
        from ..signing import sign_bytes
        entry = self.get(decision_id)
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": f"no open decision {decision_id!r}"}
        if not (party_id or "").strip():
            return {"ok": False, "error": "a link binds to a named party"}
        if party_id == entry.get("raised_by"):
            return {"ok": False, "error": "separation of duties: no link for"
                                          " the actor who raised the escalation"}
        expires = _iso(_now() + timedelta(seconds=int(ttl_s)))
        body = json.dumps({"d": decision_id, "p": party_id, "e": expires,
                           "n": uuid.uuid4().hex[:8]}, sort_keys=True)
        token = (body.encode().hex() + "." + sign_bytes(body.encode()))
        entry.setdefault("links", []).append(
            {"hash": hashlib.sha256(token.encode()).hexdigest(),
             "party_id": party_id, "expires_at": expires, "used_at": None,
             "invalidated": None})
        self._flush()
        audit_id = self._log("link_minted", decision_id, actor,
                             {"party_id": party_id, "expires_at": expires})
        return {"ok": True, "decision_id": decision_id, "party_id": party_id,
                "token": token, "expires_at": expires, "audit_id": audit_id}

    def verify_link(self, token: str, *, consume: bool = False) -> dict[str, Any]:
        """Resolve a link token to (party, decision) or refuse in words. With
        ``consume`` the token is spent — reads may verify repeatedly within
        the TTL; the write consumes."""
        import hashlib
        from ..signing import verify_signature
        try:
            body_hex, sig = (token or "").split(".", 1)
            body = bytes.fromhex(body_hex)
        except ValueError:
            return {"ok": False, "error": "malformed link token"}
        if not verify_signature(body, sig):
            return {"ok": False, "error": "link token signature does not verify"}
        claims = json.loads(body)
        entry = self.get(claims.get("d", ""))
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": "this link's decision is no longer open"}
        h = hashlib.sha256(token.encode()).hexdigest()
        rec = next((r for r in entry.get("links", []) if r["hash"] == h), None)
        if rec is None:
            return {"ok": False, "error": "unknown link token (revoked or never minted)"}
        if rec.get("used_at"):
            return {"ok": False, "error": "this link was already used — links are single-use"}
        if rec.get("invalidated"):
            return {"ok": False, "error": f"this link no longer works: {rec['invalidated']}"}
        if rec.get("expires_at", "") <= _iso(_now()):
            return {"ok": False, "error": "this link has expired — ask for a fresh one"}
        if consume:
            rec["used_at"] = _iso(_now())
            self._flush()
        return {"ok": True, "party_id": rec["party_id"],
                "decision_id": entry["decision_id"],
                "auth_rung": "channel-link"}

    # ── write re-confirm: a fresh short code to the same channel guards the
    # one governed write when the decision was opened with write_reconfirm —
    # a forwarded link then leaks a view, never a decision. Six digits are a
    # credential only while guessing is bounded: one code per party is live
    # at a time, wrong guesses land on the chain, and at the miss cap the
    # live code voids — only a fresh mint (to the holder's own channel) helps.
    RECONFIRM_MISS_CAP = 5        # wrong guesses before the live code voids

    def mint_reconfirm(self, decision_id: str, party_id: str, *,
                       ttl_s: int = 900) -> dict[str, Any]:
        import hashlib
        import secrets
        entry = self.get(decision_id)
        if entry is None or entry.get("state") != "open":
            return {"ok": False, "error": f"no open decision {decision_id!r}"}
        code = f"{secrets.randbelow(1_000_000):06d}"
        recs = entry.setdefault("reconfirms", [])
        for r in recs:                        # one live code per party
            if r["party_id"] == party_id and not r.get("used_at"):
                r["used_at"] = _iso(_now())
                r["superseded"] = True
        recs.append(
            {"hash": hashlib.sha256((party_id + ":" + code).encode()).hexdigest(),
             "party_id": party_id,
             "expires_at": _iso(_now() + timedelta(seconds=int(ttl_s))),
             "used_at": None, "misses": 0})
        self._flush()
        self._log("reconfirm_minted", decision_id, party_id, {})
        return {"ok": True, "code": code, "party_id": party_id,
                "decision_id": decision_id}

    def verify_reconfirm(self, decision_id: str, party_id: str, code: str, *,
                         consume: bool = True) -> dict[str, Any]:
        import hashlib
        entry = self.get(decision_id)
        if entry is None:
            return {"ok": False, "error": f"no decision {decision_id!r}"}
        h = hashlib.sha256((party_id + ":" + (code or "")).encode()).hexdigest()
        recs = [r for r in entry.get("reconfirms", [])
                if r["party_id"] == party_id]
        rec = (next((r for r in recs
                     if r["hash"] == h and not r.get("used_at")), None)
               or next((r for r in recs if r["hash"] == h), None))
        if rec is None:
            live = next((r for r in recs if not r.get("used_at")), None)
            if live is not None:
                live["misses"] = int(live.get("misses", 0)) + 1
                voided = live["misses"] >= self.RECONFIRM_MISS_CAP
                if voided:
                    live["used_at"] = _iso(_now())
                    live["voided"] = True
                self._flush()
                self._log("reconfirm_failed", decision_id, party_id,
                          {"misses": live["misses"], "voided": voided})
                if voided:
                    return {"ok": False,
                            "error": "the confirmation code does not match —"
                                     " too many wrong guesses voided it; ask"
                                     " for a fresh one"}
            else:
                self._log("reconfirm_failed", decision_id, party_id, {})
            return {"ok": False, "error": "the confirmation code does not match"}
        if rec.get("voided"):
            return {"ok": False, "error": "this confirmation code was voided"
                                          " after too many wrong guesses — ask"
                                          " for a fresh one"}
        if rec.get("superseded"):
            return {"ok": False, "error": "a newer confirmation code replaced"
                                          " this one — use the latest"}
        if rec.get("used_at"):
            return {"ok": False, "error": "this confirmation code was already used"}
        if rec.get("expires_at", "") <= _iso(_now()):
            return {"ok": False, "error": "the confirmation code expired — ask for a fresh one"}
        if consume:
            rec["used_at"] = _iso(_now())
            self._flush()
        return {"ok": True}

    def _invalidate_foreign_links(self, entry: dict, claimant: str) -> None:
        changed = False
        for rec in entry.get("links", []):
            if rec["party_id"] != claimant and not rec.get("used_at") and not rec.get("invalidated"):
                rec["invalidated"] = f"claimed by {claimant}"
                changed = True
        if changed:
            self._flush()

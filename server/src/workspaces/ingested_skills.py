# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""User-ingested skill objects — the universal-skill-adapter store.

A skill that did not ship in the Workspace plugin catalogue (a user paste, a
prose description, an imported Anthropic/Cursor/Cline rule) cannot ride the
catalogue path: ``dispatch_skill`` returns ``body=None`` for anything the
sandbox can't read. This module gives such a skill a **body-bearing,
content-addressed, signed object that lives inside the workspace folder**,
so its body is always readable and its ownership always provable.

Layout (inside the workspace folder so it exports with the folder)::

    <workspace>/.workspace/skills/<uid>/
        skill.md          canonical body (Anthropic SKILL.md shape)
        manifest.json     adapter-normalised metadata + ownership block
        signature.json    Ed25519 detached signature over skill.md+manifest

``uid = sha256(normalised skill.md)[:16]`` — content-addressed, so identical
bodies dedupe and any edit yields a new uid (tamper-evident by construction).

Ownership (the four locked dimensions, 2026-05-31):
  1. Provenance — Ed25519 signature; controller key is the root of trust,
     operator key the fallback. Fingerprint recorded in the manifest.
  2. Local-first — the object lives in the user's own folder. Export = copy
     the folder; re-import verifies the signature on the new machine.
  3. Monetization — license + monetization.model carried in the manifest;
     dispatch emits a usage event so the audit chain doubles as a ledger.
  4. Curation — pinning (pinned_skills) + the asymmetric resolver govern who
     sees the skill; this module only owns the body + ownership.

Pinning still happens through ``pinned_skills.pin_skill`` (the routing
label); this module owns the body store the pin points at.

No cloud calls. The prose adapter has a deterministic fallback and a hook
for a local model (``draft_skill_from_prose``) so tests stay hermetic.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


SKILLS_SUBDIR = (".workspace", "skills")
MANIFEST_FILE = "manifest.json"
BODY_FILE = "skill.md"
SIGNATURE_FILE = "signature.json"
MANIFEST_VERSION = 1
DESCRIPTION_MAX = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(text: str) -> str:
    """Lowercase kebab slug, safe for a skill_id segment and a path."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower())
    return s.strip("-") or "skill"


def _parse_version(v: str) -> tuple:
    """Best-effort semver tuple for ordering. Non-numeric parts compare as
    strings after the numeric prefix. ``'1.2.0' < '1.10.0'`` holds."""
    out: list = []
    for part in str(v or "0").split("."):
        m = re.match(r"(\d+)(.*)", part.strip())
        if m:
            out.append((int(m.group(1)), m.group(2)))
        else:
            out.append((0, part.strip()))
    return tuple(out)


def _normalise_body(text: str) -> str:
    """Canonical form for content-addressing: LF line endings, trailing
    whitespace stripped per line, single trailing newline."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(ln.rstrip() for ln in lines).strip("\n") + "\n"


def content_uid(body: str) -> str:
    """16-hex content address of the normalised body."""
    return hashlib.sha256(_normalise_body(body).encode("utf-8")).hexdigest()[:16]


def _skills_root(folder_path: str | Path) -> Path:
    return Path(folder_path).expanduser().resolve().joinpath(*SKILLS_SUBDIR)


def _object_dir(folder_path: str | Path, uid: str) -> Path:
    return _skills_root(folder_path) / uid


# ---------------------------------------------------------------------------
# Frontmatter + validation (the five install-floor failure modes)
# ---------------------------------------------------------------------------


def parse_frontmatter(body: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into (frontmatter_dict, body_after_frontmatter).

    Minimal single-line ``key: value`` parser — intentionally NOT a full
    YAML load, because the validation rules below forbid the constructs a
    full loader would mask (folded scalars). Returns ({}, body) when there
    is no frontmatter.
    """
    text = body.lstrip("﻿")
    if not text.startswith("---"):
        return {}, body
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, body
    fm_block, rest = parts[1], parts[2]
    fm: dict[str, str] = {}
    for raw in fm_block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        fm[key.strip()] = val.strip()
    return fm, rest.lstrip("\n")


def validate_skill(body: str) -> list[str]:
    """Return a list of failure strings. Empty list == valid.

    Enforces the five failure modes that broke prior installs
    (see [[skill_validation]]):
      1. description > 1024 chars
      2. description contains angle brackets
      3. folded scalar in frontmatter (``: |`` or ``: >``)
      4. body horizontal rule (``^---$`` after the frontmatter)
      5. description not single-line single-quoted (folded across lines)
    Plus structural: frontmatter present, name + description non-empty.
    """
    failures: list[str] = []
    text = body.lstrip("﻿")

    if not text.startswith("---"):
        failures.append("missing YAML frontmatter (must start with '---')")
        return failures
    parts = text.split("---", 2)
    if len(parts) < 3:
        failures.append("frontmatter not closed with a second '---'")
        return failures
    fm_block = parts[1]
    fm, after = parse_frontmatter(body)

    # Rule 3: folded scalars anywhere in frontmatter
    for raw in fm_block.splitlines():
        m = re.match(r"\s*[A-Za-z0-9_]+\s*:\s*([|>])\s*$", raw)
        if m:
            failures.append(
                f"folded scalar in frontmatter ('{m.group(1)}') — "
                "use a single-line single-quoted value"
            )

    name = fm.get("name", "")
    desc = fm.get("description", "")

    if not name:
        failures.append("frontmatter missing 'name'")
    if not desc:
        failures.append("frontmatter missing 'description'")
    else:
        # Strip one layer of surrounding quotes for length/content checks. An
        # UNQUOTED single-line description is valid (this is how real Anthropic
        # skills are written) — do not require quoting.
        d = desc
        if (d.startswith('"') and d.endswith('"')) or (
            d.startswith("'") and d.endswith("'")
        ):
            d = d[1:-1]
        # Rule 1
        if len(d) > DESCRIPTION_MAX:
            failures.append(
                f"description {len(d)} chars > {DESCRIPTION_MAX} limit"
            )
        # Rule 2
        if "<" in d or ">" in d:
            failures.append("description contains angle brackets ('<' or '>')")

    # Rule 4: a horizontal rule line in the body
    for ln in after.splitlines():
        if ln.strip() == "---":
            failures.append("body horizontal rule '---' — use '***' instead")
            break

    # Rule 5: the frontmatter must parse under strict YAML — this is the real
    # ``claude plugin install`` floor. Catches genuinely broken frontmatter
    # (a value that starts with a YAML indicator, an unbalanced quote, a real
    # multi-line fold) without rejecting valid unquoted descriptions.
    try:
        import yaml  # type: ignore
        try:
            loaded = yaml.safe_load(fm_block)
            if not isinstance(loaded, dict):
                failures.append("frontmatter does not parse to a YAML mapping")
        except yaml.YAMLError as e:  # pragma: no cover - message varies
            failures.append(f"frontmatter is not valid YAML: "
                            f"{str(e).splitlines()[0]}")
    except ImportError:
        pass  # PyYAML not present in this runtime — skip the strict parse

    return failures


# ---------------------------------------------------------------------------
# Adapters — any source format -> Anthropic SKILL.md
# ---------------------------------------------------------------------------


def draft_skill_from_prose(prose: str, *, name: str = "",
                           local_llm=None) -> str:
    """Shape free text into a valid SKILL.md.

    If ``local_llm`` (a callable ``str -> str``) is supplied it is used to
    draft the body; decision 4 mandates this runs on a LOCAL model so the
    text never leaves the machine before it is owned + Lock-gated. The
    deterministic fallback below keeps tests hermetic and guarantees a
    valid object even with no model available.
    """
    if local_llm is not None:
        try:
            drafted = local_llm(prose)
            if drafted and "---" in drafted:
                return _normalise_body(drafted)
        except Exception:
            pass  # fall through to deterministic shape
    title = (name or (prose.strip().splitlines()[0] if prose.strip() else "Untitled skill"))
    first = " ".join(prose.split())
    desc = first[:300] or f"User-authored skill: {title}"
    return _build_skill_md(title, desc, prose.strip() or title)


# ---------------------------------------------------------------------------
# Canonical SKILL.md builder — guarantees a valid object
# ---------------------------------------------------------------------------


def _sanitise_description(desc: str) -> str:
    """Single line, no angle brackets, no single quotes, <=1000 chars
    (headroom under the 1024 limit). Always non-empty."""
    d = " ".join((desc or "").split())
    d = d.replace("<", "(").replace(">", ")").replace("'", "")
    d = d[:1000].strip()
    return d or "User-authored skill"


def _sanitise_body(body: str) -> str:
    """Replace any line that is exactly a horizontal rule (``---``) with
    ``***`` so it cannot be mistaken for a frontmatter fence."""
    out = []
    for ln in (body or "").splitlines():
        out.append("***" if ln.strip() == "---" else ln)
    return "\n".join(out).strip()


def _build_skill_md(name: str, description: str, body: str) -> str:
    """Assemble a canonical, validation-passing SKILL.md from parts."""
    # Title capped to the first 6 words so the derived slug / skill_id is short.
    title = " ".join((name or "skill").strip().split()[:6])[:60] or "skill"
    slug = _slug(title)
    desc = _sanitise_description(description)
    safe_body = _sanitise_body(body) or title
    md = (
        "---\n"
        f"name: {slug}\n"
        f"description: '{desc}'\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{safe_body}\n"
    )
    return _normalise_body(md)


# ---------------------------------------------------------------------------
# Per-format adapters → (name, description, body)
# ---------------------------------------------------------------------------


def _adapt_cursor(text: str, *, name: str = "") -> tuple[str, str, str]:
    """Cursor ``.mdc`` rule: frontmatter (description, globs, alwaysApply) +
    markdown body. Preserve the description; fold globs/alwaysApply into a
    provenance line so nothing is silently dropped."""
    fm, body = parse_frontmatter(text)
    desc = fm.get("description", "").strip("'\"")
    nm = name or fm.get("name", "") or (desc[:48] if desc else "cursor rule")
    notes = []
    if fm.get("globs"):
        notes.append(f"globs: {fm['globs'].strip()}")
    if fm.get("alwaysApply"):
        notes.append(f"alwaysApply: {fm['alwaysApply'].strip()}")
    prov = ("\n\n*Imported from a Cursor rule"
            + (f" ({'; '.join(notes)})" if notes else "") + ".*")
    return nm, (desc or nm), (body or text).strip() + prov


def _adapt_cline(text: str, *, name: str = "") -> tuple[str, str, str]:
    """Cline ``.clinerules``: usually a plain markdown instruction body, no
    frontmatter. Derive name/description from the first heading or line."""
    fm, body = parse_frontmatter(text)
    body = (body or text).strip()
    first_line = next((l.strip().lstrip("# ").strip()
                       for l in body.splitlines() if l.strip()), "cline rule")
    nm = name or fm.get("name", "") or first_line[:48]
    desc = fm.get("description", "").strip("'\"") or first_line
    return nm, desc, body + "\n\n*Imported from a Cline rule.*"


def _adapt_continue(text: str, *, name: str = "") -> tuple[str, str, str]:
    """Continue rule block: markdown body, sometimes with name/description
    frontmatter."""
    fm, body = parse_frontmatter(text)
    body = (body or text).strip()
    first_line = next((l.strip().lstrip("# ").strip()
                       for l in body.splitlines() if l.strip()), "continue rule")
    nm = name or fm.get("name", "") or first_line[:48]
    desc = fm.get("description", "").strip("'\"") or first_line
    return nm, desc, body + "\n\n*Imported from a Continue rule.*"


def adapt_to_skill_md(source: str, source_format: str = "auto",
                      *, name: str = "", local_llm=None) -> tuple[str, str]:
    """Normalise any supported source into (skill_md, resolved_format).

    Formats: anthropic-skill | cursor-rule | cline-rule | continue-rule |
    prose | auto. ``auto`` sniffs: a ``globs:``/``alwaysApply:`` frontmatter
    key -> cursor-rule; any frontmatter -> anthropic-skill; else prose.
    """
    fmt = (source_format or "auto").strip().lower()
    text = source or ""

    if fmt == "auto":
        if text.lstrip("﻿").startswith("---"):
            fm, _ = parse_frontmatter(text)
            if "globs" in fm or "alwaysApply" in fm:
                fmt = "cursor-rule"
            elif "name" in fm and "description" in fm:
                fmt = "anthropic-skill"
            else:
                fmt = "cursor-rule" if "description" in fm else "prose"
        else:
            fmt = "prose"

    if fmt == "anthropic-skill":
        return _normalise_body(text), "anthropic-skill"
    if fmt == "cursor-rule":
        return _build_skill_md(*_adapt_cursor(text, name=name)), "cursor-rule"
    if fmt == "cline-rule":
        return _build_skill_md(*_adapt_cline(text, name=name)), "cline-rule"
    if fmt == "continue-rule":
        return _build_skill_md(*_adapt_continue(text, name=name)), "continue-rule"
    if fmt == "prose":
        return draft_skill_from_prose(text, name=name, local_llm=local_llm), "prose"
    raise ValueError(f"unknown source_format: {source_format!r}")


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass
class Ownership:
    author: str = ""
    author_key_fingerprint: str = ""
    signed_with: str = "operator"          # controller | operator
    license: str = "proprietary"
    rights: str = "all-rights-reserved"
    monetization_model: str = "none"        # none | attribution | royalty | license
    terms_ref: str = ""
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillManifest:
    uid: str
    skill_id: str
    name: str
    description: str
    source_format: str
    version: str
    imported_at: str
    body_sha256: str
    ownership: Ownership
    workflows: list[str] = field(default_factory=list)
    policy_hint: dict[str, Any] = field(default_factory=dict)
    manifest_version: int = MANIFEST_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillManifest":
        own = d.get("ownership") or {}
        return cls(
            uid=str(d.get("uid", "")),
            skill_id=str(d.get("skill_id", "")),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            source_format=str(d.get("source_format", "")),
            version=str(d.get("version", "1.0.0")),
            imported_at=str(d.get("imported_at", "")),
            body_sha256=str(d.get("body_sha256", "")),
            ownership=Ownership(**{k: own.get(k) for k in Ownership().to_dict()
                                   if own.get(k) is not None}),
            workflows=list(d.get("workflows") or []),
            policy_hint=dict(d.get("policy_hint") or {}),
            manifest_version=int(d.get("manifest_version") or MANIFEST_VERSION),
        )


# ---------------------------------------------------------------------------
# Signing (controller key root-of-trust, operator fallback)
# ---------------------------------------------------------------------------


def _sign_object(payload: bytes) -> dict[str, str]:
    """Sign with the controller key if available, else the operator key.

    Decision 1: the controller key is the root of trust. Returns a dict
    recording which key signed, its fingerprint, and the hex signature.
    """
    from . import signing
    # Try controller first.
    try:
        if signing.controller_public_key_or_none() is not None:
            sig = signing.sign_with_controller(payload)
            return {
                "signed_with": "controller",
                "fingerprint": signing.public_controller_key_fingerprint() or "",
                "signature": sig,
            }
    except Exception:
        pass
    # Operator fallback.
    sig = signing.sign_bytes(payload)
    return {
        "signed_with": "operator",
        "fingerprint": signing.public_key_fingerprint(),
        "signature": sig,
    }


def _verify_object(payload: bytes, sig_record: dict[str, Any]) -> bool:
    from . import signing
    signed_with = sig_record.get("signed_with", "operator")
    sig = sig_record.get("signature", "")
    if not sig:
        return False
    try:
        if signed_with == "controller":
            return signing.verify_controller_signature(payload, sig)
        return signing.verify_signature(payload, sig)
    except Exception:
        return False


def _signing_payload(body: str, manifest: SkillManifest) -> bytes:
    """Deterministic bytes signed at ingest = normalised body + the
    ownership-bearing manifest core (excludes the volatile imported_at so a
    re-sign of the same content is stable)."""
    core = {
        "uid": manifest.uid,
        "skill_id": manifest.skill_id,
        "body_sha256": manifest.body_sha256,
        "ownership": manifest.ownership.to_dict(),
    }
    return (_normalise_body(body) + "\n" +
            json.dumps(core, sort_keys=True)).encode("utf-8")


# ---------------------------------------------------------------------------
# Store API
# ---------------------------------------------------------------------------


def ingest(folder_path: str | Path,
           source: str,
           *,
           source_format: str = "auto",
           skill_id: str = "",
           name: str = "",
           author: str = "",
           license: str = "proprietary",
           monetization_model: str = "none",
           terms_ref: str = "",
           version: str = "1.0.0",
           workflows: Optional[list[str]] = None,
           policy_hint: Optional[dict[str, Any]] = None,
           on_conflict: str = "upgrade",
           local_llm=None) -> dict[str, Any]:
    """Adapt -> validate -> content-address -> sign -> store.

    Returns ``{ok, uid, skill_id, manifest, signature, warnings, body}`` or
    ``{ok: False, error, failures}`` when validation rejects the skill.

    Idempotency / versioning:
      - identical body (same uid) → no-op, returns the existing object.
      - same skill_id, higher version → upgrade (new uid, lineage
        'supersedes', ``action='upgrade'``).
      - same skill_id, lower version → refused unless ``on_conflict='fork'``.
      - same skill_id, same version, different body → refused unless
        ``on_conflict='fork'``. Fork mints ``<skill_id>~fork``.
    """
    body, fmt = adapt_to_skill_md(source, source_format,
                                  name=name, local_llm=local_llm)
    failures = validate_skill(body)
    if failures:
        return {"ok": False, "error": "skill failed validation",
                "failures": failures}

    fm, _ = parse_frontmatter(body)
    name = fm.get("name", "skill")
    desc = fm.get("description", "").strip("'\"")

    uid = content_uid(body)
    obj_dir = _object_dir(folder_path, uid)

    # Idempotency: identical body already stored.
    if (obj_dir / MANIFEST_FILE).exists():
        existing = load(folder_path, uid)
        return {"ok": True, "uid": uid, "skill_id": existing["manifest"]["skill_id"],
                "manifest": existing["manifest"], "signature": existing["signature"],
                "warnings": ["idempotent: identical body already stored"],
                "body": existing["body"]}

    # Author identity: controller-key fingerprint is the root of trust;
    # `author` is the display label bound to it.
    from . import signing
    fp = ""
    signed_with = "operator"
    try:
        if signing.controller_public_key_or_none() is not None:
            fp = signing.public_controller_key_fingerprint() or ""
            signed_with = "controller"
    except Exception:
        pass
    if not fp:
        fp = signing.public_key_fingerprint()

    sid = (skill_id or "").strip() or f"user:{_slug(author or fp[:8])}/{_slug(name)}"

    # Version conflict against any existing object with the same skill_id.
    lineage: list[dict[str, Any]] = []
    action = "create"
    existing = find_by_skill_id(folder_path, sid)
    if existing is not None:
        ex_ver = existing["manifest"].get("version", "0.0.0")
        ex_uid = existing["manifest"].get("uid", "")
        cmp_new, cmp_old = _parse_version(version), _parse_version(ex_ver)
        if cmp_new > cmp_old:
            action = "upgrade"
            lineage = [{"uid": ex_uid, "version": ex_ver, "relation": "supersedes"}]
        elif cmp_new < cmp_old:
            if on_conflict != "fork":
                return {"ok": False,
                        "error": f"refusing downgrade of {sid!r}: incoming "
                                 f"{version} < stored {ex_ver} "
                                 "(use on_conflict='fork')",
                        "existing_version": ex_ver}
            sid = f"{sid}~fork"
            action = "fork"
            lineage = [{"uid": ex_uid, "version": ex_ver, "relation": "forked-from"}]
        else:  # same version, different body (uid already differs here)
            if on_conflict != "fork":
                return {"ok": False,
                        "error": f"refusing same-version overwrite of {sid!r} "
                                 f"(v{version}) with different body "
                                 "(use on_conflict='fork')",
                        "existing_version": ex_ver}
            sid = f"{sid}~fork"
            action = "fork"
            lineage = [{"uid": ex_uid, "version": ex_ver, "relation": "forked-from"}]

    ownership = Ownership(
        author=author or fp[:8],
        author_key_fingerprint=fp,
        signed_with=signed_with,
        license=license or "proprietary",
        monetization_model=monetization_model or "none",
        terms_ref=terms_ref or "",
        lineage=lineage,
    )
    manifest = SkillManifest(
        uid=uid, skill_id=sid, name=name, description=desc,
        source_format=fmt, version=version or "1.0.0",
        imported_at=_now_iso(), body_sha256=hashlib.sha256(
            _normalise_body(body).encode("utf-8")).hexdigest(),
        ownership=ownership, workflows=list(workflows or []),
        policy_hint=dict(policy_hint or {}),
    )

    payload = _signing_payload(body, manifest)
    sig_record = _sign_object(payload)
    # Keep the manifest's recorded signer consistent with the actual signer.
    manifest.ownership.signed_with = sig_record["signed_with"]
    manifest.ownership.author_key_fingerprint = sig_record["fingerprint"] or fp

    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / BODY_FILE).write_text(_normalise_body(body), encoding="utf-8")
    (obj_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    (obj_dir / SIGNATURE_FILE).write_text(
        json.dumps(sig_record, indent=2, sort_keys=True), encoding="utf-8")

    return {"ok": True, "uid": uid, "skill_id": sid, "action": action,
            "manifest": manifest.to_dict(), "signature": sig_record,
            "warnings": [], "body": _normalise_body(body)}


def load(folder_path: str | Path, uid: str) -> dict[str, Any]:
    """Load a stored skill object by uid. Raises FileNotFoundError if absent."""
    obj_dir = _object_dir(folder_path, uid)
    manifest = json.loads((obj_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    body = (obj_dir / BODY_FILE).read_text(encoding="utf-8")
    sig = json.loads((obj_dir / SIGNATURE_FILE).read_text(encoding="utf-8"))
    return {"uid": uid, "manifest": manifest, "body": body, "signature": sig}


def find_by_skill_id(folder_path: str | Path, skill_id: str) -> Optional[dict[str, Any]]:
    """Return the stored object whose manifest.skill_id matches, newest wins.

    Searches THIS folder's store only. The MCP dispatch path walks ancestors
    separately via the pinned-skills resolver before calling this.
    """
    root = _skills_root(folder_path)
    if not root.exists():
        return None
    best: Optional[dict[str, Any]] = None
    best_key: tuple = ((), "")
    for d in root.iterdir():
        man = d / MANIFEST_FILE
        if not man.exists():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("skill_id") == skill_id.strip():
            # Highest version wins; imported_at breaks ties (second-granular).
            key = (_parse_version(m.get("version", "0.0.0")),
                   m.get("imported_at", ""))
            if best is None or key > best_key:
                best = load(folder_path, d.name)
                best_key = key
    return best


def verify(folder_path: str | Path, uid: str) -> dict[str, Any]:
    """Re-verify a stored object: body hash + signature. Returns
    ``{ok, body_hash_ok, signature_ok, signed_with, fingerprint}``."""
    obj = load(folder_path, uid)
    manifest = SkillManifest.from_dict(obj["manifest"])
    body = obj["body"]
    body_hash_ok = (hashlib.sha256(
        _normalise_body(body).encode("utf-8")).hexdigest() == manifest.body_sha256)
    payload = _signing_payload(body, manifest)
    signature_ok = _verify_object(payload, obj["signature"])
    return {
        "ok": bool(body_hash_ok and signature_ok),
        "body_hash_ok": body_hash_ok,
        "signature_ok": signature_ok,
        "signed_with": obj["signature"].get("signed_with", ""),
        "fingerprint": obj["signature"].get("fingerprint", ""),
    }


def list_ingested(folder_path: str | Path) -> list[dict[str, Any]]:
    """List manifests of all stored objects in THIS folder."""
    root = _skills_root(folder_path)
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        man = d / MANIFEST_FILE
        if man.exists():
            try:
                out.append(json.loads(man.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out

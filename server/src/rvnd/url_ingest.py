# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""URL ingestion lane — save a user-chosen URL into a workspace and retrieve its
content as far as ``robots.txt`` allows.

Demand (2026-06-02): a user adds *any* URL to a workspace (type / drag / paste /
picker). The URL is **saved** in a per-workspace watchlist, and its content is
**fetched** — honouring ``robots.txt`` — then handed to the normal file-ingest
path so the workspace's extractors + domain NDs run over it.

Design notes
------------
* This runs **inside the workspace runtime** (a local MCP process with its own
  network stack), so it fetches directly via ``urllib``. It is *not* the
  Cowork ``web_fetch`` tool and is therefore not subject to Cowork's egress
  allowlist. Targeted, user-selected retrieval — not blind crawling.
* The URL is recorded in ``<folder>/sources/urls.jsonl`` **even when robots
  disallows or the fetch fails** — an honest row, never a silent drop. This
  matches the workspace ethos of surfacing gaps rather than smoothing them.
* Provenance (``source_url``, ``fetched_at``, ``http_status``,
  ``content_hash``, ``robots_allowed``, ``tdm_reservation``, ``lawful_access``)
  is written both as front-matter on the saved text file and as the ledger
  row, so the lawful basis is an audit artifact, not an assumption.
* ``tdm_reservation`` records any machine-readable Art. 4 DSM opt-out signal
  (``tdm-reservation`` header / ``X-Robots-Tag: noai|notrain``). P1 *records*
  it; whether a reservation blocks ingest is a per-workspace policy decision left
  to the caller (``block_on_tdm_reservation``).

Stdlib only — no third-party dependency.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

# Lazy/local import to avoid a heavy import chain at module load.
from .inbox_watcher import ingest_file

__all__ = [
    "ingest_url",
    "read_ledger",
    "DEFAULT_USER_AGENT",
    "SUPPORTED_CONTENT_TYPES",
]

DEFAULT_USER_AGENT = "RVND/0.6 (workspace url-ingest)"
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 5_000_000  # 5 MB cap on a single fetched resource.
MAX_REDIRECTS = 10
SOURCES_SUBDIR = "sources"
LEDGER_NAME = "urls.jsonl"

# Content types we know how to persist for ingestion. Anything else is saved
# to the ledger as a fetch_error rather than guessed at.
SUPPORTED_CONTENT_TYPES = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/pdf": ".pdf",
}

_TEXTLIKE = {"text/html", "application/xhtml+xml", "text/plain",
             "text/markdown", "application/json", "application/xml", "text/xml"}


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    """Minimal boilerplate-stripping HTML→text. Drops script/style/nav noise,
    keeps the page title and visible text. Good enough for P1; the Lock-mirror
    cleaner can replace it later for a higher-fidelity pass."""

    # NB: do not skip <head> — the <title> lives there and is captured via the
    # in_title flag. Skipping head would swallow the title before it's read.
    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4",
                     "h5", "h6", "section", "article"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        raw = " ".join(self._chunks)
        # Collapse runs of whitespace but preserve paragraph breaks.
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\s*\n\s*", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> tuple[str, str]:
    """Return ``(title, text)`` extracted from an HTML document."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        # On a malformed document, fall back to a crude tag strip.
        text = re.sub(r"<[^>]+>", " ", html)
        return "", re.sub(r"\s+", " ", text).strip()
    return parser.title.strip(), parser.get_text()


# ---------------------------------------------------------------------------
# Safety: URL validation + SSRF guard
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        return f"invalid URL: {exc}"
    if parts.scheme not in ("http", "https"):
        return f"unsupported scheme: {parts.scheme or '(none)'}"
    if not parts.hostname:
        return "missing host"
    if parts.username is not None or parts.password is not None:
        return "URL credentials are not supported"
    if port is not None and not 1 <= port <= 65535:
        return "invalid port"
    return None


def _resolve_public(host: str, port: int) -> list[tuple[int, tuple[Any, ...]]]:
    """Resolve ``host`` once and return globally routable stream destinations.

    Every returned address is checked. The caller connects directly to one of
    these socket addresses, so DNS cannot select a different destination after
    validation.
    """
    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise _CappedFetchError(f"host resolution failed: {exc}") from exc
    destinations: list[tuple[int, tuple[Any, ...]]] = []
    for info in infos:
        family, socktype, proto, _, sockaddr = info
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            raise _CappedFetchError(
                "host resolved to an invalid address") from None
        if not ip.is_global:
            raise _CappedFetchError(
                "host did not resolve exclusively to public addresses")
        if socktype == socket.SOCK_STREAM and proto == socket.IPPROTO_TCP:
            destinations.append((family, sockaddr))
    if not destinations:
        raise _CappedFetchError("host did not resolve to a usable public address")
    return destinations


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials a previously validated socket address."""

    def __init__(self, host: str, port: int, destination: tuple[int, tuple[Any, ...]],
                 timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._destination = destination

    def connect(self) -> None:
        family, sockaddr = self._destination
        sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            sock.settimeout(self.timeout)
            sock.connect(sockaddr)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to an IP with hostname verification intact."""

    def __init__(self, host: str, port: int, destination: tuple[int, tuple[Any, ...]],
                 timeout: float) -> None:
        super().__init__(
            host, port=port, timeout=timeout,
            context=ssl.create_default_context())
        self._destination = destination

    def connect(self) -> None:
        family, sockaddr = self._destination
        raw_sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            raw_sock.settimeout(self.timeout)
            raw_sock.connect(sockaddr)
            self.sock = self._context.wrap_socket(
                raw_sock, server_hostname=self.host)
        except BaseException:
            raw_sock.close()
            raise


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def _robots_allows(url: str, user_agent: str,
                   timeout: float) -> tuple[bool, str]:
    """Check ``robots.txt`` for ``url``. Returns ``(allowed, note)``.

    Convention: if robots.txt is missing / unreachable (404, timeout, error),
    access is **allowed** — but the note records why so the decision is
    auditable. A reachable robots.txt that disallows the path → not allowed.
    """
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        conn, resp, _ = _open_url(
            robots_url, {"User-Agent": user_agent}, timeout)
        try:
            if resp.status >= 400:
                return True, f"robots.txt HTTP {resp.status} — default allow"
            body = resp.read(1_000_000).decode("utf-8", errors="replace")
        finally:
            conn.close()
    except (_CappedFetchError, OSError, ValueError) as e:
        return True, f"robots.txt unreachable ({type(e).__name__}) — default allow"

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    allowed = rp.can_fetch(user_agent, url)
    return allowed, "robots.txt evaluated"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


class _CappedFetchError(Exception):
    pass


def _open_url(url: str, headers: dict[str, str], timeout: float
              ) -> tuple[http.client.HTTPConnection,
                         http.client.HTTPResponse, str]:
    """Open an HTTP URL with validated redirects and pinned destinations."""
    current_url = urldefrag(url).url
    for redirect_count in range(MAX_REDIRECTS + 1):
        validation_error = _validate_url(current_url)
        if validation_error:
            raise _CappedFetchError(validation_error)
        parts = urlsplit(current_url)
        host = (parts.hostname or "").encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        destinations = _resolve_public(host, port)
        destination = destinations[0]
        connection_type = (
            _PinnedHTTPSConnection
            if parts.scheme == "https"
            else _PinnedHTTPConnection
        )
        conn = connection_type(host, port, destination, timeout)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        try:
            conn.request("GET", target, headers=headers)
            resp = conn.getresponse()
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            conn.close()
            raise _CappedFetchError(
                f"{type(exc).__name__}: {exc}") from exc

        if resp.status not in (301, 302, 303, 307, 308):
            return conn, resp, current_url
        location = resp.getheader("Location")
        conn.close()
        if not location:
            raise _CappedFetchError(
                f"redirect HTTP {resp.status} omitted Location")
        if redirect_count == MAX_REDIRECTS:
            raise _CappedFetchError(
                f"redirect limit exceeded ({MAX_REDIRECTS})")
        current_url = urldefrag(urljoin(current_url, location)).url

    raise _CappedFetchError(f"redirect limit exceeded ({MAX_REDIRECTS})")


def _fetch(url: str, user_agent: str, timeout: float, max_bytes: int,
           etag: str | None, last_modified: str | None
           ) -> dict[str, Any]:
    """Fetch ``url`` with caps + conditional headers.

    Returns a dict: ``{status, headers, content_type, body, not_modified}``.
    Raises ``_CappedFetchError`` on transport failure or oversize.
    """
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        conn, resp, final_url = _open_url(url, headers, timeout)
        try:
            status = resp.status
            if status == 304:
                return {"status": 304, "headers": {}, "content_type": "",
                        "body": b"", "not_modified": True,
                        "final_url": final_url}
            if status >= 400:
                raise _CappedFetchError(f"HTTP {status}")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            ctype = (resp_headers.get("content-type", "")
                     .split(";")[0].strip().lower())
            # Stream with a hard cap.
            chunks: list[bytes] = []
            read = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                read += len(chunk)
                if read > max_bytes:
                    raise _CappedFetchError(
                        f"resource exceeds {max_bytes}-byte cap")
                chunks.append(chunk)
            return {
                "status": status,
                "headers": resp_headers,
                "content_type": ctype,
                "body": b"".join(chunks),
                "not_modified": False,
                "final_url": final_url,
            }
        finally:
            conn.close()
    except _CappedFetchError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise _CappedFetchError(f"{type(e).__name__}: {e}")


def _tdm_reservation(headers: dict[str, str]) -> str:
    """Best-effort read of a machine-readable Art. 4 DSM opt-out signal.

    Returns ``""`` when none is present. Records, does not enforce.
    """
    signals: list[str] = []
    tdm = headers.get("tdm-reservation", "").strip()
    if tdm and tdm != "0":
        signals.append(f"tdm-reservation={tdm}")
    xrobots = headers.get("x-robots-tag", "").lower()
    for token in ("noai", "noimageai", "notrain"):
        if token in xrobots:
            signals.append(f"x-robots-tag:{token}")
    return "; ".join(signals)


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _sources_dir(folder: Path) -> Path:
    d = folder / SOURCES_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger_path(folder: Path) -> Path:
    return _sources_dir(folder) / LEDGER_NAME


def read_ledger(folder_context: str) -> list[dict[str, Any]]:
    """Return all rows of a workspace's URL watchlist (newest write wins per URL)."""
    folder = Path(folder_context).expanduser().resolve()
    path = folder / SOURCES_SUBDIR / LEDGER_NAME
    if not path.exists():
        return []
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = row.get("url")
        if url:
            rows[url] = row
    return list(rows.values())


def _ledger_find(folder: Path, url: str) -> dict[str, Any] | None:
    for row in read_ledger(str(folder)):
        if row.get("url") == url:
            return row
    return None


def _ledger_append(folder: Path, row: dict[str, Any]) -> None:
    path = _ledger_path(folder)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Saved-file naming + provenance
# ---------------------------------------------------------------------------


def _slug_for(url: str, ext: str) -> tuple[str, str]:
    """Return ``(host, filename)`` for the saved resource."""
    parts = urlsplit(url)
    host = (parts.hostname or "unknown").replace(":", "_")
    path = (parts.path or "/").strip("/")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path) or "index"
    stem = stem[:80].strip("-") or "index"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return host, f"{stem}.{digest}{ext}"


def _frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def ingest_url(
    folder_context: str,
    url: str,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
    extractor: Any | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_robots_override: bool = False,
    block_on_tdm_reservation: bool = False,
) -> dict[str, Any]:
    """Save ``url`` to the workspace's watchlist and fetch its content (robots-permitting).

    The URL is always recorded. The terminal ``state`` is one of:
      - ``fetched``        — retrieved and ingested; ``pair_ids`` populated.
      - ``unchanged``      — server returned 304 / identical hash; not re-ingested.
      - ``robots_blocked`` — robots.txt disallows; saved, not fetched.
      - ``tdm_reserved``   — machine-readable Art. 4 opt-out + caller chose to block.
      - ``fetch_error``    — transport / type / size failure; saved, not ingested.

    Network destinations must resolve exclusively to public addresses.
    """
    folder = Path(folder_context).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        return {"url": url, "state": "fetch_error",
                "error": f"folder not found: {folder}"}

    url = url.strip()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    base_row: dict[str, Any] = {
        "url": url,
        "added_at": now,
        "added_by": actor,
        "robots_allowed": None,
        "tdm_reservation": "",
        "lawful_access": "user_selected",
        "http_status": None,
        "content_hash": None,
        "etag": None,
        "last_modified": None,
        "last_fetched": None,
        "saved_path": None,
        "pair_ids": [],
        "state": "saved",
    }
    prior = _ledger_find(folder, url)
    if prior:
        base_row["added_at"] = prior.get("added_at", now)
        base_row["added_by"] = prior.get("added_by", actor)

    # --- validation + SSRF guard ----------------------------------------
    validation_error = _validate_url(url)
    if validation_error:
        row = {**base_row, "state": "fetch_error", "error": validation_error}
        _ledger_append(folder, row)
        return row

    # --- robots.txt ------------------------------------------------------
    allowed, robots_note = _robots_allows(url, user_agent, timeout)
    base_row["robots_allowed"] = allowed
    base_row["robots_note"] = robots_note
    if not allowed and not allow_robots_override:
        row = {**base_row, "state": "robots_blocked"}
        _ledger_append(folder, row)
        return row

    # --- fetch -----------------------------------------------------------
    try:
        fetched = _fetch(url, user_agent, timeout, max_bytes,
                         etag=(prior or {}).get("etag"),
                         last_modified=(prior or {}).get("last_modified"))
    except _CappedFetchError as e:
        row = {**base_row, "state": "fetch_error", "error": str(e)}
        _ledger_append(folder, row)
        return row

    if fetched["not_modified"]:
        row = {**base_row, "state": "unchanged",
               "http_status": 304,
               "content_hash": (prior or {}).get("content_hash"),
               "etag": (prior or {}).get("etag"),
               "last_modified": (prior or {}).get("last_modified"),
               "saved_path": (prior or {}).get("saved_path"),
               "pair_ids": (prior or {}).get("pair_ids", []),
               "last_fetched": now}
        _ledger_append(folder, row)
        return row

    headers = fetched["headers"]
    ctype = fetched["content_type"]
    base_row["http_status"] = fetched["status"]
    base_row["etag"] = headers.get("etag")
    base_row["last_modified"] = headers.get("last-modified")
    base_row["last_fetched"] = now
    base_row["tdm_reservation"] = _tdm_reservation(headers)

    if base_row["tdm_reservation"] and block_on_tdm_reservation:
        row = {**base_row, "state": "tdm_reserved"}
        _ledger_append(folder, row)
        return row

    if ctype not in SUPPORTED_CONTENT_TYPES:
        row = {**base_row, "state": "fetch_error",
               "error": f"unsupported content-type: {ctype or '(none)'}"}
        _ledger_append(folder, row)
        return row

    body = fetched["body"]
    content_hash = "sha256:" + hashlib.sha256(body).hexdigest()[:32]
    base_row["content_hash"] = content_hash

    # Content-level idempotency: identical bytes as last time → skip re-ingest.
    if prior and prior.get("content_hash") == content_hash and prior.get("pair_ids"):
        row = {**base_row, "state": "unchanged",
               "saved_path": prior.get("saved_path"),
               "pair_ids": prior.get("pair_ids", [])}
        _ledger_append(folder, row)
        return row

    # --- persist to disk -------------------------------------------------
    provenance = {
        "source_url": url,
        "fetched_at": now,
        "http_status": fetched["status"],
        "content_hash": content_hash,
        "robots_allowed": allowed,
        "tdm_reservation": base_row["tdm_reservation"] or "none",
        "lawful_access": "user_selected",
        "retrieved_by": actor,
    }

    if ctype in _TEXTLIKE:
        ext = SUPPORTED_CONTENT_TYPES[ctype]
        if ctype in ("text/html", "application/xhtml+xml"):
            title, text = _html_to_text(body.decode("utf-8", errors="replace"))
            ext = ".md"
            if title:
                provenance["title"] = title
            payload = _frontmatter(provenance) + (f"# {title}\n\n" if title else "") + text
        else:
            payload = _frontmatter(provenance) + body.decode("utf-8", errors="replace")
        save_bytes = payload.encode("utf-8")
    else:
        # Binary (e.g. PDF): save raw; provenance lives in the ledger + a
        # sidecar so the FormatAwareExtractor can read the original bytes.
        ext = SUPPORTED_CONTENT_TYPES[ctype]
        save_bytes = body

    host_dir, filename = _slug_for(url, ext)
    target_dir = _sources_dir(folder) / host_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    tmp = target_dir / (f".{filename}.partial")
    try:
        with tmp.open("wb") as fh:
            fh.write(save_bytes)
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    if not ctype in _TEXTLIKE:
        # Sidecar provenance for binary resources.
        sidecar = target_dir / (filename + ".provenance.json")
        sidecar.write_text(json.dumps(provenance, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    base_row["saved_path"] = str(target)

    # --- ingest ----------------------------------------------------------
    try:
        pair_ids = ingest_file(
            file_path=str(target),
            folder_context=str(folder),
            log_root=log_root,
            extractor=extractor,   # None → DefaultExtractor inside ingest_file
            actor=actor,
        )
        # URL acquisition ends here; the acquired local artifact then enters
        # the same network-free Ingest → Versum plane as file ingestion.
        from .ingest.versum import ingest_into_versum
        graph_ingest = ingest_into_versum(str(target), str(folder))
    except Exception as e:  # noqa: BLE001 — record the failure honestly
        row = {**base_row, "state": "fetch_error",
               "error": f"ingest failed: {type(e).__name__}: {e}"}
        _ledger_append(folder, row)
        return row

    row = {
        **base_row,
        "state": "fetched",
        "pair_ids": pair_ids,
        "versum": graph_ingest.get("write", graph_ingest),
    }
    _ledger_append(folder, row)
    return row

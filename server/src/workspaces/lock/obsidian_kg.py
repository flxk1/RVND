# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Obsidian-vault -> Privacy Lock KG adapter.

The local Knowledge Graph lives as an Obsidian vault — a directory of `.md`
notes with YAML frontmatter, `[[wikilinks]]`, and `#tags`. Each note is one
or more entities. Privacy Lock needs to know which entities are confidential
so they get refused at the cloud-LLM boundary.

This adapter reads the vault, finds the confidential entities, and returns a
newline-separated list ready to pass as `context=` to `lock_text()`.

Confidentiality conventions (any one of these flags a note as confidential):

1. **Frontmatter `confidential: true`** — explicit binary flag.
2. **Frontmatter `sensitivity: <tier>`** — `confidential` or `secret` flags;
   `public` and `internal` do not.
3. **`#confidential` tag** in the body of the note.
4. **`CONFIDENTIAL.md` manifest** at the vault root — a markdown note whose
   body lists confidential entity names (one per line, optionally prefixed
   `-`/`*`/`•`).

The entity name extracted from a confidential note is the note's basename
without `.md` (canonical Obsidian convention). Manifest entries are taken
verbatim per line.

The adapter is conservative: it does NOT mark notes confidential by
association (e.g. notes that wikilink to a confidential note are NOT
auto-flagged). Rule: flag explicitly, never guess.

No PyYAML dependency — the frontmatter parser is a minimal homegrown
top-of-file `---` block reader. Handles the formats Obsidian ships by default.
"""

from __future__ import annotations

import re
from pathlib import Path


# Directories the adapter skips entirely.
_SKIP_DIRS = {
    ".obsidian",     # Obsidian app config — never user content
    ".trash",        # Obsidian's trash bin
    ".git",          # git internals
    "_archive",      # convention for retired content
    "node_modules",  # if a vault sits next to JS code
    ".venv",
    "__pycache__",
}

# Frontmatter key names treated as confidentiality signals.
_CONFIDENTIAL_KEYS = {"confidential", "sensitive"}

# Frontmatter `sensitivity:` values that flag a note as confidential.
_CONFIDENTIAL_SENSITIVITY_VALUES = {"confidential", "secret", "restricted"}

# The tag the body scanner looks for. Word-boundary on both sides.
_CONFIDENTIAL_TAG_RE = re.compile(r"(?:^|\s|>)#confidential\b", re.IGNORECASE)

# Default manifest filename at the vault root.
_DEFAULT_MANIFEST_NAME = "CONFIDENTIAL.md"


def kg_context_for_vault(
    vault_path: str | Path,
    *,
    manifest_name: str = _DEFAULT_MANIFEST_NAME,
    extra_skip_dirs: set[str] | None = None,
) -> str:
    """Read an Obsidian vault and return a confidential-terms context string.

    Args:
        vault_path: path to the vault root directory.
        manifest_name: name of the vault-root manifest note. Defaults to
            ``CONFIDENTIAL.md``.
        extra_skip_dirs: additional directory names to skip alongside the
            defaults (.obsidian, .git, etc.).

    Returns:
        A newline-separated, sorted, de-duplicated string of confidential
        entity names. Empty string if the vault has no confidential entities.

    Raises:
        FileNotFoundError: if ``vault_path`` does not exist.
        NotADirectoryError: if ``vault_path`` is not a directory.
    """
    root = Path(vault_path)
    if not root.exists():
        raise FileNotFoundError(f"vault path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"vault path is not a directory: {root}")

    skip = set(_SKIP_DIRS)
    if extra_skip_dirs:
        skip |= set(extra_skip_dirs)

    confidential: set[str] = set()

    # Pass 1 — walk the vault, extract per-note confidential signals.
    for note_path in _walk_notes(root, skip_dirs=skip):
        try:
            content = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if _note_is_confidential(content):
            confidential.add(note_path.stem)

    # Pass 2 — read the vault-root manifest if present.
    manifest_path = root / manifest_name
    if manifest_path.is_file():
        confidential |= _parse_manifest(manifest_path)

    return "\n".join(sorted(confidential))


def _walk_notes(root: Path, *, skip_dirs: set[str]):
    """Yield every ``.md`` file under ``root``, skipping configured directories."""
    for path in root.rglob("*.md"):
        # Skip if any path component matches a skip-dir.
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.is_file():
            yield path


def _note_is_confidential(content: str) -> bool:
    """Return True if any confidentiality signal is present in this note."""
    fm = _read_frontmatter(content)
    if fm:
        # Binary flag — `confidential: true`
        for key in _CONFIDENTIAL_KEYS:
            if _truthy(fm.get(key)):
                return True
        # Tier flag — `sensitivity: confidential | secret | restricted`
        tier = (fm.get("sensitivity") or "").strip().lower()
        if tier in _CONFIDENTIAL_SENSITIVITY_VALUES:
            return True

    # Body tag — `#confidential`
    body = _strip_frontmatter(content)
    if _CONFIDENTIAL_TAG_RE.search(body):
        return True

    return False


def _read_frontmatter(content: str) -> dict[str, str]:
    """Parse the top-of-file ``---`` block as flat key/value pairs.

    Conservative parser — handles the cases Obsidian writes by default:
    ``key: value``, ``key: "quoted value"``, ``key: true``, blank-line tolerated
    inside the block. Nested mappings, lists, and multi-line scalars are not
    interpreted (the parser is intentionally minimal — confidentiality flags
    don't need a real YAML library).

    Returns an empty dict if no frontmatter is present.
    """
    if not content.startswith("---"):
        return {}

    # Find the closing `---` on its own line.
    lines = content.split("\n")
    if len(lines) < 2 or lines[0].strip() != "---":
        return {}

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}

    result: dict[str, str] = {}
    for line in lines[1:end_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip matching quote pairs.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key.lower()] = value
    return result


def _strip_frontmatter(content: str) -> str:
    """Return the body of the note (everything after the closing frontmatter ``---``)."""
    if not content.startswith("---"):
        return content
    lines = content.split("\n")
    if len(lines) < 2 or lines[0].strip() != "---":
        return content
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :])
    return content


def _truthy(value) -> bool:
    """Frontmatter-aware truthiness check."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    return s in {"true", "yes", "1", "on"}


def _parse_manifest(manifest_path: Path) -> set[str]:
    """Extract entity names from a vault-root ``CONFIDENTIAL.md`` manifest.

    Each non-blank, non-heading line in the body is treated as one entity
    name (after stripping leading bullet markers). Frontmatter is ignored.
    """
    try:
        content = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()

    body = _strip_frontmatter(content)
    result: set[str] = set()
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):  # markdown heading
            continue
        # Strip common bullet prefixes.
        line = line.lstrip("-*•").strip()
        # Strip wrapping wikilink brackets if present: [[Foo]] -> Foo.
        if line.startswith("[[") and line.endswith("]]"):
            line = line[2:-2].strip()
        if line and len(line) >= 2:  # filter trivial single-chars
            result.add(line)
    return result

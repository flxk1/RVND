# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Obsidian-vault -> Privacy Lock KG adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from rvnd.lock import kg_context_for_vault, lock_text
from rvnd.lock.tier_c import reset_backend_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_note(vault: Path, relpath: str, content: str) -> Path:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Empty / negative cases
# ---------------------------------------------------------------------------


def test_empty_vault_returns_empty_string(tmp_path):
    result = kg_context_for_vault(tmp_path)
    assert result == ""


def test_vault_with_no_confidential_notes_returns_empty(tmp_path):
    _write_note(tmp_path, "Public Note.md", "# Hello\n\nClear info only.\n")
    _write_note(tmp_path, "Another.md", "---\ntitle: Another\nsensitivity: public\n---\nNothing secret here.\n")
    assert kg_context_for_vault(tmp_path) == ""


def test_missing_vault_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        kg_context_for_vault(tmp_path / "nonexistent")


def test_vault_path_is_a_file_raises(tmp_path):
    f = tmp_path / "not_a_dir.md"
    f.write_text("hi")
    with pytest.raises(NotADirectoryError):
        kg_context_for_vault(f)


# ---------------------------------------------------------------------------
# Frontmatter signals
# ---------------------------------------------------------------------------


def test_frontmatter_confidential_true_flags_note(tmp_path):
    _write_note(
        tmp_path,
        "Workspaceversum.md",
        "---\ntitle: Workspaceversum\nconfidential: true\n---\n# Architecture\n",
    )
    assert kg_context_for_vault(tmp_path) == "Workspaceversum"


def test_frontmatter_sensitivity_secret_flags_note(tmp_path):
    _write_note(
        tmp_path,
        "Project Echo.md",
        "---\nsensitivity: secret\n---\nDetails here.\n",
    )
    assert kg_context_for_vault(tmp_path) == "Project Echo"


def test_frontmatter_sensitivity_confidential_flags_note(tmp_path):
    _write_note(
        tmp_path,
        "ClientA.md",
        "---\nsensitivity: confidential\n---\nDetails.\n",
    )
    assert kg_context_for_vault(tmp_path) == "ClientA"


def test_frontmatter_sensitivity_public_does_not_flag(tmp_path):
    _write_note(
        tmp_path,
        "Open Note.md",
        "---\nsensitivity: public\n---\nClear.\n",
    )
    assert kg_context_for_vault(tmp_path) == ""


def test_frontmatter_quoted_values_handled(tmp_path):
    _write_note(
        tmp_path,
        "Quoted.md",
        '---\nsensitivity: "confidential"\n---\nBody.\n',
    )
    assert kg_context_for_vault(tmp_path) == "Quoted"


def test_frontmatter_yes_is_truthy(tmp_path):
    _write_note(
        tmp_path,
        "YesNote.md",
        "---\nconfidential: yes\n---\nBody.\n",
    )
    assert kg_context_for_vault(tmp_path) == "YesNote"


# ---------------------------------------------------------------------------
# Body-tag signal
# ---------------------------------------------------------------------------


def test_body_tag_confidential_flags_note(tmp_path):
    _write_note(
        tmp_path,
        "Tagged Note.md",
        "# Heading\n\nSome content. #confidential #project\n",
    )
    assert kg_context_for_vault(tmp_path) == "Tagged Note"


def test_body_tag_is_case_insensitive(tmp_path):
    _write_note(
        tmp_path,
        "Upper.md",
        "Body with #Confidential tag.\n",
    )
    assert kg_context_for_vault(tmp_path) == "Upper"


def test_hash_in_url_does_not_falsely_flag(tmp_path):
    """A `#confidential` substring inside text without word boundary shouldn't trigger."""
    _write_note(
        tmp_path,
        "Url.md",
        "Visit https://example.com#confidentialfragment for more.\n",
    )
    # The regex requires whitespace/start-of-line/quote before `#`, so this should NOT flag.
    assert kg_context_for_vault(tmp_path) == ""


# ---------------------------------------------------------------------------
# Manifest signal
# ---------------------------------------------------------------------------


def test_manifest_entries_flagged(tmp_path):
    _write_note(
        tmp_path,
        "CONFIDENTIAL.md",
        "# Confidential entities\n\n- Workspaceversum\n- Brain\n- Project Echo\n",
    )
    _write_note(tmp_path, "Random.md", "Public stuff.\n")
    result = kg_context_for_vault(tmp_path)
    assert "Brain" in result
    assert "Workspaceversum" in result
    assert "Project Echo" in result


def test_manifest_handles_wikilink_brackets(tmp_path):
    _write_note(
        tmp_path,
        "CONFIDENTIAL.md",
        "# Confidential\n\n- [[Workspaceversum]]\n- [[Brain]]\n",
    )
    result = kg_context_for_vault(tmp_path)
    assert "Workspaceversum" in result
    assert "Brain" in result


def test_manifest_skips_headings(tmp_path):
    _write_note(
        tmp_path,
        "CONFIDENTIAL.md",
        "# Top heading\n\n## Subheading\n\n- RealEntity\n",
    )
    result = kg_context_for_vault(tmp_path)
    assert result == "RealEntity"  # only the bullet line, not the headings


def test_manifest_unioned_with_frontmatter(tmp_path):
    _write_note(
        tmp_path,
        "CONFIDENTIAL.md",
        "- ManifestOnly\n",
    )
    _write_note(
        tmp_path,
        "FromFrontmatter.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    result = kg_context_for_vault(tmp_path).split("\n")
    assert "ManifestOnly" in result
    assert "FromFrontmatter" in result


# ---------------------------------------------------------------------------
# Walk behaviour
# ---------------------------------------------------------------------------


def test_recursive_walk_finds_nested_notes(tmp_path):
    _write_note(
        tmp_path,
        "Areas/Projects/Secret.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    assert kg_context_for_vault(tmp_path) == "Secret"


def test_skip_dot_obsidian(tmp_path):
    """Notes inside .obsidian/ are config artifacts, not user content."""
    _write_note(
        tmp_path,
        ".obsidian/plugins/foo.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    assert kg_context_for_vault(tmp_path) == ""


def test_skip_archive(tmp_path):
    _write_note(
        tmp_path,
        "_archive/Old.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    assert kg_context_for_vault(tmp_path) == ""


def test_skip_extra_dirs_param(tmp_path):
    _write_note(
        tmp_path,
        "drafts/Draft.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    assert kg_context_for_vault(tmp_path, extra_skip_dirs={"drafts"}) == ""


# ---------------------------------------------------------------------------
# Dedup + sort
# ---------------------------------------------------------------------------


def test_dedup_across_signals(tmp_path):
    """Same entity flagged by frontmatter AND manifest = listed once."""
    _write_note(
        tmp_path,
        "Workspaceversum.md",
        "---\nconfidential: true\n---\nbody\n",
    )
    _write_note(
        tmp_path,
        "CONFIDENTIAL.md",
        "- Workspaceversum\n",
    )
    result = kg_context_for_vault(tmp_path).split("\n")
    assert result == ["Workspaceversum"]


def test_output_is_sorted(tmp_path):
    _write_note(tmp_path, "Zebra.md", "---\nconfidential: true\n---\nbody\n")
    _write_note(tmp_path, "Alpha.md", "---\nconfidential: true\n---\nbody\n")
    _write_note(tmp_path, "Mango.md", "---\nconfidential: true\n---\nbody\n")
    result = kg_context_for_vault(tmp_path).split("\n")
    assert result == ["Alpha", "Mango", "Zebra"]


# ---------------------------------------------------------------------------
# Integration with lock_text
# ---------------------------------------------------------------------------


def test_integration_with_lock_text_refuses(tmp_path, monkeypatch):
    """End-to-end: vault declares 'Workspaceversum' confidential; text mentioning it refuses."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    _write_note(
        tmp_path,
        "Workspaceversum.md",
        "---\nconfidential: true\n---\nDetails.\n",
    )
    context = kg_context_for_vault(tmp_path)
    decision = lock_text(
        "the workspaceversum phase-3 ships in june.",
        context=context,
    )
    assert decision.action == "refuse"
    assert any("confidential" in f.detail.lower() for f in decision.findings)


def test_integration_with_lock_text_allows(tmp_path, monkeypatch):
    """End-to-end: vault confidential terms not in text; lock_text allows."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    reset_backend_cache()
    _write_note(
        tmp_path,
        "Workspaceversum.md",
        "---\nconfidential: true\n---\nDetails.\n",
    )
    context = kg_context_for_vault(tmp_path)
    decision = lock_text("the build pipeline finishes in twelve minutes.", context=context)
    assert decision.action == "allow"

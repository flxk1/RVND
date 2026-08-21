# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Top-level oversight dial verbs: oversight (show/set), mute, unmute."""
import os

from rvnd import cli
from rvnd.policy import effective_policy, load_policy, set_oversight_level, OVERSIGHT_LEVELS

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def test_oversight_levels_constant():
    assert OVERSIGHT_LEVELS == ("autonomous", "notify", "review", "approve",
                                "supervised", "manual")


def test_set_oversight_level_persists_and_audits(tmp_path):
    c = tmp_path / "c"; c.mkdir()
    lr = tmp_path / "log"
    set_oversight_level(c, "manual", log_root=lr)
    assert load_policy(c).oversight_default_level == "manual"


def test_set_oversight_level_rejects_unknown(tmp_path):
    c = tmp_path / "c"; c.mkdir()
    try:
        set_oversight_level(c, "bogus", log_root=tmp_path / "log")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unknown oversight level" in str(e)


def test_cli_oversight_set_then_show(tmp_path, capsys):
    c = tmp_path / "c"; c.mkdir()
    lr = str(tmp_path / "log")
    assert cli.main(["--log-root", lr, "oversight", "supervised",
                     "--folder", str(c)]) == 0
    assert cli.main(["--log-root", lr, "oversight", "--folder", str(c)]) == 0
    out = capsys.readouterr().out
    assert "supervised" in out


def test_cli_mute_requires_ack_then_mutes(tmp_path, capsys):
    c = tmp_path / "c"; c.mkdir()
    lr = str(tmp_path / "log")
    # without ack -> refused, prompts still active
    assert cli.main(["--log-root", lr, "mute", "--folder", str(c)]) == 2
    assert effective_policy(c, log_root=tmp_path / 'log').oversight_is_active is True
    capsys.readouterr()
    # with ack -> muted
    assert cli.main(["--log-root", lr, "mute", "--folder", str(c),
                     "--i-accept-the-risk"]) == 0
    assert effective_policy(c, log_root=tmp_path / 'log').oversight_is_active is False
    # unmute restores
    assert cli.main(["--log-root", lr, "unmute", "--folder", str(c)]) == 0
    assert effective_policy(c, log_root=tmp_path / 'log').oversight_is_active is True

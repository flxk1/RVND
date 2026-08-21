# SPDX-License-Identifier: AGPL-3.0-only
"""The posture is the deployment's, and every path agrees about that.

RVND governs egress with no folder at all, so its enforcement posture cannot be
something a user's directory sets. The split landed at the resolver; this pins
the rest of it:

* the mutators refuse rather than writing a declaration nothing reads;
* the decision sites read the effective policy, not the folder's;
* the capability moved rather than vanished — the deployment can still set it.

A mutator that writes a file enforcement ignores is the same defect as an audit
write that returns success while dropping the record: the caller is told it
worked.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from rvnd import subject as S
from rvnd.policy import (DEPLOYMENT_POLICY_FILENAME, FolderPolicy,
                         PolicyScopeError, deployment_policy,
                         disable_lock, disable_lock_for_deployment,
                         disable_oversight, disable_oversight_for_deployment,
                         effective_policy, enable_lock,
                         enable_lock_for_deployment, enable_oversight,
                         enable_oversight_for_deployment, load_policy,
                         save_policy)


@pytest.mark.parametrize("fn,kwargs", [
    (disable_lock, {"accepted_by": "alex"}),
    (disable_oversight, {"accepted_by": "alex"}),
    (enable_lock, {}),
    (enable_oversight, {}),
])
def test_folder_level_posture_mutators_refuse(tmp_path, fn, kwargs):
    """Both directions. Enabling looks harmless, which is why it was the one
    left behind: silently ineffective in the reassuring direction is worse,
    because nobody investigates protection that appears to be ON."""
    with pytest.raises(PolicyScopeError) as e:
        fn(tmp_path, **kwargs)
    assert "deployment" in str(e.value)
    assert "_for_deployment" in str(e.value), "the refusal must name the replacement"


def test_the_deployment_can_still_turn_the_lock_off_and_on(tmp_path):
    """The capability moved; it did not vanish."""
    lr = tmp_path / "lr"
    assert deployment_policy(lr).lock_is_active is True

    disable_lock_for_deployment(accepted_by="alex", reason="test", log_root=lr)
    assert deployment_policy(lr).lock_is_active is False, (
        "the deployment could not turn its own Lock off — the split removed a "
        "capability instead of relocating it")

    enable_lock_for_deployment(log_root=lr)
    assert deployment_policy(lr).lock_is_active is True


def test_the_deployment_can_still_turn_oversight_off_and_on(tmp_path):
    lr = tmp_path / "lr"
    disable_oversight_for_deployment(accepted_by="alex", reason="test", log_root=lr)
    assert deployment_policy(lr).oversight_is_active is False
    enable_oversight_for_deployment(log_root=lr)
    assert deployment_policy(lr).oversight_is_active is True


def test_an_unacknowledged_boolean_never_disables_a_protection(tmp_path):
    """The safeguard that made the folder version safe survives the move."""
    from rvnd.policy import save_deployment_policy
    lr = tmp_path / "lr"
    save_deployment_policy(FolderPolicy(privacy_lock_enabled=False), lr)
    assert deployment_policy(lr).lock_is_active is True, (
        "a flipped boolean with no acknowledgement disabled the Lock")


def test_a_folder_keeps_what_is_genuinely_its_own(tmp_path):
    """The denylist's point: fields nobody enumerated still belong to the folder."""
    lr = tmp_path / "lr"; lr.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    save_policy(ws, FolderPolicy(ai_training_optout=True,
                                 moderation_rules={"banned_terms": ["x"]}))
    eff = effective_policy(ws, log_root=lr)
    assert eff.ai_training_optout is True
    assert eff.moderation_rules == {"banned_terms": ["x"]}, (
        "a folder field outside the deployment's list was dropped — the "
        "resolver must start from the folder and force only the regime")


def test_no_decision_site_reads_a_deployment_field_off_the_folder():
    """The regression guard.

    Scoped PER FUNCTION on purpose: keying `load_policy` bindings by name across
    a whole module made one edit site's `pol` contaminate every other function
    that happened to reuse the name, and 22 false positives buried the real
    ones. A gate that cries wolf gets read as noise and then ignored when it is
    right.

    drift_monitor is exempt IN WRITING: it monitors, so a folder edit that no
    longer takes effect is more worth reporting, not less.
    """
    DEPLOYMENT_OWNED = set(S.DEPLOYMENT_OWNED) | {
        # derived reads of the same two switches
        "lock_is_active", "oversight_is_active",
    }
    EXEMPT = {"drift_monitor.py", "policy.py"}
    src = Path(__file__).resolve().parents[1] / "src" / "rvnd"
    offenders = []
    for f in src.rglob("*.py"):
        if "_quarantine" in str(f) or f.name in EXEMPT:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            bound = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Assign):
                    continue
                v = n.value
                # `pol = load_policy(...)` and also `self._loader = load_policy`
                # (the lazy-import form, where the call happens elsewhere).
                is_lp = ((isinstance(v, ast.Call)
                          and (getattr(v.func, "id", None) == "load_policy"
                               or getattr(v.func, "attr", None) == "load_policy"))
                         or (isinstance(v, ast.Name) and v.id == "load_policy"))
                if is_lp:
                    bound |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            if not bound:
                continue
            # An EDIT legitimately reads the folder's own declaration: it is
            # about to write that same file back.
            edits = any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "save_policy"
                        for c in ast.walk(fn))
            if edits:
                continue
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Attribute) and n.attr in DEPLOYMENT_OWNED):
                    continue
                v = n.value
                # `pol.oversight_enabled` where pol = load_policy(...)
                by_name = isinstance(v, ast.Name) and v.id in bound
                # `load_policy(x).oversight_enabled` -- no binding to key on.
                # The Solver governance seam read this way and the first version
                # of this guard walked straight past it.
                by_call = (isinstance(v, ast.Call)
                           and (getattr(v.func, "id", None) == "load_policy"
                                or getattr(v.func, "attr", None) == "load_policy"))
                if by_name or by_call:
                    offenders.append(
                        f"{f.name}:{n.lineno} {fn.name}() reads .{n.attr} off load_policy")
    assert not offenders, (
        f"these ask a FOLDER to decide what the deployment owns: {offenders}")


def test_the_regression_guard_can_actually_fire():
    """The positive control. A guard that only ever passes proves nothing --
    and this one had already been rewritten once, which is exactly when a check
    quietly stops checking."""
    bad = ast.parse("def f():\n p = load_policy(x)\n return p.privacy_lock_enabled\n")
    fn = bad.body[0]
    bound = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    hits = [n.attr for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id in bound and n.attr in S.DEPLOYMENT_OWNED]
    assert hits == ["privacy_lock_enabled"], (
        "the guard's own matching logic no longer detects a known-bad site")


# ===========================================================================
# The safeguards had to move WITH the decision, not be left at the old address
# ===========================================================================


@pytest.mark.parametrize("fn", [disable_lock_for_deployment,
                                disable_oversight_for_deployment])
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_disable_still_refuses_an_empty_acceptor(tmp_path, fn, blank):
    """The folder version refused this; the deployment version was written
    without it, so for a while the library accepted an unattributed disable and
    only the MCP handler's own check stood in the way."""
    with pytest.raises(ValueError):
        fn(accepted_by=blank, reason="scratch", log_root=tmp_path / "lr")


@pytest.mark.parametrize("call,change", [
    (lambda lr: disable_lock_for_deployment(accepted_by="alex", reason="r", log_root=lr),
     "lock_disabled"),
    (lambda lr: enable_lock_for_deployment(actor="alex", log_root=lr), "lock_enabled"),
    (lambda lr: disable_oversight_for_deployment(accepted_by="alex", reason="r", log_root=lr),
     "oversight_disabled"),
    (lambda lr: enable_oversight_for_deployment(actor="alex", log_root=lr),
     "oversight_enabled"),
])
def test_every_posture_change_is_audited(tmp_path, call, change):
    """Including the protective direction. Reconstructing WHEN protection was
    off needs both edges; an unaudited re-enable leaves the window open-ended."""
    from rvnd.mutation_log import MutationLog
    lr = tmp_path / "lr"
    call(lr)
    events = [e for e in MutationLog.for_deployment(lr).replay()
              if e.extra.get("policy_change") == change]
    assert len(events) == 1, f"{change} left no audit event"
    assert events[0].extra.get("scope") == "deployment"
    assert events[0].actor == "alex", "the change was not attributed"


def test_the_deployment_chain_is_not_a_folder_chain(tmp_path):
    """It must not be filed under a scope that did not make the decision."""
    from rvnd.mutation_log import DEPLOYMENT_LOG_ID, MutationLog
    lr = tmp_path / "lr"
    disable_lock_for_deployment(accepted_by="alex", log_root=lr)
    log = MutationLog.for_deployment(lr)
    assert log.folder_id == DEPLOYMENT_LOG_ID
    assert all(c in "0123456789abcdef" for c in DEPLOYMENT_LOG_ID) is False, (
        "the reserved id looks like a folder hash and could collide with one")
    assert log.log_file.exists()


@pytest.mark.live_deployment_root
def test_the_posture_follows_the_operators_log_root(tmp_path, monkeypatch):
    """A site with no log_root to pass must still read the operator's root.

    The Lock host's injected `l0_load_policy` hook has a fixed signature and
    gets no log_root, so if the fallback were the import-time default, an
    operator who moved the log root would have the gate consult a posture file
    they never configured -- reading full protection while the deployment had
    turned it off, or the reverse.
    """
    lr = tmp_path / "operator-root"
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(lr))

    disable_lock_for_deployment(accepted_by="alex", reason="test")  # no log_root

    assert (lr / DEPLOYMENT_POLICY_FILENAME).exists(), (
        "the posture was written somewhere other than the operator's log root")
    assert deployment_policy().lock_is_active is False
    assert effective_policy(tmp_path).lock_is_active is False, (
        "the read end did not follow the operator's root the write end used")


# ===========================================================================
# Ratcheted fields: a floor, not a flattening
# ===========================================================================


def test_a_folder_may_ask_for_more_restriction(tmp_path):
    """The first cut of the denylist took these outright, which silently broke
    six working setters -- a folder could no longer raise its own oversight or
    pick a stricter lock mode. Asking for MORE restriction over your own
    contents was never the risk the split was addressing."""
    from rvnd.policy import save_deployment_policy, set_oversight_level
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()
    save_deployment_policy(FolderPolicy(oversight_default_level="review"), lr)

    set_oversight_level(ws, "manual", log_root=lr)
    assert effective_policy(ws, log_root=lr).oversight_default_level == "manual"


def test_a_folder_may_not_ask_for_less(tmp_path):
    from rvnd.policy import save_deployment_policy, set_oversight_level
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()
    save_deployment_policy(FolderPolicy(oversight_default_level="approve"), lr)

    with pytest.raises(PolicyScopeError) as e:
        set_oversight_level(ws, "autonomous", log_root=lr)
    assert "floor" in str(e.value)
    assert effective_policy(ws, log_root=lr).oversight_default_level == "approve"


def test_a_lock_mode_may_not_be_dropped_to_off_by_a_folder(tmp_path):
    from rvnd.policy import (LOCK_MODE_CLEAN_ROOM, LOCK_MODE_OFF,
                             save_deployment_policy, set_lock_mode)
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()
    save_deployment_policy(FolderPolicy(lock_mode_explicit=LOCK_MODE_CLEAN_ROOM), lr)
    with pytest.raises(PolicyScopeError):
        set_lock_mode(ws, LOCK_MODE_OFF, accepted_by="alex", log_root=lr)


def test_an_unknown_value_does_not_outrank_the_floor(tmp_path):
    """A value missing from the strictness table must contribute nothing rather
    than win by accident -- otherwise a typo, or a field added later, silently
    beats the deployment."""
    assert S.strictest("oversight_default_level", "nonsense", "approve") == "approve"
    assert S.strictest("lock_mode_explicit", "", "clean_room") == "clean_room"
    assert S.weakens("oversight_default_level", "manual", "approve") is False
    assert S.weakens("oversight_default_level", "notify", "approve") is True


def test_a_folder_keeps_its_own_decision_matrix(tmp_path):
    """policy_matrix has ancestor inheritance of its own and is per-workspace by
    construction; sweeping it into the deployment's bucket disabled that."""
    from rvnd.policy_matrix import GRADES, OVERSIGHT, save_own_matrix
    lr = tmp_path / "lr"; lr.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    grid = {g: {o: "ask_user" for o in OVERSIGHT} for g in GRADES}
    save_own_matrix(ws, grid, log_root=lr)
    assert effective_policy(ws, log_root=lr).policy_matrix == grid


def test_an_edit_does_not_copy_the_deployments_fields_into_the_folder(tmp_path):
    """A read-modify-write site must read what it is about to write back.

    Reading the EFFECTIVE policy and saving it stamps the deployment's posture
    into the folder's file: a stale copy the resolver ignores, sitting in the
    one place someone might read it and believe it.
    """
    from rvnd.policy import POLICY_FILENAME, save_deployment_policy
    from rvnd.policy_matrix import GRADES, OVERSIGHT, save_own_matrix
    import json
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()
    disable_lock_for_deployment(accepted_by="alex", log_root=lr)

    save_own_matrix(ws, {g: {o: "ask_user" for o in OVERSIGHT} for g in GRADES},
                    log_root=lr)

    written = json.loads((ws / POLICY_FILENAME).read_text())
    assert "lock_disable" not in (written.get("acknowledgements") or {}), (
        "the deployment's disable acknowledgement was copied into the folder file")
    assert written.get("privacy_lock_enabled") is not False, (
        "the deployment's posture was stamped into the folder file")


def test_an_mcp_response_never_claims_protection_the_deployment_removed(tmp_path,
                                                                        monkeypatch):
    """The reply a user reads right after changing a setting is the worst place
    to report a folder's declaration as if it were the state in force."""
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "lr"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    ws = tmp_path / "ws"; ws.mkdir()

    disable_lock_for_deployment(accepted_by="alex", log_root=tmp_path / "lr")

    from rvnd import mcp_impl
    r = mcp_impl.policy_set_lock_mode(str(ws), "clean_room_with_algo",
                                      accepted_by="alex", reason="test")
    assert r.get("ok") is True, r
    assert r["lock_is_active"] is False, (
        "the response claimed the Lock was active while the deployment had it off")


def test_the_chokepoint_honours_the_deployments_oversight_floor(tmp_path, monkeypatch):
    """decide_action decides whether a human sees the action.

    It read the level off the folder, so a folder that declared nothing read as
    unset and fell through to a hardcoded "approve" -- serving less supervision
    than a deployment requiring "manual" had asked for. The ratchet only helps
    if the site consults it.
    """
    from rvnd.policy import save_deployment_policy
    from rvnd import governance
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(lr))
    save_deployment_policy(FolderPolicy(oversight_default_level="manual"), lr)

    assert governance._load_oversight(ws, log_root=lr) == "manual", (
        "the folder's silence was read as 'approve' instead of the "
        "deployment's floor")


def test_a_default_is_not_a_floor(tmp_path):
    """A deployment that declared nothing imposes nothing.

    `deployment_policy()` returns a full object whether or not a file exists,
    so the built-in `oversight_default_level="approve"` began acting as a floor
    on every install -- refusing folders that wanted a lower level on the
    strength of a value nobody had written. The floor has to come from the
    file, not from the dataclass.
    """
    from rvnd.policy import (deployment_declared, save_deployment_policy,
                             set_oversight_level)
    lr = tmp_path / "lr"
    ws = tmp_path / "ws"; ws.mkdir()

    assert deployment_declared(lr) == frozenset(), "an absent file declared fields"
    set_oversight_level(ws, "autonomous", log_root=lr)   # must not raise
    assert effective_policy(ws, log_root=lr).oversight_default_level == "autonomous"

    # Once the deployment DOES declare one, it binds.
    save_deployment_policy(FolderPolicy(oversight_default_level="approve"), lr)
    assert "oversight_default_level" in deployment_declared(lr)
    with pytest.raises(PolicyScopeError):
        set_oversight_level(ws, "autonomous", log_root=lr)

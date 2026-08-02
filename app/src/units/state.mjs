// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// The shared synchronous store — the one live loop the front-door widgets read
// from and write to. Membrane model: the focus is the inside; `outside` is its
// projection. Say/Build/Read subscribe here; any of them mutates the store and
// every surface re-renders from it in the same tick.
//
// Contract:
//  - View state only. The store holds the active workspace, a focus path for
//    descent into nested insides, the projected outside for that focus, the chat
//    context, and a version counter. It never decides governance — it reads
//    (console_snapshot, audit tail) and triggers refetches; every governed
//    write stays a gated op the caller routes through the bridge.
//  - Hydrated only from real reads. `project`/`hydrate` are the sole writers of
//    `outside`, both through the injected bridge `call`.
//  - A focus change re-projects before it notifies. setActiveWorkspace/setFocus
//    (and setState when it carries a focus key) refetch the outside and only
//    then publish, so no subscriber ever sees a new focus against a stale
//    outside.
//  - setState notifies synchronously, in the same tick, with a bumped version.

export function createStore(call) {
  const s = {
    activeWorkspace: null,   // the focused workspace path (the inside in focus)
    focusPath: [],           // descent into racks/child buses (empty = top)
    outside: null,           // projected read data for the current focus
    chatContext: { history: [], ledger: null },
    version: 0,              // bumps on every publish; subscribers diff on it
  };
  // The last console_snapshot rollup, kept privately so a focus change can
  // re-project the outside without re-reading the whole environment.
  let snapshot = { buses: [], count: 0, attention: [], attention_overflow: 0 };
  const subs = new Set();

  function getState() { return s; }

  function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }

  // The one publish primitive: bump the version and notify every subscriber
  // synchronously, in this tick. A snapshot of the set is taken so a subscriber
  // may unsubscribe (or subscribe) during the notification without skipping.
  function emit() {
    s.version++;
    for (const fn of Array.from(subs)) { try { fn(s); } catch (_e) { /* one bad subscriber never blocks the rest */ } }
  }

  // Rebuild `outside` wholesale from the last snapshot plus a fresh audit tail
  // for the current focus. Wholesale rebuild is what stops a prior workspace's
  // data leaking across a focus switch — the outside is never merged forward.
  async function project() {
    const buses = Array.isArray(snapshot.buses) ? snapshot.buses : [];
    const fc = s.activeWorkspace;
    const bus = fc ? (buses.find((b) => b.path === fc) || {}) : {};
    let happened = [];
    if (fc) {
      const tail = await call('workspace_audit', { op: 'tail', params: { folder_context: fc, limit: 3 } });
      happened = (tail && Array.isArray(tail.events)) ? tail.events : [];
    }
    s.outside = {
      count: (snapshot.count != null) ? snapshot.count : buses.length,
      attention: snapshot.attention || [],
      attention_overflow: snapshot.attention_overflow || 0,
      buses,
      bus,
      happened,
    };
  }

  // Read the environment rollup, pick a default focus if none is set, project
  // its outside, then publish. The only entry that refreshes the snapshot.
  async function hydrate() {
    const r = await call('workspace_workflow', { op: 'console_snapshot', params: {} });
    const ok = r && Array.isArray(r.buses);
    snapshot = ok
      ? r
      : { buses: [], count: 0, attention: [], attention_overflow: 0 };
    // A refreshed inventory is authoritative — but only when the call actually
    // returned one. Re-point the focus when the snapshot no longer lists it, so
    // no centre unit queries a vanished workspace while the header says "0
    // workspaces". On a failed snapshot (no buses array) keep the current focus
    // rather than dropping the user's place on a transient error.
    if (ok && !snapshot.buses.some((b) => b.path === s.activeWorkspace)) {
      s.activeWorkspace = snapshot.buses.length ? snapshot.buses[0].path : null;
      s.focusPath = [];
    }
    await project();
    emit();
  }

  // Move the focus, re-project its outside via the bridge, then publish once.
  // `extra` merges any non-focus view state in the same commit.
  async function _setFocus(workspace, path, extra) {
    s.activeWorkspace = workspace;
    s.focusPath = Array.isArray(path) ? path : [];
    if (extra) for (const k in extra) if (k !== 'activeWorkspace' && k !== 'focusPath') s[k] = extra[k];
    await project();                // refetch the outside BEFORE any subscriber sees the new focus
    emit();
  }

  function setActiveWorkspace(workspace, path = []) { return _setFocus(workspace, path, null); }
  function setFocus(workspace, path = []) { return _setFocus(workspace, path, null); }

  // Shallow-merge view state and publish. A partial that changes the focus
  // (activeWorkspace or focusPath) routes through the re-project path so the
  // outside can never lag the focus, whichever entry point a widget uses; a
  // pure view-state partial notifies synchronously in this tick.
  function setState(partial) {
    partial = partial || {};
    if (('activeWorkspace' in partial) || ('focusPath' in partial)) {
      return _setFocus(
        ('activeWorkspace' in partial) ? partial.activeWorkspace : s.activeWorkspace,
        ('focusPath' in partial) ? partial.focusPath : s.focusPath,
        partial,
      );
    }
    Object.assign(s, partial);
    emit();
    return Promise.resolve();
  }

  return { getState, setState, subscribe, hydrate, project, setActiveWorkspace, setFocus };
}

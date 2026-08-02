/* The Transport bar — one Play/Pause run-state toggle for the focused
   workspace (Running = its agents may act; Held = suspended). Resuming
   loosens governance, so it confirms + records (never a silent loosen);
   holding tightens, so it's instant + recorded. No Stop here — the rail's
   master All-Stop is the single global stop; the toggle just reflects the
   live state. Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. */
// transport ▶ Resume — un-suspend the focused workspace's agents. This LOOSENS
// governance, so it confirms + records (never a silent loosen).
// Run-state — one Play/Pause state toggle for the focused workspace. Running = its
// agents may act; Held = suspended. Resuming loosens (confirm + record); holding
// tightens (instant + record). No Stop here — the rail's master ■ All-Stop is the
// single global stop. The button reflects the live state (updateRunState).
async function transportToggle(){
  if(!S.path){ announce('open a workspace first'); return; }
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(e){ announce('could not read run state: '+((e&&e.message)||'failed')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('could not read run state: '+esc(pl.error||'failed')); return; }
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active').length;
  if(active>0) return transportHold();      // Running → Hold (pause)
  return transportResume();                  // Held → Run (resume, confirms)
}
async function updateRunState(){
  const b=document.getElementById('trplay'); if(!b) return;
  const lbl=document.getElementById('trplaylbl');
  if(!S.path){ b.classList.remove('on'); b.disabled=true; if(lbl)lbl.textContent='Run'; b.setAttribute('aria-pressed','false'); b.setAttribute('aria-label','run state — open a workspace'); return; }
  b.disabled=false;
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(_){ return; }
  if(pl&&(pl.error||pl.ok===false)) return;
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active').length; const tot=arr.length;
  const running=active>0;
  b.classList.toggle('on',running); b.setAttribute('aria-pressed',String(running));
  if(lbl) lbl.textContent=running?'Running':'Held';
  b.setAttribute('aria-label','run state — '+(running?(active+' of '+tot+' agent'+(tot===1?'':'s')+' active; click to hold'):'agents held; click to resume (loosens, asks first)'));
}
async function transportResume(){
  if(!S.path){ announce('open a workspace first'); return; }
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(e){ announce('could not read “'+bn(S.path)+'” parties: '+((e&&e.message)||'failed')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('could not read “'+bn(S.path)+'” parties: '+esc(pl.error||'failed')); return; }
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const susp=arr.filter(p=>(p.status||'active')==='suspended');
  if(!susp.length){ announce('“'+bn(S.path)+'” has no suspended agent to resume'); return; }
  if(!confirm('Resume '+susp.length+' suspended agent'+(susp.length===1?'':'s')+' in “'+bn(S.path)+'”? This loosens governance — recorded.')) return;
  let n=0,fail=0; for(const p of susp){ const pid=p.party_id||p.id; if(!pid){ fail++; continue; } try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:S.path,party_id:pid,status:'active',actor:'app-user',reason:'transport resume'}}); n++; }catch(_){ fail++; } }
  await reload(); await loadWsRail();
  announce('Resumed '+n+' of '+susp.length+' agent(s) in '+bn(S.path)+(fail?(' · '+fail+' could not resume'):''));
}
// transport ⏸ Hold — suspend the focused workspace's active agents. The safe
// direction (tighten), so it's instant + recorded, and reversible with ▶.
async function transportHold(){
  if(!S.path){ announce('open a workspace first'); return; }
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(e){ announce('could not read “'+bn(S.path)+'” parties: '+((e&&e.message)||'failed')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('could not read “'+bn(S.path)+'” parties: '+esc(pl.error||'failed')); return; }
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active');
  if(!active.length){ announce('“'+bn(S.path)+'” has no active agent to hold'); return; }
  let n=0,fail=0; for(const p of active){ const pid=p.party_id||p.id; if(!pid){ fail++; continue; } try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:S.path,party_id:pid,status:'suspended',actor:'app-user',reason:'transport hold'}}); n++; }catch(_){ fail++; } }
  await reload(); await loadWsRail();
  announce('Held '+bn(S.path)+' — suspended '+n+' of '+active.length+' agent(s)'+(fail?(' · '+fail+' failed'):'')+(n?' · ▶ to resume':''));
}

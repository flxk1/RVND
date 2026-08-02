/* Workspaces rail — the channel strips shown per workspace: view-solo/govern-
   solo (wsSolo/_updateIsolate/wsIsolate), the rail render (loadWsRail), saving
   an unsaved focused folder to the rail (saveWs), focusing a strip (focusWs),
   muting a strip's active agents (wsMute), and the master All-Stop spanning
   every workspace (allStopAll). Shell chrome, not plane-provided content:
   loaded unconditionally by compose_classic(), no panel-mount registration.
   _OVBANDS, _normp, _samePath, _parentOf, _ledStates, _ledLabel and markWsRail
   stay in app/src/index.html: _samePath and the LED helpers are shared with
   the ARRANGE view (already extracted to app/src/shell/arrange.js) and with
   other index.html code, and markWsRail is called from index.html's own
   reload(). */
// view-solo: focus a workspace, dim the others — purely visual, no server, no governance change
function wsSolo(i){ const w=(window._wsrail||[])[i]; if(!w) return; window._solo=window._solo||new Set();
  if(window._solo.has(w.path)) window._solo.delete(w.path); else window._solo.add(w.path);
  markWsRail(); announce((window._solo.has(w.path)?'Soloed ':'Un-soloed ')+bn(w.path)+' — view only'); }
function _updateIsolate(){ const b=document.getElementById('wsisolate'); if(!b) return; b.style.display=(window._solo&&window._solo.size)?'block':'none'; }
// govern-solo: suspend every active agent OUTSIDE the soloed workspace(s) — the safe
// direction (tighten), confirmed + recorded; honest partial-failure report.
async function wsIsolate(){
  const solo=window._solo||new Set(); if(!solo.size){ announce('nothing soloed'); return; }
  const others=(window._wsrail||[]).filter(w=>!solo.has(w.path));
  if(!others.length){ announce('no other workspace to suspend'); return; }
  if(!confirm('Isolate the soloed workspace'+(solo.size===1?'':'s')+' — suspend every active agent in the other '+others.length+' workspace'+(others.length===1?'':'s')+'? Resume from Govern → Protections. (recorded)')) return;
  let done=0,scope=0,readFail=0,suspendFail=0;
  for(const w of others){ let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:w.path}}); }catch(_){ readFail++; continue; }
    if(pl&&(pl.error||pl.ok===false)){ readFail++; continue; }
    const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active');
    for(const p of active){ const pid=p.party_id||p.id; if(!pid){ suspendFail++; continue; } scope++; try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:w.path,party_id:pid,status:'suspended',actor:'app-user',reason:'solo-isolate'}}); done++; }catch(_){ suspendFail++; } } }
  await reload(); await loadWsRail();
  announce('Isolated — suspended '+done+' of '+scope+' agent(s) across '+others.length+' other workspace'+(others.length===1?'':'s')+(suspendFail?(' · '+suspendFail+' failed'):'')+(readFail?(' · '+readFail+' unreadable'):''));
}
async function loadWsRail(){
  const wrap=document.getElementById('wschans'); if(!wrap) return;
  const cta='<button class="wsadd" onclick="openWorkspacePanel()" aria-label="add a workspace">+ add workspace</button>';
  let r; try{ r=await tool('workspace_workspace',{op:'list'}); }catch(e){ wrap.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-bottom:6px">couldn’t reach the server</div>'+cta; return; }
  const ws=((r&&r.workspaces)||[]).slice();
  // Always carry a channel for the workspace open on the canvas, even if it was
  // never saved (workspace_workspace add) — otherwise the rail can read "empty" while
  // the canvas shows that folder's agents. The unsaved one gets a "save to rail".
  if(S.path && !ws.some(w=>_samePath(w.path,S.path))) ws.unshift({path:S.path,unsaved:true});
  window._wsrail=ws;
  if(!ws.length){ wrap.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-bottom:6px">no workspaces yet</div>'+cta; return; }
  // one slow/unreachable workspace must not hang the whole rail (allSettled per read)
  const cells=await Promise.all(ws.map(async w=>{ const o={path:w.path,unsaved:!!w.unsaved};
    const [s,pl,mx]=await Promise.allSettled([
      tool('workspace_policy',{op:'snapshot',params:{folder_context:w.path}}),
      tool('workspace_policy',{op:'party_list',params:{folder_context:w.path}}),
      tool('workspace_matrix',{op:'show',params:{folder_context:w.path}})]);
    if(s.status==='fulfilled'&&s.value&&!s.value.error){ o.ov=s.value.oversight_default_level; } else { o.ovErr=true; }
    if(pl.status==='fulfilled'){ const v=pl.value; if(!(v&&(v.error||v.ok===false))){ const a=Array.isArray(v)?v:((v&&(v.parties||v.rows))||[]); o.tot=a.length; o.act=a.filter(p=>(p.status||'active')==='active').length; } else { o.partiesErr=true; } } else { o.partiesErr=true; }
    if(mx.status==='fulfilled'&&mx.value&&!mx.value.error&&mx.value.matrix){ o.leds=_ledStates(mx.value.matrix,o.ov); } else { o.matrixErr=true; }
    o.parent=_parentOf(w.path,ws); o.kids=ws.filter(x=>_samePath((_parentOf(x.path,ws)||{}).path,w.path)).length;
    return o; }));
  wrap.innerHTML=cells.map((c,i)=>{ const here=_samePath(c.path,S.path); const lc=c.ov?'#92c4ac':'#5b6472';
    const ovtext=c.ov?esc(c.ov):(c.ovErr?'oversight unread':'no oversight set');
    const leds=(c.leds||['block','block','block','block','block']);
    const ledHtml='<div class="wsleds" role="img" aria-label="'+escA(c.matrixErr?'autonomy unreadable — could not read the matrix':_ledLabel(leds))+'"'+(c.matrixErr?' style="opacity:.4"':'')+'>'+leds.map((s,gi)=>'<span class="wsled'+(s==='go'?' go':(s==='ask'?' ask':''))+'" title="L'+gi+': '+s+'"></span>').join('')+'</div>'+(c.matrixErr?'<div class="wsgroup" style="color:#c79">autonomy unread</div>':'');
    const grp=c.parent?('<div class="wsgroup">⮡ group: '+esc(bn(c.parent.path))+'</div>'):'';
    const snd=c.kids?('<div class="wssend">send→ '+c.kids+' workspace'+(c.kids===1?'':'s')+'</div>'):'';
    const uns=c.unsaved?('<div class="wsgroup"><button class="wsadd" style="padding:2px 6px;font-size:8.5px" data-save="'+i+'">open on canvas · unsaved — + save to rail</button></div>'):'';
    const soloed=!!(window._solo&&window._solo.has(c.path)); const dimmed=!!(window._solo&&window._solo.size&&!soloed);
    return '<div class="wschan'+(here?' on':'')+(soloed?' solo':'')+(dimmed?' dim':'')+'" data-i="'+i+'" role="group" aria-label="workspace '+escA(esc(bn(c.path)))+(here?' (current)':'')+'">'
      +'<div class="wstop"><button class="wsname" data-focus="'+i+'" aria-label="focus '+escA(esc(bn(c.path)))+(here?' (current)':'')+'">'+esc(bn(c.path))+'</button>'
      +'<button class="wsolo'+(soloed?' on':'')+'" data-solo="'+i+'" aria-pressed="'+soloed+'" title="solo — focus this, dim the others (view only)" aria-label="solo '+escA(esc(bn(c.path)))+' — focus it and dim the others (view only, no governance effect)">S</button>'
      +'<button class="wmute" data-mute="'+i+'" title="mute — suspend this workspace’s active agents" aria-label="mute '+escA(esc(bn(c.path)))+' — suspend its active agents">M</button></div>'
      +ledHtml
      +'<div class="wm"><span class="wslamp" style="background:'+lc+'"></span>'+ovtext+(c.tot!=null?(' · '+esc(c.act)+'/'+esc(c.tot)+' agents'):(c.partiesErr?' · agents unread':''))+'</div>'
      +grp+snd+uns+'</div>'; }).join('')+cta;
  wrap.querySelectorAll('.wsname').forEach(b=>b.addEventListener('click',()=>focusWs(+b.dataset.focus)));
  wrap.querySelectorAll('.wmute').forEach(m=>m.addEventListener('click',ev=>{ ev.stopPropagation(); wsMute(+m.dataset.mute); }));
  wrap.querySelectorAll('.wsolo').forEach(b=>b.addEventListener('click',ev=>{ ev.stopPropagation(); wsSolo(+b.dataset.solo); }));
  wrap.querySelectorAll('[data-save]').forEach(b=>b.addEventListener('click',ev=>{ ev.stopPropagation(); saveWs(+b.dataset.save); }));
  _updateIsolate();
}
async function saveWs(i){ const w=(window._wsrail||[])[i]; if(!w) return;
  let r; try{ r=await tool('workspace_workspace',{op:'add',params:{folder_context:w.path}}); }catch(e){ announce('Could not save “'+bn(w.path)+'”: '+((e&&e.message)||'failed')); return; }
  if(r&&(r.error||r.ok===false)){ announce('Could not save “'+bn(w.path)+'”: '+esc(r.error||'failed')); return; }
  announce('Saved “'+bn(w.path)+'” to the rail'); await loadWsRail();
}
function focusWs(i){ const w=(window._wsrail||[])[i]; if(!w||_samePath(w.path,S.path)){ if(w) announce('already focused: '+bn(w.path)); return; }
  const sel=document.getElementById('folder'); if(sel){ const oi=[...sel.options].findIndex(o=>o.value===w.path||o.textContent===bn(w.path)); if(oi>=0){ window._fi=oi; sel.selectedIndex=oi; } }
  S.path=w.path; boot(); }
async function wsMute(i){
  const w=(window._wsrail||[])[i]; if(!w) return;
  // read the live party list FIRST so the confirm states a true count and we never fake success
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:w.path}}); }catch(e){ announce('Could not read “'+bn(w.path)+'” parties: '+((e&&e.message)||'failed')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('Could not read “'+bn(w.path)+'” parties: '+esc(pl.error||'failed')); return; }
  const a=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=a.filter(p=>(p.status||'active')==='active');
  if(!active.length){ announce('“'+bn(w.path)+'” has no active agent to mute'); return; }
  if(!confirm('Mute “'+bn(w.path)+'” — suspend its '+active.length+' active agent'+(active.length===1?'':'s')+'? Resume them from Govern → Protections. (recorded)')) return;
  let n=0,fail=0; for(const p of active){ const pid=p.party_id||p.id; if(!pid){ fail++; continue; } try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:w.path,party_id:pid,status:'suspended',actor:'app-user',reason:'workspace mute'}}); n++; }catch(_){ fail++; } }
  await loadWsRail();
  announce('Muted '+bn(w.path)+' — suspended '+n+' of '+active.length+' agent(s)'+(fail?(' · '+fail+' could not be suspended'):''));
  // re-find the strip by PATH (loadWsRail rebuilt the rail; the old index may now point elsewhere)
  const nb=[...document.querySelectorAll('.wschan .wsname')].find(b=>bn(b.textContent||'')===bn(w.path)); if(nb&&nb.focus){ try{nb.focus();}catch(_){} }
}
// Master All-Stop (the rail's red bar) — spans EVERY workspace, asks first, and
// reports the true outcome (never fakes success; partial failures surfaced).
async function allStopAll(){
  const ws=(window._wsrail||[]).slice(); if(!ws.length){ announce('no workspaces to stop'); return; }
  if(!confirm('MASTER ALL-STOP — suspend every active agent across all '+ws.length+' workspace'+(ws.length===1?'':'s')+'? Resume them from Govern → Protections. (recorded)')) return;
  let done=0,scope=0,readFail=0,suspendFail=0;
  for(const w of ws){ let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:w.path}}); }catch(_){ readFail++; continue; }
    if(pl&&(pl.error||pl.ok===false)){ readFail++; continue; }
    const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active');
    for(const p of active){ const pid=p.party_id||p.id; if(!pid){ suspendFail++; continue; } scope++; try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:w.path,party_id:pid,status:'suspended',actor:'app-user',reason:'master ALL-STOP'}}); done++; }catch(_){ suspendFail++; } } }
  await reload(); await loadWsRail();
  announce('Master ALL-STOP — suspended '+done+' of '+scope+' active agent(s) across '+ws.length+' workspace'+(ws.length===1?'':'s')+(suspendFail?(' · '+suspendFail+' failed'):'')+(readFail?(' · '+readFail+' workspace(s) unreadable'):''));
}

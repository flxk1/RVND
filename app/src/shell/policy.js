/* Policy-ingest cluster — per-step sign-off override (overrideStep), the
   read-only query bar (runQuery), boundary-port registration (addConnector),
   and P4 "Ingest a policy → digital twin" (openPolicyPanel/ingestPolicy/
   renderTwin/applyTwin). Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. The
   policy drawer calls the shared draft-persistence helpers (draftPanelClosed,
   draftChipHtml, draftLoaded, draftQueue, draftDiscard, draftChipMount) and
   modalize as globals defined elsewhere in the shell — they stay in
   app/src/index.html, shared with the other drafted surfaces (map, chat,
   cards) and every other modal drawer. */
async function overrideStep(uc,issueId,value){
  const out=document.querySelector('.sovr[data-r="'+issueId+'"]'); if(out)out.textContent='…';
  try{ await tool('workspace_audit',{op:'record_override',params:{folder_context:S.path,card:{issue_id:issueId,stage:'decision',node_id:issueId},actor:'app-user',field:'disposition',new_value:value,rationale:'decided in patchbay'}}); if(out)out.textContent='✓ '+value+' · signed'; }
  catch(e){ if(out)out.textContent='err'; }
}
/* the query bar — ask the patch the governance questions a spreadsheet can't
   (governance_query). Read-only; results float over the canvas. */
async function runQuery(name){
  if(!name) return;
  let out=document.getElementById('queryout');
  if(!out){ out=document.createElement('div'); out.id='queryout'; out.setAttribute('role','status'); out.setAttribute('aria-live','polite'); out.style.cssText='position:absolute;top:12px;left:12px;max-width:440px;max-height:72%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;z-index:9;box-shadow:0 8px 30px rgba(0,0,0,.5)'; stage.appendChild(out); }
  out.innerHTML='<b style="font-family:Space Grotesk,sans-serif;font-size:12px">'+esc(name)+'</b><div class="muted" style="font-size:11px;margin-top:6px">querying…</div>';
  let r;
  try{ r = S.path ? await tool('workspace_workflow',{op:'governance_query',params:{folder_context:S.path,query:name}}) : {rows:[],count:0,_offline:true}; }
  catch(e){ out.innerHTML='<div class="ro">err: '+esc(e.message)+'</div>'; return; }
  let h='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:12px">'+esc(name)+'</b><span class="muted" style="font-size:10px">'+(r.count||0)+' result'+(r.count===1?'':'s')+'</span><span style="flex:1"></span><span id="qx" role="button" tabindex="0" aria-label="close query results" title="close (Esc)" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>';
  if(r._offline) h+='<div class="muted" style="font-size:11px;margin-top:6px">open a folder for live queries</div>';
  else if(!r.rows||!r.rows.length) h+='<div class="ro" style="margin-top:6px;color:#7fae97">✓ none</div>';
  else h+='<div style="margin-top:6px">'+r.rows.map(row=>'<div style="border-top:1px solid var(--line);padding:5px 0;font-size:11px;color:var(--txt)">'+Object.entries(row).map(([k,v])=>'<span class="muted">'+esc(k)+':</span> '+esc(v)).join(' · ')+'</div>').join('')+'</div>';
  out.innerHTML=h;
  // D13 — the panel must be dismissable by keyboard (WCAG 2.1.1): the ✕ is a
  // real button (Enter/Space) and Escape closes the panel from anywhere in it,
  // restoring focus to the canvas.
  // non-modal results popover: dismissable (✕ / Esc) + returns focus to the query
  // selector that opened it. Deliberately NOT a focus-trapped dialog — it's a live
  // region the operator reads then dismisses, not a task that owns focus.
  const close=()=>{ out.remove(); const q=document.getElementById('qsel'); const st=document.getElementById('stage'); const back=(q&&q.focus)?q:st; if(back&&back.focus){try{back.focus();}catch(_){}} };
  const x=out.querySelector('#qx');
  if(x){ x.addEventListener('click',close); x.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();} }); }
  out.setAttribute('tabindex','-1');
  out.addEventListener('keydown',ev=>{ if(ev.key==='Escape'){ev.preventDefault();close();} });
  if(out.focus){ try{out.focus();}catch(_){} }
}
/* connectors — boundary ports (task spine). Concrete send is permissioned; this
   registers the port + link on the chain (connector_register). */
async function addConnector(role, channel, ucId){
  const id=role.slice(0,3)+'-'+(Date.now()%100000);
  const ucs=ucId?[ucId.replace(/^uc:/,'')]:[];
  if(S.path){ await tool('workspace_workflow',{op:'connector_register',params:{folder_context:S.path,connector_id:id,role,channel,use_cases:ucs,actor:'app-user'}}); await reload(); }
  else { S.g.nodes.push({id:'conn:'+id,kind:'connector',role,channel,label:id});
    if(role==='ingress'&&ucId) S.g.edges.push({from:'conn:'+id,to:ucId,kind:'ingress'});
    else if(role==='oversight'&&ucId) S.g.edges.push({from:ucId,to:'conn:'+id,kind:'notify'});
    else if(role==='egress') S.g.edges.push({from:'master',to:'conn:'+id,kind:'deliver'});
    render(); }
}
/* P4 — policy ingest → digital twin. Paste a policy; Rvnd drafts a v0.5 twin
   (declares what it can, hands the rest to the host) and applies NOTHING until
   you confirm. The confirm is the auto-instrumented write (via patch_apply). */
async function openPolicyPanel(){
  let pp=document.getElementById('policypanel');
  if(pp){ draftPanelClosed('policy_paste'); pp.remove(); return; }
  const opener=document.activeElement;
  pp=document.createElement('div'); pp.id='policypanel'; pp.setAttribute('role','dialog'); pp.setAttribute('aria-label','Ingest an AI policy');
  pp.style.cssText='position:absolute;top:12px;left:12px;width:460px;max-width:92%;max-height:84%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  pp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Ingest a policy → digital twin</b>'+draftChipHtml('policy_paste')+'<span style="flex:1"></span><span id="ppx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">Paste your AI policy. Rvnd drafts a governance twin — it declares what it can, hands the rest to the host, and applies nothing until you confirm.</div>'
    +'<textarea id="pptext" aria-label="Policy text" placeholder="e.g. Automated decisions must be reviewed by a compliance officer." style="width:100%;height:120px;background:var(--panel-2);border:1px solid var(--line);color:#fff;font-family:IBM Plex Mono,monospace;font-size:12px;border-radius:6px;padding:8px"></textarea>'
    +'<label style="display:block;font-size:10.5px;color:var(--txt-dim);margin-top:6px"><input type="checkbox" id="ppllm"> draft with the local model when one is capable — degrades to the deterministic extractor and says so</label>'
    +'<div style="display:flex;gap:6px;margin-top:8px"><button class="del" id="ppbuild" style="border-color:#3a3357;color:#b9acff;flex:1">Build twin</button><button class="del" id="ppdiscard" title="delete this panel\'s saved draft" style="border-color:#5a2f2a;color:#e6b0aa">Discard draft</button></div>'
    +'<div id="ppout" role="status" aria-live="polite" style="margin-top:10px"></div>';
  stage.appendChild(pp);
  const restore=modalize(pp,opener);
  const closeP=()=>{draftPanelClosed('policy_paste');pp.remove();restore();}; pp._close=closeP;   // so applyTwin can close + restore focus
  const x=pp.querySelector('#ppx'); x.addEventListener('click',closeP);
  x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();closeP();}});
  pp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();closeP();}});
  pp.querySelector('#ppbuild').addEventListener('click',()=>ingestPolicy());
  // silent prefill from the rehydrated draft; every edit re-saves, debounced
  const ppta=pp.querySelector('#pptext'); const ppd=draftLoaded('policy_paste');
  if(ppd&&typeof ppd.text==='string'&&!ppta.value) ppta.value=ppd.text;
  ppta.addEventListener('input',()=>draftQueue('policy_paste',()=>({text:ppta.value})));
  pp.querySelector('#ppdiscard').addEventListener('click',()=>{ ppta.value=''; draftDiscard('policy_paste'); });
  draftChipMount('policy_paste');
}
async function ingestPolicy(text){
  const ta=document.getElementById('pptext'); const policy=(text!=null?text:(ta?ta.value:''));
  const out=document.getElementById('ppout'); if(out)out.innerHTML='<div class="ro" style="font-size:11px;color:var(--txt-dim)">drafting…</div>';
  let r;
  const llm=document.getElementById('ppllm');
  const params={folder_context:S.path||'',policy_text:policy};
  if(llm&&llm.checked) params.use_llm=true;   // by choice — the capability gate degrades declared, never silent
  try{ r=await tool('workspace_workflow',{op:'policy_ingest',params:params}); }
  catch(e){ if(out)out.innerHTML='<div class="ro">err: '+esc(e.message)+'</div>'; return null; }
  window._twin=r;
  if(out){ out.innerHTML=renderTwin(r); const ab=document.getElementById('ppapply'); if(ab) ab.addEventListener('click',()=>applyTwin()); }
  return r;
}
function renderTwin(r){
  if(!r||!r.ok) return '<div class="finding bad"><span class="ttl">Could not build a twin</span>'+esc(((r&&r.errors)||['unknown']).join('; '))+'</div>';
  const c=r.classification||{}; const li=a=>(a&&a.length)?a.map(x=>'<li>'+esc(x)+'</li>').join(''):'<li style="color:var(--txt-dim)">none</li>';
  let h='<div class="finding ok"><span class="ttl">✓ draft twin — not yet applied</span>'+esc(r.note||'')+'</div>';
  // the ambient-model gate reports a degrade instead of silently skipping the model
  if(r.capability&&r.capability.capable===false) h+='<div class="finding warn"><span class="ttl">Drafted without the local model</span>'+esc(r.capability.reason||'no capable model registered')+' — the draft used the deterministic extractor.</div>';
  h+='<label>Express — in the governance graph ('+((c.express||[]).length)+')</label><ul style="margin:4px 0 8px 16px;font-size:11px">'+li(c.express)+'</ul>';
  h+='<label>Host hand-offs — the runtime must do these ('+((r.host_handoffs||[]).length)+')</label><ul style="margin:4px 0 8px 16px;font-size:11px;color:#e6b483">'+li(r.host_handoffs)+'</ul>';
  h+='<label>Policy values — to confirm ('+((c.policy||[]).length)+')</label><ul style="margin:4px 0 8px 16px;font-size:11px;color:var(--txt-dim)">'+li(c.policy)+'</ul>';
  if((c.unmapped||[]).length) h+='<label>Unmapped — review ('+c.unmapped.length+')</label><ul style="margin:4px 0 8px 16px;font-size:11px;color:var(--txt-dim)">'+li(c.unmapped)+'</ul>';
  h+='<details style="margin:6px 0"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">.lg netlist</summary><pre style="white-space:pre-wrap;font-size:10.5px;color:#cdd2dc;background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:8px">'+esc(r.netlist||'')+'</pre></details>';
  h+='<button class="del" id="ppapply" style="border-color:#2f4a3a;color:#8fd1ad;width:100%;margin-top:6px"'+(S.path?'':' disabled title="open a folder to apply"')+'>Confirm &amp; apply to the chain</button>';
  return h;
}
async function applyTwin(){
  const r=window._twin; if(!r||!r.ok||!S.path) return;
  const out=document.getElementById('ppout');
  let res;
  try{ res=await tool('workspace_workflow',{op:'patch_apply',params:{folder_context:S.path,actor:'app-user',netlist:r.netlist}}); }
  catch(e){ res={ok:false,errors:[e.message]}; }
  if(!res||res.ok===false){ if(out)out.insertAdjacentHTML('afterbegin','<div class="finding bad"><span class="ttl">Apply failed — nothing written</span>'+esc(((res&&res.errors)||['unknown']).join('; '))+'</div>'); return; }
  // the commit act completed the paste — a kept draft would resurrect on the
  // next open and read stale against the new chain tip (addNode's rationale)
  try{ await draftDiscard('policy_paste'); }catch(_){}
  const pp=document.getElementById('policypanel'); if(pp){ if(pp._close)pp._close(); else pp.remove(); }
  await reload();
}

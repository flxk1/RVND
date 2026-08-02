/* The PATCH canvas — the console's central rendering engine. render() draws
   every node and cord from S.g/S.sel/S.selEdge onto the SVG stage and is the
   thing every other panel and every other shell bundle ultimately waits on;
   inspect() builds the per-node Inspector body that fillTrackStrip
   (track_strip.js) and the ARRANGE mix view (arrange.js) key off. wire()/
   unwire() grant and revoke an authority cord; promptCreate()/addNode() are
   the toolbar's node-creation flow — both hand back into render()/inspect()
   once a node is named. Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration.

   The verdict-router node kind rendered inline inside render() (the
   DEST/VN/ORDER/live/ACT table) is one of the five embedded governance-panel
   behaviors confirmed elsewhere in this project as deliberate traps, not
   separable panels — moving render() here relocates the file it lives in,
   nothing about what it tests.

   layout/drawCords/cordPath/cordSeg/pos/setpos/byId/out/inn/effVerdict/
   allowedFor/drag and the findings/federatedCheck/inspectEdge/selectEdge/
   reservationsHtml/tagsHtml/declRows/declAddHtml/addDeclaration/stopCard/
   bindStopCards family all stay in index.html: each is called from code
   outside this cluster too (the wiring-interaction drag/drop section, the
   ARRANGE and track-strip renders, or other Inspector helpers), so pulling
   them out here would relocate the sharing problem rather than resolve it —
   the same discipline arrange.js used for _TS_STATUS/_tsGrade. */
function render(){
  // re-inject client-only Verdict Router nodes wiped by a server reload (visualize-only)
  if(window._routers&&window._routers.length&&S.g&&S.g.nodes){ for(const r of window._routers){ if(!S.g.nodes.some(n=>n.id===r.id)) S.g.nodes.push(r); } }
  layout();
  [...stage.querySelectorAll('.node')].forEach(el=>el.remove());
  drawCords();
  // legibility: first-run empty hint + a persistent sample/offline watermark so an
  // operator never mistakes the demo patch for the real signed record (no silent data loss).
  stage.querySelectorAll('#stagehint,#stagewm').forEach(x=>x.remove());
  const hasWork=S.g.nodes.some(n=>n.kind==='agent'||n.kind==='human'||n.kind==='use_case');
  if(!hasWork){ const hint=document.createElement('div'); hint.id='stagehint'; hint.setAttribute('role','status'); hint.setAttribute('aria-live','polite');
    const steps=(LIVE&&S.path)?'<div style="text-align:left;margin-top:8px;font-size:11.5px"><div>1 · <b>Set up → Agent</b> — who acts</div><div>2 · <b>Set up → Task</b> — a governed action</div><div>3 · drag the agent’s right dot onto the task’s left dot — authority, signed into the record</div><div>4 · <b>Rules → Autonomy</b> — go / ask / block per cell</div></div><div style="margin-top:6px;font-size:10.5px">the <b>?</b> button lists everything this console can ask of the server</div>':'';
    hint.innerHTML='<div class="card">'+(steps?'This workspace is empty — get started:':'This patch is empty. Add an <b>Agent</b> and a <b>Task</b> from the toolbar, then drag the agent’s right dot onto the task’s left dot to grant authority — every grant is signed into the record.')+steps+'</div>'; stage.appendChild(hint); }
  if(S.g._sample){ const wm=document.createElement('div'); wm.id='stagewm'; wm.textContent='DEMO PATCH · not the signed record — add a workspace in the sidebar'; stage.appendChild(wm); }
  // reserved/prohibited at the boundary -> the master node carries the reserved treatment
  const boundaryReserved=S.g.edges.some(e=>e.kind==='egress'&&['reserved','prohibited'].includes(effVerdict(e)));
  for(const n of S.g.nodes){
    if(!pos(n.id)) continue;
    const el=document.createElement('div'); el.className='node '+n.kind+(S.sel===n.id?' sel':'');
    if(n.kind==='master'&&boundaryReserved) el.className+=' reserved';
    const p=pos(n.id); el.style.left=p.x+'px'; el.style.top=p.y+'px'; el.dataset.id=n.id;
    const reserved=n.kind==='use_case'&&n.reserved&&n.reserved.length;
    const _ev=n.kind==='use_case'?S.g.edges.find(e=>e.kind==='egress'&&e.from===n.id):null;
    const verdict=_ev?effVerdict(_ev):null;   // single source — the resolved verdict, never the raw one
    if(reserved||verdict==='reserved'||verdict==='prohibited') el.classList.add('bad');
    if(n.kind==='agent'&&(n.status==='killed'||n.status==='suspended')) el.classList.add('killed');
    let strip='#cbc8be';
    if(n.kind==='agent') strip=n.status&&n.status!=='active'?'#9a6f6a':'var(--human)';
    else if(n.kind==='human') strip='var(--human)';
    else if(n.kind==='use_case') strip=GHEX[Math.max(0,Math.min(4,n.grade||0))];
    else if(n.kind==='master') strip='#c98f88';
    else if(n.kind==='connector') strip=(n.role==='ingress'?'#5DCAA5':n.role==='egress'?'#378ADD':'#df8b46');
    else if(n.kind==='router') strip='#7d6fb0';
    const kind={agent:'Agent',human:'Person · sign-off',use_case:'Task',master:'The boundary',connector:'Connector',router:'Routing'}[n.kind];
    let meta='';
    if(n.kind==='agent') meta='<span>'+esc(n.grade||'')+'</span>'+((n.status&&n.status!=='active')?'<span style="color:#b15;font-weight:600">'+esc(n.status)+'</span>':'');
    else if(n.kind==='human') meta='in the loop';
    else if(n.kind==='use_case'){const g=Math.max(0,Math.min(4,n.grade||0)); meta='<span class="gradechip" style="background:'+GHEX[g]+'">L'+g+'</span><span>risk '+esc(n.risk||'?')+'</span>'+(reserved?'<span style="color:#a33;font-weight:600">'+esc(reservedPhrase(n))+'</span>':'');}
    else if(n.kind==='master') meta=boundaryReserved?'<span style="color:#e2877f;font-weight:600">limited at the boundary</span> · see each gate':'edge of what’s allowed out';
    else if(n.kind==='connector') meta='<span>'+esc(n.role||'')+' · '+esc(n.channel||'')+'</span>';
    else if(n.kind==='router'){
      const DEST={auto:'proceeds · runs',human:'a person signs off',reserved:'a person signs off · reserved by law',refused:'logged · stopped',prohibited:'severed · no route',unfired:'not run yet'};
      const VN={auto:'auto',human:'needs a person',reserved:'reserved',refused:'refused',prohibited:'not allowed',unfired:'unfired'};
      const ORDER=['auto','human','reserved','refused','prohibited','unfired'];
      // only EGRESS cords carry a server verdict (grant/authority cords don't) — match
      // the master-tally pattern so the router never lights a row off a verdict-less edge
      const live=new Set((S.g.edges||[]).filter(e=>e.kind==='egress').map(effVerdict));
      const liveNames=ORDER.filter(v=>live.has(v));
      const ACT=new Set(['human','reserved']);   // verdicts a person can act on from here
      meta='<div class="vrtable" aria-label="verdict routing — '+escA(liveNames.length?('live: '+liveNames.map(v=>VN[v]).join(', ')):'no live verdicts in this patch')+'">'
        +ORDER.map(v=>{ const on=live.has(v); const act=(on&&ACT.has(v))?'<span class="vract" role="button" tabindex="0" data-v="'+v+'" aria-label="request sign-off for tasks at '+VN[v]+'">request sign-off →</span>':'';
          return '<div class="vrow'+(on?' live':'')+'"><span class="vlamp" style="background:'+((VERDICT[v]||{}).col||'#5a616f')+'"></span><span class="vn">'+VN[v]+'</span><span class="vd">→ '+DEST[v]+'</span>'+(on?'<span class="vlive">live</span>':'')+act+'</div>'; }).join('')
        +'</div><div class="vrnote">live needs-a-person / reserved rows can request a sign-off · the server decides the verdict</div>';
    }
    el.innerHTML='<div class="strip" style="background:'+strip+'"></div><div class="body"><div class="kind">'+kind+'</div><div class="lbl">'+esc(n.label||n.id)+'</div><div class="meta">'+meta+'</div></div>';
    if(n.kind==='use_case'||n.kind==='master'){ const pi=document.createElement('div'); pi.className='inlet port'; pi.tabIndex=0; pi.setAttribute('role','button'); pi.setAttribute('aria-label','inlet — press Enter to complete a grant to '+(n.label||n.id)); el.appendChild(pi); pi.addEventListener('pointerdown',ev=>ev.stopPropagation()); pi.addEventListener('click',ev=>{ev.stopPropagation();finishWire(n.id);}); pi.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();finishWire(n.id);}}); }
    if(n.kind==='agent'){ const po=document.createElement('div'); po.className='outlet port'+(S.pending===n.id?' pending':''); po.tabIndex=0; po.setAttribute('role','button'); po.setAttribute('aria-label','grant authority from '+(n.label||n.id)+' — press Enter, then choose a task'); el.appendChild(po); po.addEventListener('pointerdown',ev=>ev.stopPropagation()); po.addEventListener('click',ev=>{ev.stopPropagation();startWire(n.id);}); po.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();startWire(n.id);}}); }
    el.tabIndex=0; el.setAttribute('role','button'); el.setAttribute('aria-selected',String(S.sel===n.id));
    let aria=kind+': '+(n.label||n.id);
    if(n.kind==='use_case'){ aria+=', risk '+(n.risk||'low')+', level L'+Math.max(0,Math.min(4,n.grade||0)); if(reserved)aria+=', '+reservedPhrase(n); const ev=S.g.edges.find(e=>e.kind==='egress'&&e.from===n.id); if(ev){const vl=VINFO(effVerdict(ev)).label; if(vl)aria+=', verdict '+vl;} }
    else if(n.kind==='agent') aria+=', '+(n.status||'active');
    else if(n.kind==='human'&&n.role) aria+=', role '+n.role;
    else if(n.kind==='master'&&boundaryReserved) aria+=', limited at the boundary';
    else if(n.kind==='router') aria+=', shows where each verdict is handled and can request a sign-off for live needs-a-person / reserved tasks; the server decides the verdict';
    el.setAttribute('aria-label',aria);
    el.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();S.sel=n.id;S.selEdge=null;S.pending=null;render();} else if(ev.key==='Escape'){ if(S.pending)announce('Wiring cancelled.'); S.pending=null;render();} });
    if(n.kind==='router'){ el.querySelectorAll('.vract').forEach(b=>{ const go=ev=>{ ev.stopPropagation(); routerRequestSignoffs(b.dataset.v); }; b.addEventListener('click',go); b.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); go(ev); } }); }); }
    drag(el,n); stage.appendChild(el);
  }
  // linear screen-reader alternative to the spatial canvas (nodes + cords)
  const ul=document.getElementById('a11ylist');
  if(ul){ const KN={agent:'Agent',human:'Person',use_case:'Task',master:'The boundary',connector:'Connector',router:'Verdict router'};
    const li=S.g.nodes.map(n=>'<li>'+esc((KN[n.kind]||n.kind)+': '+(n.label||n.id))+'</li>');
    S.g.edges.forEach(e=>{const a=byId(e.from),b=byId(e.to); if(a&&b) li.push('<li>'+esc((a.label||a.id)+' — '+e.kind+' → '+(b.label||b.id))+'</li>');});
    ul.innerHTML=li.join(''); }
  inspect(); findings(); federatedCheck();
}
function inspect(){
  const b=document.getElementById('inspectBody');
  if(S.selEdge!=null && S.g && S.g.edges[S.selEdge]) return inspectEdge(b, S.g.edges[S.selEdge]);
  const n=S.sel?byId(S.sel):null;
  if(!n){ b.innerHTML='<div class="empty">Select a node or cord to edit it.</div>'; return; }
  let h='<div class="field"><label>Label</label><input type="text" id="lbl" value="'+escA(n.label)+'"'+(n.kind==='master'||n.kind==='router'?' disabled':'')+'></div>';
  if(n.kind==='use_case'){
    h+='<div class="field"><label id="risklbl">Risk</label><div class="toggle" role="radiogroup" aria-labelledby="risklbl">'+RISKS.map(r=>{const on=(n.risk||'low')===r;return '<div class="tb '+(on?'on':'')+'" data-risk="'+r+'" role="radio" tabindex="'+(on?'0':'-1')+'" aria-checked="'+on+'">'+r+'</div>';}).join('')+'</div></div>';
    h+='<div class="field"><label>Server</label><div class="ro">autonomy level <b>L'+Math.max(0,Math.min(4,n.grade||0))+'</b></div>'+reservationsHtml(n)+'</div>';
    h+='<div class="field"><label>Data tags</label>'+tagsHtml(n)+'</div>';
    h+='<div class="field"><label>Declarations</label>'+declRows(n)+(S.path?declAddHtml():'<div class="ro" style="font-size:10px;color:var(--txt-dim)">open a folder to author declarations</div>')+'</div>';
    h+='<div class="field" id="ucops"><div class="empty">loading contract…</div></div>';
  } else if(n.kind==='agent'){
    h+='<div class="field"><label>Server</label><div class="ro">grade <b>'+esc(n.grade||'—')+'</b> · status <b>'+esc(n.status||'active')+'</b></div></div>';
    h+='<div class="field" id="trackstrip"><div class="empty">loading track…</div></div>';
    h+='<button class="del" id="kill" title="'+((n.status&&n.status!=='active')?'re-enable this agent to act (asks to confirm — reviving loosens governance)':'stop this agent now — the safe direction, takes effect immediately')+'">'+((n.status&&n.status!=='active')?'Revive agent':'Kill agent')+'</button>';
  } else if(n.kind==='human'){
    h+='<div class="field"><label>Decided by the server</label><div class="ro">a person in the loop'+(n.role?' · '+esc(n.role):'')+'</div></div>';
    h+='<div class="field" id="trackstrip"><div class="empty">loading track…</div></div>';
  } else if(n.kind==='connector'){
    h+='<div class="field"><label>Boundary port</label><div class="ro">'+esc(n.role||'')+' · '+esc(n.channel||'')+'</div></div>';
    h+='<div class="field" id="trackstrip"><div class="empty">loading track…</div></div>';
  } else if(n.kind==='master'){
    // refusing dual-limiter: which kind of stop is in force, binary per lamp (no meter)
    const evs=S.g.edges.filter(e=>e.kind==='egress').map(e=>effVerdict(e));
    // prohibited (severed, no sign-off) is counted SEPARATELY from reserved
    // (a person CAN sign off) — never folded into one law tally (E6).
    const nAuto=evs.filter(v=>v==='auto').length, nReserved=evs.filter(v=>v==='reserved').length, nProhibited=evs.filter(v=>v==='prohibited').length, nPpl=evs.filter(v=>v==='human').length;
    h+='<div class="field"><label>The boundary — what’s in force</label>';
    const cc=n=>n?' · '+n+' cord'+(n===1?'':'s'):'';
    h+='<div class="lamp'+(nAuto?' on':'')+'" data-auto="'+nAuto+'"><span class="b" style="background:'+(nAuto?'#5aa886':'#2c313c')+'"></span>releasing · auto within policy'+cc(nAuto)+'</div>';
    h+='<div class="lamp'+(nPpl?' on':'')+'"><span class="b" style="background:'+(nPpl?'#df8b46':'#2c313c')+'"></span>needs a person'+cc(nPpl)+'</div>';
    h+='<div class="lamp'+(nReserved?' on':'')+'" data-reserved="'+nReserved+'"><span class="b" style="background:'+(nReserved?'#e2554a':'#2c313c')+'"></span>reserved · a person must sign off'+cc(nReserved)+'</div>';
    h+='<div class="lamp proh'+(nProhibited?' on':'')+'" data-prohibited="'+nProhibited+'"><span class="b" style="background:'+(nProhibited?'#a8332b':'#2c313c')+'"></span>not allowed · severed, no sign-off'+cc(nProhibited)+'</div>';
    h+='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">the boundary releases only what is auto; it never weakens a verdict.</div></div>';
  } else if(n.kind==='router'){
    h+='<div class="field"><label>What it is</label><div class="ro">A routing key: it shows where each server verdict is handled and lights the ones live on the patch. On a live <b>needs-a-person</b> or <b>reserved</b> row it can request a sign-off (routed to the competent person, recorded). The server still decides the verdict.</div></div>';
  }
  b.innerHTML=h;
  const lbl=b.querySelector('#lbl');
  if(lbl) lbl.addEventListener('change',()=>rename(n,lbl.value));
  b.querySelectorAll('.tb').forEach(t=>{ t.addEventListener('click',()=>setRisk(n,t.dataset.risk));
    t.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();setRisk(n,t.dataset.risk);}
      else if(ev.key==='ArrowRight'||ev.key==='ArrowLeft'){ev.preventDefault(); const i=RISKS.indexOf(n.risk||'low'); setRisk(n,RISKS[(i+(ev.key==='ArrowRight'?1:RISKS.length-1))%RISKS.length]);} }); });
  const k=b.querySelector('#kill'); if(k) k.addEventListener('click',()=>{
    // fail-safe direction: kill (restrict) is instant; revive (loosen) is deliberate.
    const reviving=(n.status&&n.status!=='active');
    if(reviving && !confirm('Revive '+(n.label||n.id)+'? This re-enables the agent to act, and is recorded in the signed record.')) return;
    toggleKill(n);
  });
  const dadd=b.querySelector('#daddbtn'); if(dadd) dadd.addEventListener('click',()=>addDeclaration(n));
  if(n.kind==='use_case') fillUcOps(n);
  if(n.kind==='agent'||n.kind==='human') fillTrackStrip({party_id:n.id.replace(/^party:/,'')});
  else if(n.kind==='connector') fillTrackStrip({connector_id:n.id.replace(/^conn:/,'')});
}
async function wire(agentId, ucId){
  const a=agentId.replace(/^party:/,''); const uc=byId(ucId);
  if(S.path){ const al=allowedFor(ucId); if(al.indexOf(a)===-1) al.push(a); await reReg(uc, al); await reload(); }
  else { if(!S.g.edges.some(e=>e.from===agentId&&e.to===ucId)) S.g.edges.push({from:agentId,to:ucId,kind:'authority'}); render(); }
}
async function unwire(edgeIdx){
  const e=S.g.edges[edgeIdx]; if(!e||e.kind!=='authority') return;
  // a cord is a thin click target — confirm before removing a signed grant (prevents
  // an accidental mutation of the record; doctrine-neutral, the grant stays revocable).
  const ua=byId(e.from), ub=byId(e.to);
  if(!confirm('Revoke authority: '+((ua&&ua.label)||e.from)+' → '+((ub&&ub.label)||e.to)+'?\nThis removes a signed grant and is recorded in the signed record.')) return;
  if(S.path){ const uc=byId(e.to); const al=allowedFor(e.to).filter(x=>x!==e.from.replace(/^party:/,'')); await reReg(uc, al); await reload(); }
  else { S.g.edges.splice(edgeIdx,1); render(); }
}
/* add nodes */
let _seq=0;
// Name-on-create — clicking + Agent/Person/Task asks for a real name (and a
// discrete risk for a Task) instead of dropping uc-94217. Returns {name,risk}
// or null on cancel. Risk is a discrete choice, never a dial (doctrine).
function promptCreate(kind){
  const labelKind=kind==='use_case'?'Task':(kind==='agent'?'Agent':'Person');
  // re-entrancy guard — a second click while a create modal is open just refocuses
  // it (no duplicate id, no leaked scrim), and creates nothing.
  const open=document.getElementById('createpanel');
  if(open){ const nf=open.querySelector('#cpname'); if(nf)nf.focus(); return Promise.resolve(null); }
  return new Promise(resolve=>{
    const opener=document.activeElement;
    const p=document.createElement('div'); p.id='createpanel'; p.setAttribute('role','dialog'); p.setAttribute('aria-label','Name the new '+labelKind);
    p.style.cssText='position:absolute;top:64px;left:50%;transform:translateX(-50%);width:320px;max-width:92%;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:11;box-shadow:0 10px 40px rgba(0,0,0,.55)';
    p.innerHTML='<b style="font-family:Space Grotesk,sans-serif;font-size:13px">New '+labelKind+'</b>'+draftChipHtml('cards')
      +'<div style="margin-top:8px"><label style="font-size:11px;color:var(--txt-dim);display:block;margin-bottom:3px">Name</label>'
      +'<input id="cpname" aria-required="true" placeholder="'+(kind==='use_case'?'e.g. Loan scoring':(kind==='agent'?'e.g. Drafting assistant':'e.g. Jordan (DPO)'))+'" style="width:100%;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;box-sizing:border-box">'
      +'<div id="cperr" role="alert" aria-live="assertive" style="display:none;color:#d98b8b;font-size:10px;margin-top:3px">A name is required to create it.</div></div>'
      +(kind==='use_case'?'<div style="margin-top:8px"><label style="font-size:11px;color:var(--txt-dim);display:block;margin-bottom:3px">Risk</label><select id="cprisk" style="width:100%;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px"><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></div>':'')
      +'<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px"><button id="cpdiscard" class="tool" title="delete the saved intake draft" style="margin-right:auto;border-color:#5a2f2a;color:#e6b0aa">Discard draft</button><button id="cpcancel" class="tool">Cancel</button><button id="cpok" class="tool" style="border-color:#5DCAA5;color:#9fe0c4">Create</button></div>';
    stage.appendChild(p);
    const restore=modalize(p,opener);
    const done=(val)=>{ draftPanelClosed('cards'); p.remove(); restore(); resolve(val); };
    const submit=()=>{ const name=(p.querySelector('#cpname').value||'').trim(); if(!name){ const e=p.querySelector('#cperr'); if(e)e.style.display='block'; p.querySelector('#cpname').focus(); return; } const risk=kind==='use_case'?p.querySelector('#cprisk').value:undefined; done({name:name,risk:risk}); };
    p.querySelector('#cpname').addEventListener('input',()=>{ const e=p.querySelector('#cperr'); if(e)e.style.display='none'; });
    p.querySelector('#cpok').addEventListener('click',submit);
    p.querySelector('#cpcancel').addEventListener('click',()=>done(null));
    p.addEventListener('keydown',ev=>{ if(ev.key==='Escape'){ev.preventDefault();done(null);} else if(ev.key==='Enter'&&ev.target.tagName!=='BUTTON'){ev.preventDefault();submit();} });
    // silent prefill of the in-progress intake card (same kind only); edits re-save
    const cnm=p.querySelector('#cpname'), crk=p.querySelector('#cprisk'); const cdd=draftLoaded('cards');
    if(cdd&&cdd.kind===kind){ if(typeof cdd.name==='string'&&!cnm.value) cnm.value=cdd.name;
      if(crk&&cdd.risk&&[...crk.options].some(o=>o.value===cdd.risk)) crk.value=cdd.risk; }
    const cq=()=>draftQueue('cards',()=>({kind:kind,name:cnm.value,risk:crk?crk.value:''}));
    cnm.addEventListener('input',cq); if(crk) crk.addEventListener('change',cq);
    p.querySelector('#cpdiscard').addEventListener('click',()=>{ cnm.value=''; draftDiscard('cards'); });
    draftChipMount('cards');
  });
}
async function addNode(kind){
  if(kind==='router'){
    // Verdict Router — a visualize-only display node (no server concept, no wiring):
    // shows the verdict→handling map, lighting whichever verdicts are live in the
    // patch. Kept client-side and re-injected across reloads (like POS positions).
    const id='router:'+(Date.now()%100000); const r={id:id,kind:'router',label:'Verdict router'};
    (window._routers=window._routers||[]).push(r); S.g.nodes.push(r);
    // sit in clear working area (the 226px-wide node would overflow under the
    // Inspector at the use_case column); lower-left, stepped so multiples don't stack
    setpos(id,150,300+(_seq++%4)*26); S.sel=id; render(); return;
  }
  const created=await promptCreate(kind); if(!created) return;
  const nm=created.name, y=70+(_seq%6)*40;
  if(kind==='use_case'){ const id='uc-'+(Date.now()%100000); const risk=created.risk||'low';
    if(S.path){ await tool('workspace_workflow',{op:'use_case_register',params:{folder_context:S.path,use_case_id:id,name:nm,fingerprint:{},risk:risk,allowed_agents:[],actor:'app-user'}}); setpos('uc:'+id,330,y); await reload(); S.sel='uc:'+id; }
    else { S.g.nodes.push({id:'uc:'+id,kind:'use_case',label:nm,risk:risk,issue_type:'',grade:0,grade_ceiling:offlineCeil(risk),reserved:[]}); S.g.edges.push({from:'uc:'+id,to:'master',kind:'egress',verdict:'unfired'}); setpos('uc:'+id,330,y); S.sel='uc:'+id; render(); }
  } else {
    const id=(kind==='agent'?'agent-':'human-')+(Date.now()%100000);
    if(S.path){ await tool('workspace_policy',{op:'party_register',params:{folder_context:S.path,party_id:id,kind:kind,name:nm,actor:'app-user'}}); setpos('party:'+id,46,y); await reload(); S.sel='party:'+id; }
    else { S.g.nodes.push({id:'party:'+id,kind:kind,label:nm,status:'active'}); setpos('party:'+id,46,y); S.sel='party:'+id; render(); }
  }
  // the commit act completed the intake — the scratch card would otherwise
  // resurrect on the next create (and read stale against the new chain tip)
  if(S.path) draftDiscard('cards');
  _seq++; inspect();
}

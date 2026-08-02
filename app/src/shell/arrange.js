/* The ARRANGE view — one of the four view-toggle states (PATCH/ARRANGE/
   MATRIX/DESK). LANES: agents as time lanes, runs as verdict-coloured clips,
   a read-only projection of the audit tail. MIX: the arranger's console
   form — one channel strip per agent party (status lamp, the L0-L4 ladder
   with every rung's own matrix light rendered verbatim, a screen that
   follows one global mode selector — Overview | Autonomy | Checks | Reach |
   Time — pending sign-off count, Hold) plus a bus header (name, pending
   total, Hold-all, the inherit line, and the console_snapshot environment
   rollup). Ladder and lights are displays only; tightening/loosening keep
   their write paths in Rules. Shell chrome, not plane-provided content:
   loaded unconditionally by compose_classic(), no panel-mount registration.

   renderArrange/renderArrangeLanes/renderArrangeMix read S.path only (to
   gate on "no workspace open") — never S.g/S.sel/S.selEdge, so they carry
   none of the PATCH canvas's own mutable state, same as fillTrackStrip and
   drawLoom before them. _TS_STATUS and _tsGrade stay in index.html: they're
   shared with the Inspector's track-strip rendering (see track_strip.js). */
function _arrMode(){ return window._arrModeSel==='mix'?'mix':'lanes'; }
function _arrBar(){ const m=_arrMode();
  return '<div class="arr-bar" role="radiogroup" aria-label="arrange form">'
    +'<button class="arr-vt'+(m==='lanes'?' on':'')+'" role="radio" aria-checked="'+(m==='lanes')+'" data-arrmode="lanes" title="lanes — agents over time, runs as clips; a read-only projection of the record">LANES</button>'
    +'<button class="arr-vt'+(m==='mix'?' on':'')+'" role="radio" aria-checked="'+(m==='mix')+'" data-arrmode="mix" title="mix — one channel strip per agent, each with a screen (Overview | Autonomy | Checks | Reach | Time); Hold suspends (a write, recorded)">MIX</button></div>'; }
// every arrange paint goes through here so the LANES | MIX sub-toggle stays live
function _arrSet(host,body){ host.innerHTML=_arrBar()+body;
  host.querySelectorAll('[data-arrmode]').forEach(b=>b.addEventListener('click',()=>{ window._arrModeSel=b.dataset.arrmode; renderArrange(); })); }
function renderArrange(){
  const host=document.getElementById('arrange'); if(!host) return;
  if(!S.path){ host.innerHTML='<div class="arr-empty">open a workspace to see its lanes</div>'; return; }
  if(_arrMode()==='mix') return renderArrangeMix(host);
  return renderArrangeLanes(host);
}
async function renderArrangeLanes(host){
  _arrSet(host,'<div class="arr-empty">loading lanes…</div>');
  let parties=[], events=[];
  try{ const r=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); parties=(Array.isArray(r)?r:((r&&(r.parties||r.rows))||[])); }catch(_){ }
  try{ const r=await tool('workspace_audit',{op:'tail',params:{folder_context:S.path,limit:60}}); events=((r&&r.events)||[]); }catch(_){ }
  if(window._view!=='arrange'||_arrMode()!=='lanes') return;   // toggled away while loading
  const agentIds=parties.filter(p=>(p.party_kind||p.kind)==='agent').map(p=>p.party_id||p.id).filter(Boolean);
  const actors=[]; const seen=new Set();
  agentIds.forEach(a=>{ if(!seen.has(a)){ seen.add(a); actors.push(a); } });
  events.forEach(e=>{ const a=e.actor||'system'; if(!seen.has(a)){ seen.add(a); actors.push(a); } });
  if(!actors.length){ _arrSet(host,'<div class="arr-empty">no lanes yet — add an agent (Set up → Agent)</div>'); return; }
  const byActor={}; actors.forEach(a=>byActor[a]=[]);
  events.forEach(e=>{ const a=e.actor||'system'; (byActor[a]=byActor[a]||[]).push(e); });
  const VN={auto:'auto',human:'needs a person',refused:'refused',reserved:'reserved',prohibited:'not allowed',unfired:'unfired',permitted:'event'};
  const signedN=events.filter(e=>e.signed).length; const unsignedN=events.length-signedN;
  let h='<div class="arr-group"><span class="arr-gname">'+esc(bn(S.path))+'</span><span class="arr-ghint">group · '+actors.length+' lane'+(actors.length===1?'':'s')+' · '+events.length+' event'+(events.length===1?'':'s')+(unsignedN?(' ('+unsignedN+' unsigned)'):'')+'</span></div>';
  h+=actors.map(a=>{
    const evs=(byActor[a]||[]); const isAgent=agentIds.includes(a);
    const clips=evs.map((e,i)=>{ const v=e.verdict; const col=((VERDICT[v]||{}).col)||'#5a616f'; const stripe=(v==='reserved'||v==='prohibited')?' arr-stripe':''; const nm=(e.event||'event');
      return '<span class="arr-clip'+stripe+'" style="left:'+(6+i*48)+'px;background:'+col+';color:#fff" title="'+escA(esc(nm+(v?(' · '+v):' · no verdict')+' · '+(e.ts||'')+(e.signed?' · signed':' · UNSIGNED')))+'">'+esc(((e.signed?'':'⚠ ')+nm).slice(0,8))+'</span>'; }).join('');
    const laneUn=evs.filter(e=>!e.signed).length;
    return '<div class="arr-row"><div class="arr-lh"><span class="arr-dot" style="background:'+(isAgent?'#df8b46':'#5DCAA5')+'"></span>'+esc(a)+'</div><div class="arr-lane" role="img" aria-label="'+escA(esc(a+' — '+evs.length+' event'+(evs.length===1?'':'s')+(laneUn?(', '+laneUn+' unsigned'):'')))+'">'+(clips||'<span class="arr-noev">no runs yet</span>')+'</div></div>';
  }).join('');
  h+='<div class="arr-legend">'+['auto','human','reserved','refused','prohibited','unfired','permitted'].map(v=>'<span><span class="arr-lg'+((v==='reserved'||v==='prohibited')?' arr-stripe':'')+'" style="background:'+VERDICT[v].col+'"></span>'+esc(VN[v])+'</span>').join('')+'</div>';
  _arrSet(host,h);
}
/* MIX — the arranger's console form. One channel strip per agent party: status
   word + lamp, the L0-L4 ladder as a read-only stepped display — the earned
   rung highlighted, and every rung carrying its own matrix light (the server
   matrix cell for this workspace's oversight band, go|ask|block, rendered
   verbatim; the client never composes a ceiling from the cells, because the
   grid need not be monotonic) — a mono readout so colour is never the only
   signal, a screen (below), the oversight band word, the pending sign-off
   count, Hold, and the name scribble. One bus header for the workspace: name,
   pending total (the sum of the strips' counts), Hold-all, and the inherit
   line when a registered parent exists. Ladder and lights are displays only —
   tightening and loosening keep their write paths in Rules.

   The strip screen: one global mode selector — Overview | Autonomy | Checks |
   Reach | Time — and every strip's screen follows it (view follows mode), so
   one quantity compares across all channels at a glance. Screen content comes
   from the strip's own track_strip payload; the only arithmetic is field-to-
   field (the ask+block share from the meter tally, time remaining from the
   server deadline) — the client never composes governance. A page carries at
   most three value rows and never scrolls; every value is words, never colour
   alone. A law-locked track's screen shows the reserved plate and renders no
   control. The Checks page renders its stop through stopCard — the one
   component every stop shares — whose hold state carries the one screen
   affordance, the deep-link to Pending: a navigation, not a decision. */
const MIX_MODES=['overview','autonomy','checks','reach','time'];
const MIX_MODE_LABEL={overview:'Overview',autonomy:'Autonomy',checks:'Checks',reach:'Reach',time:'Time'};
const MIX_MODE_HINT={overview:'plain sentences — what waits, the status, the worst item',
  autonomy:'level with its matrix cell, and the ask+block share',
  checks:'sign-offs in progress — form and signed count',
  reach:'channels and their floors',
  time:'clocks — each deadline with its elapse direction'};
function _mixScreenMode(){ return MIX_MODES.includes(window._mixScreenSel)?window._mixScreenSel:'overview'; }
function _mixModesBar(){ const m=_mixScreenMode();
  return '<div class="mix-modes" role="radiogroup" aria-label="strip screen mode — every strip follows the selected mode">'
    +MIX_MODES.map(k=>'<button class="mix-mt'+(k===m?' on':'')+'" role="radio" aria-checked="'+(k===m)+'" data-mixmode="'+k+'" title="'+escA(MIX_MODE_HINT[k])+'">'+MIX_MODE_LABEL[k]+'</button>').join('')+'</div>'; }
// time remaining against a server deadline — sign always rendered, so a clock
// never reads as a bare number: t−36h (still open) / t+2h (elapsed)
function _tRem(deadline,now){ const d=deadline-now, a=Math.abs(d);
  const u=a>=86400?Math.round(a/86400)+'d':(a>=3600?Math.round(a/3600)+'h':(a>=60?Math.round(a/60)+'m':Math.round(a)+'s'));
  return (d>=0?'t−':'t+')+u; }
/* The value rows for one strip's screen page in one mode — at most three, each
   plain words from the track_strip payload. A row is either plain text or a
   stop card (rendered through stopCard, the one component every stop shares);
   a card's lines count as value rows. A law-locked track leads with the
   reserved plate in every mode — on the Checks page the plate is the reserved
   stop card, which renders no control whatever the mode. The worst-item pick
   in Overview is display ordering only, not a composed verdict. */
function _mixScreenRows(t,mode){
  const s=t.strip;
  if(!s) return {rows:[{txt:'strip unread'}]};
  const rows=[]; const push=(txt,cls)=>rows.push({txt:txt,cls:cls||''});
  const locked=!!(s.ladder&&s.ladder.locked);
  const pend=((s.approvals||{}).pending)||[];
  const st=s.status||t.list.status||'active';
  if(locked){
    if(mode==='checks'){ const lk=((s.ladder.locks||[])[0])||{};
      rows.push({card:{state:'reserved',phrase:'reserved · by law',bound_by:lk.source||'law'},
        txt:'reserved · by law · bound by '+(lk.source||'law')}); }
    else push('reserved · by law — tighten only','mix-splate');
  }
  if(mode==='overview'){
    push(pend.length===0?'nothing waits on you':(pend.length+' sign-off'+(pend.length===1?'':'s')+' wait'+(pend.length===1?'s':'')+' on you'));
    push('is '+st);
    if(!locked){
      const clocked=pend.filter(p=>p.deadline!=null);
      if(st!=='active') push(st==='suspended'?'held — resume in Protections':'stopped — see Protections');
      else if(clocked.length) push('next clock '+_tRem(clocked.reduce((a,p)=>p.deadline<a.deadline?p:a).deadline,Date.now()/1000)+' → '+(clocked.reduce((a,p)=>p.deadline<a.deadline?p:a).on_elapse||'unread'));
      else push('nothing else needs attention');
    }
  }else if(mode==='autonomy'){
    const g=_tsGrade((s.ladder&&s.ladder.grade)!=null?s.ladder.grade:t.list.grade);
    const leds=t.leds;
    push(g==null?'level unread':('L'+g+(leds?' · matrix: '+leds[g]:' · matrix unread')));
    // ask+block share — the strip's gain-reduction reading: 1 − auto/(auto+human+refused),
    // every term a tally field; the tally is windowless, so the label says all time.
    const v=((s.meter||{}).verdicts)||{};
    const a=v.auto||0,h=v.human||0,r=v.refused||0,den=a+h+r;
    push('ask+block '+(den?((h+r)+'/'+den):'0/0 —')+' · all time');
    push(((s.meter||{}).events||0)+' events on record · all time');
  }else if(mode==='checks'){
    if(!pend.length) push('no checks waiting');
    else{
      const p=pend[0];
      const bound=(p.form||'form unread')+' · signed '+(p.signed==null?'—':p.signed)+' of '+(p.required==null?'—':p.required);
      if(locked) push(bound);   // the reserved card leads; the count is information only
      else rows.push({card:{state:'hold',phrase:'needs a person',bound_by:bound},
        txt:'needs a person · bound by '+bound});
      if(pend.length>1) push('+'+(pend.length-1)+' more');
    }
  }else if(mode==='reach'){
    const ch=s.channels||[];
    if(!ch.length) push('no channels — reaches nothing outside');
    else{
      push(ch.length+' channel'+(ch.length===1?'':'s'));
      ch.slice(0,2).forEach(c=>push(c.registered===false?(c.connector_id+' · not registered')
        :(c.connector_id+' · '+(c.role||'role unread')+' · floor '+(c.floor||'unread'))));
    }
  }else if(mode==='time'){
    const clocks=pend.filter(p=>p.deadline!=null);
    if(!clocks.length) push('no clocks');
    else clocks.slice(0,3).forEach(p=>push(_tRem(p.deadline,Date.now()/1000)+' → '+(p.on_elapse||'unread')+' · '+(p.form||'')));
  }
  // cap at three value rows per page — a stop card's lines count as rows
  const out=[]; let n=0;
  for(const x of rows){ const w=x.card?(1+(x.card.bound_by?1:0)+(x.card.note?1:0)):1;
    if(n+w>3) break; out.push(x); n+=w; }
  return {rows:out};
}
function _mixScreenHTML(t,mode){
  const r=_mixScreenRows(t,mode);
  const lbl='screen · '+MIX_MODE_LABEL[mode]+' — '+r.rows.map(x=>x.txt).join(' · ');
  let h='<div class="mix-screen" data-screen="'+escA(esc(t.pid))+'" data-mode="'+mode+'" role="group" aria-label="'+escA(esc(lbl))+'">';
  h+=r.rows.map(x=>x.card?stopCard(x.card,'mix-srow')
    :'<div class="mix-srow'+(x.cls?' '+x.cls:'')+'">'+esc(x.txt)+'</div>').join('');
  return h+'</div>';
}
/* Listener discipline: the mode buttons persist across a mode switch (only
   their classes change), so they are bound exactly once per full render —
   binding them again would stack handlers and double the screen repaints on
   every click. The stop-card deep-links live inside the screens, which a mode
   switch replaces, so those rebind per replaced screen. */
function _mixBindScreens(host){
  host.querySelectorAll('[data-mixmode]').forEach(b=>b.addEventListener('click',()=>_mixSetScreenMode(b.dataset.mixmode)));
  bindStopCards(host);
}
// mode switch repaints every screen from the strips already fetched — a display
// change, no refetch; a stale cache (folder or view moved on) repaints fully
function _mixSetScreenMode(mode){
  window._mixScreenSel=mode;
  const host=document.getElementById('arrange'); const c=window._mixScreens;
  if(!host||!c||c.fc!==S.path||window._view!=='arrange'||_arrMode()!=='mix'){ renderArrange(); return; }
  const m=_mixScreenMode();
  host.querySelectorAll('[data-mixmode]').forEach(b=>{ const on=b.dataset.mixmode===m; b.classList.toggle('on',on); b.setAttribute('aria-checked',String(on)); });
  host.querySelectorAll('.mix-screen').forEach(el=>{ const t=c.tracks.find(x=>x.pid===el.dataset.screen); if(!t) return;
    const box=document.createElement('div'); box.innerHTML=_mixScreenHTML(t,m);
    const nu=box.firstElementChild; el.replaceWith(nu); bindStopCards(nu); });
}
async function renderArrangeMix(host){
  _arrSet(host,'<div class="arr-empty">loading strips…</div>');
  const get=async(t,a)=>{ try{ const r=await tool(t,a); return (r&&(r.error||r.ok===false))?null:r; }catch(_){ return null; } };
  const fc=S.path;
  const [pl,sn,mx,wl,env]=await Promise.all([
    get('workspace_policy',{op:'party_list',params:{folder_context:fc}}),
    get('workspace_policy',{op:'snapshot',params:{folder_context:fc}}),
    get('workspace_matrix',{op:'show',params:{folder_context:fc}}),
    get('workspace_workspace',{op:'list'}),
    // one rollup across every workspace this caller may see — the whole
    // environment at a glance, not one poll per bus (console_snapshot).
    get('workspace_workflow',{op:'console_snapshot',params:{}})]);
  if(window._view!=='arrange'||_arrMode()!=='mix'||S.path!==fc) return;   // toggled away while loading
  const parties=pl?(Array.isArray(pl)?pl:((pl.parties||pl.rows)||[])):[];
  const agents=parties.filter(p=>(p.party_kind||p.kind)==='agent');
  const band=(sn&&sn.oversight_default_level)||null;
  // per-rung matrix lights at this workspace's oversight band, each the
  // server cell rendered verbatim (the rail's LED read) — never reduced to a
  // scalar ceiling client-side
  const leds=(mx&&mx.matrix&&band)?_ledStates(mx.matrix,band):null;
  const parent=_parentOf(fc,((wl&&wl.workspaces)||[]));
  const strips=await Promise.all(agents.map(async p=>{
    const pid=p.party_id||p.id;
    const r=await get('workspace_workflow',{op:'track_strip',params:{folder_context:fc,party_id:pid}});
    return {pid:pid,list:p,strip:(r&&r.strip)||null,leds:leds}; }));
  if(window._view!=='arrange'||_arrMode()!=='mix'||S.path!==fc) return;
  window._mixScreens={fc:fc,tracks:strips};   // the mode switch repaints screens from this, no refetch
  const screenMode=_mixScreenMode();
  let pendTotal=0, pendUnread=false;
  const cells=strips.map(t=>{
    const s=t.strip||{};
    const st=s.status||t.list.status||'active';
    const g=_tsGrade((s.ladder&&s.ladder.grade)!=null?s.ladder.grade:t.list.grade);
    const locked=!!(s.ladder&&s.ladder.locked); const lk=locked?((s.ladder.locks||[])[0]||{}):null;
    const pend=t.strip?(((s.approvals||{}).pending)||[]).length:null;
    if(pend==null) pendUnread=true; else pendTotal+=pend;
    const ladderLbl='autonomy ladder: '+(g==null?'level unread':'L'+g+' · '+GRADE_NAME[g])
      +(locked?' · locked by law — tighten only':'')
      +' · matrix at oversight '+(band||'(unset)')+': '
      +(leds?leds.map((s,i)=>'L'+i+' '+s).join(', '):'unread');
    const roTxt=(g==null?'L —':'L'+g)+(leds?(g==null?'':' · matrix: '+leds[g]):' · matrix unread');
    let cellsH=''; for(let i=0;i<5;i++){ const lw=leds?leds[i]:null;
      cellsH+='<div class="mix-rung"><div class="fcell'+(i===g?' earned':'')+'"'+(i===g?' style="background:'+GHEX[g]+'"':'')+' title="L'+i+' · '+GRADE_NAME[i]+(lw?' · matrix: '+lw:'')+'">L'+i+'</div>'
        +'<span class="wsled'+(lw==='go'?' go':(lw==='ask'?' ask':''))+'" title="L'+i+': '+(lw||'unread')+'"'+(lw?'':' style="opacity:.35"')+'></span></div>'; }
    let h='<div class="mix-strip" role="group" aria-label="channel strip '+escA(esc(t.pid))+'">';
    h+='<div class="mix-status" aria-label="status: '+escA(st)+'"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+(_TS_STATUS[st]||'#5a616f')+';margin-right:5px"></span>'+esc(st)+'</div>';
    h+='<div class="mix-ladder" role="img" aria-label="'+escA(ladderLbl)+'">'+cellsH+'</div>';
    h+='<div class="mix-read">'+esc(roTxt)+'</div>';
    h+=_mixScreenHTML(t,screenMode);
    if(locked) h+='<div class="mix-lock" title="'+escA(lk.source||'')+'">🔒 locked by law — tighten only'+(lk.act_type?' ('+esc(lk.act_type)+')':'')+'</div>';
    h+='<div class="mix-ov">oversight '+(band?esc(band):'unread')+'</div>';
    h+='<div class="mix-pend">'+(pend==null?'sign-offs unread':(pend+' sign-off'+(pend===1?'':'s')+' waiting'))+'</div>';
    if(st==='active') h+='<button class="mix-hold" data-hold="'+escA(t.pid)+'" aria-label="hold '+escA(esc(t.pid))+' — suspend this agent (recorded)" title="suspend this agent — the safe direction, recorded">⏸ Hold</button>';
    else h+='<div class="mix-held" aria-label="'+escA(st==='suspended'?'held — resume from Govern → Protections':'status: '+st)+'">'+(st==='suspended'?'held — resume in Protections':esc(st))+'</div>';
    h+='<div class="mix-name">'+esc(t.pid)+'</div></div>';
    return h; });
  const pendTxt=pendUnread?'sign-offs unread':(pendTotal+' sign-off'+(pendTotal===1?'':'s')+' waiting');
  let h='<div class="mix-bus" role="group" aria-label="bus '+escA(esc(bn(fc)))+' — '+agents.length+' strip'+(agents.length===1?'':'s')+', '+escA(pendTxt)+'">';
  h+='<span class="mix-busname">'+esc(bn(fc))+'</span>';
  h+='<span class="mix-buspend" title="the sum of the strips’ pending counts">'+esc(pendTxt)+'</span>';
  h+='<button class="mix-holdall" onclick="transportHold()" aria-label="hold all — suspend every active agent in '+escA(esc(bn(fc)))+' (recorded)" title="suspend every active agent in this workspace — the safe direction, recorded">⏸ Hold all</button>';
  // environment rollup: every visible bus folded into one line so the whole
  // estate reads at a glance without a poll per workspace. Server-scoped to
  // what this caller may see; a meter, never a score.
  if(env&&Array.isArray(env.buses)){
    const nAtt=(env.attention||[]).length, over=env.attention_overflow||0;
    const envTxt=env.count+' workspace'+(env.count===1?'':'s')
      +(nAtt?' · '+nAtt+' need'+(nAtt===1?'s':'')+' a person':' · all clear')
      +(over?' (+'+over+' more)':'');
    h+='<span class="mix-busenv" title="every workspace you can see, folded into one — console_snapshot">'+esc(envTxt)+'</span>';
  }
  if(parent) h+='<div class="mix-inherit">inherits '+esc(bn(parent.path))+' — tighten here, never loosen below it</div>';
  h+='</div>';
  h+=agents.length?(_mixModesBar()+'<div class="mix-rack">'+cells.join('')+'</div>'):'<div class="arr-empty">no strips yet — add an agent (Set up → Agent)</div>';
  _arrSet(host,h);
  host.querySelectorAll('[data-hold]').forEach(b=>b.addEventListener('click',()=>mixHold(b.dataset.hold)));
  _mixBindScreens(host);
}
// per-strip Hold — the existing party-status suspend, rail flow: read the live
// state first so the confirm states the truth, confirm, write, reload, announce.
async function mixHold(pid){
  if(!S.path||!pid) return;
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(e){ announce('could not read '+bn(S.path)+' parties: '+((e&&e.message)||'failed')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('could not read '+bn(S.path)+' parties: '+esc(pl.error||'failed')); return; }
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]);
  const rec=arr.find(p=>(p.party_id||p.id)===pid);
  if(!rec||(rec.status||'active')!=='active'){ announce(pid+' is not active — nothing to hold'); renderArrange(); return; }
  if(!confirm('Hold '+pid+' — suspend this agent? Resume from Govern → Protections. (recorded)')) return;
  try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:S.path,party_id:pid,status:'suspended',actor:'app-user',reason:'mixer hold'}}); }
  catch(e){ announce('could not hold '+pid+': '+((e&&e.message)||'failed')); return; }
  await reload(); await loadWsRail();
  announce('Held '+pid+' — suspended (recorded) · resume from Govern → Protections');
}

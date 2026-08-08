/* Integral governance strip (I1) — the always-on readout of the Live
   Governance board: four traffic-light tiles (sessions · admitted · run-leases
   · needs-you) + the HOTL alarm, fed by the same read-only governance_live
   projection as the drawer (one contract, no second vocabulary). Click →
   expands to the full v2 drawer (openGovlivePanel). No write controls — the
   strip can only show what the server enforces. The alarm names the first
   not-green step and stays silent on all-green; fields the projection omits
   are not drawn (omit-don't-fake). Shell chrome, not plane-provided content:
   loaded unconditionally by compose_classic(), no panel-mount registration. */
let _govStripTimer=null;
function mountGovStrip(){
  if(document.getElementById('govstrip')) return;
  const strip=document.createElement('div'); strip.id='govstrip';
  strip.setAttribute('role','button'); strip.setAttribute('tabindex','0');
  strip.setAttribute('aria-label','Live governance — sessions, admissions, run leases, needs-you; activate to open the full board');
  strip.setAttribute('aria-live','polite');
  strip.style.cssText='position:absolute;top:12px;left:12px;display:flex;align-items:center;gap:9px;padding:5px 10px;background:linear-gradient(180deg,var(--panel),var(--panel-2));border:1px solid var(--line);border-radius:9px;z-index:6;font-family:"IBM Plex Mono",monospace;font-size:10px;cursor:pointer;box-shadow:0 4px 18px rgba(0,0,0,.35)';
  strip.innerHTML='<span style="color:var(--txt-dim)">governance…</span>';
  strip.addEventListener('click',()=>openGovlivePanel());
  strip.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();openGovlivePanel();}});
  stage.appendChild(strip);
  loadGovStrip();
  if(!_govStripTimer) _govStripTimer=setInterval(loadGovStrip,4000);
}
async function loadGovStrip(){
  const strip=document.getElementById('govstrip'); if(!strip) return;
  if(!S.path){ strip.innerHTML='<span style="color:var(--txt-dim)">governance — open a workspace</span>'; return; }
  let b; try{ b=await tool('workspace_workflow',{op:'governance_live',params:{folder_context:S.path}}); }
  catch(e){ strip.innerHTML='<span style="color:#d98b8b">governance board offline — '+esc((e&&e.message)||'failed')+'</span>'; return; }
  if(!b||b.ok===false){ strip.innerHTML='<span style="color:#d98b8b">'+esc((b&&b.error)||'board unavailable')+'</span>'; return; }
  const sum=b.summary||{};
  // The HOTL alarm concerns STEPS a human must mind: any session whose lane
  // verdict left the GO family (human/reserved/refused/prohibited) or that
  // carries the escalation flag. `unfired` is idle, not a step — flaring on
  // every fresh workspace would train people to ignore the lamp.
  const flare=(b.sessions||[]).filter(s=>s.escalation===true||['human','reserved','refused','prohibited'].includes(s.verdict));
  const first=flare[0]||null;
  const firstName=first?((first.sid||'?').slice(0,14)+' · '+(first.escalation&&!['human','reserved','refused','prohibited'].includes(first.verdict)?'escalation':first.verdict)):'';
  const needs=sum.escalations!=null?sum.escalations:flare.length;
  const light=(k,label,count,col,title)=>'<span class="gs-light" data-k="'+k+'" data-count="'+escA(count!=null?count:'—')+'" title="'+escA(esc(title))+'" style="display:inline-flex;align-items:center;gap:4px;white-space:nowrap">'
    +'<span style="width:8px;height:8px;border-radius:50%;background:'+col+';flex:none"></span>'
    +'<b style="color:var(--txt)">'+esc(count!=null?count:'—')+'</b><span style="color:var(--txt-dim);font-size:9px">'+label+'</span></span>';
  const admittedCol=(sum.admitted!=null&&sum.sessions_open!=null&&sum.admitted<sum.sessions_open)?'#e0a852':'#4fbe8b';
  let h='';
  h+=light('sessions','sessions',sum.sessions_open,'#3ec8d8','live sessions derived from the signed log');
  h+=light('admitted','admitted',sum.admitted,admittedCol,'unexpired + unrevoked admissions'+(admittedCol==='#e0a852'?' — some sessions are NOT admitted':''));
  h+=light('leases','leases',sum.run_leases_held,'#92c4ac','run leases held — one in flight per folder·workflow, a second is refused');
  h+=light('needsyou','needs you',needs,needs>0?'#e2554a':'#4fbe8b','escalations — a human is in the loop');
  h+='<span class="gs-alarm" data-armed="'+(first?'true':'false')+'"'+(first?' data-name="'+escA(firstName)+'"':'')
    +' style="display:inline-flex;align-items:center;gap:5px;border-left:1px solid var(--line);padding-left:9px;color:'+(first?((VERDICT[first.verdict]||{}).col||'#e2554a'):'var(--txt-dim)')+'">'
    +'<span style="width:8px;height:8px;border-radius:50%;background:'+(first?((VERDICT[first.verdict]||{}).col||'#e2554a'):'var(--line)')+(first?';box-shadow:0 0 7px currentColor':'')+'"></span>'
    +(first?esc(firstName):'all green')+'</span>';
  strip.innerHTML=h;
}

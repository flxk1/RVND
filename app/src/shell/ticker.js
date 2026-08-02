/* Live Audit Ticker — the instrument's readout, a non-modal strip along the
   bottom that streams recent SIGNED events (workspace_audit op=tail),
   read-only, server-fed. Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. */
let _tickTimer=null;
function verdictLamp(v){ const s=(v||'').toString().toLowerCase();
  if(/permit|^go$|allow/.test(s)) return '#92c4ac';
  if(/hold|ask|review|pending/.test(s)) return '#e6b483';
  if(/deny|no-?go|block|refus/.test(s)) return '#d98b8b';
  return '#5b6472'; // no verdict on this event kind — neutral, not a fake green
}
function toggleTicker(){
  let strip=document.getElementById('tickerstrip');
  if(strip){ strip.remove(); if(_tickTimer){clearInterval(_tickTimer);_tickTimer=null;} return; }
  strip=document.createElement('div'); strip.id='tickerstrip'; strip.setAttribute('role','region');
  strip.setAttribute('aria-label','Live audit ticker — recent signed events'); strip.setAttribute('aria-live','polite');
  strip.style.cssText='position:absolute;left:0;right:0;bottom:0;height:48px;display:flex;align-items:center;gap:9px;overflow-x:auto;padding:0 12px;background:linear-gradient(180deg,var(--panel),var(--panel-2));border-top:2px solid #2c333f;z-index:8;font-family:"IBM Plex Mono",monospace;font-size:11px';
  strip.innerHTML='<span style="color:var(--txt-dim)">loading…</span>';
  stage.appendChild(strip);
  loadTicker();
  _tickTimer=setInterval(loadTicker,4000);
}
async function loadTicker(){
  const strip=document.getElementById('tickerstrip'); if(!strip) return;
  if(!S.path){ strip.innerHTML='<span style="color:var(--txt-dim)">open a workspace to watch its signed record</span>'; return; }
  let r; try{ r=await tool('workspace_audit',{op:'tail',params:{folder_context:S.path,limit:24}}); }catch(e){ strip.innerHTML='<span style="color:#d98b8b">ticker offline — '+esc((e&&e.message)||'failed')+'</span>'; return; }
  if(r&&r.error){ strip.innerHTML='<span style="color:#d98b8b">'+esc(r.error)+'</span>'; return; }
  const evs=((r&&r.events)||[]).slice().reverse(); // newest first
  if(!evs.length){ strip.innerHTML='<span style="color:var(--txt-dim)">no signed events yet — wire something and run it</span>'; return; }
  // meter-bridge tally — a discrete count per verdict over this window (read-only,
  // computed from the events; the "meters" of the console). Spans only — no buttons.
  const order=['auto','human','refused','reserved','prohibited','unfired'];
  const tally={}; let none=0; evs.forEach(e=>{ const v=e.verdict; if(v&&(v in VERDICT)) tally[v]=(tally[v]||0)+1; else none++; });
  const meterChips=order.filter(v=>tally[v]).map(v=>{ const inf=VERDICT[v];
    return '<span class="mtr" title="'+escA(esc(v+(inf&&inf.label?(' — '+inf.label):'')))+'"><span style="width:7px;height:7px;border-radius:50%;background:'+((inf&&inf.col)||'#5a616f')+';display:inline-block"></span><b style="color:var(--txt)">'+tally[v]+'</b><span style="color:var(--txt-dim);font-size:9px">'+esc(v)+'</span></span>'; });
  if(none) meterChips.push('<span class="mtr" title="events with no verdict"><span style="width:7px;height:7px;border-radius:50%;background:#5a616f;display:inline-block"></span><b style="color:var(--txt)">'+none+'</b><span style="color:var(--txt-dim);font-size:9px">no verdict</span></span>');
  const bridge='<span class="mbcap">meter bridge · last '+evs.length+'</span><span class="mbmeters" role="img" aria-label="verdict mix in the last '+evs.length+' signed events">'+meterChips.join('')+'</span><span class="mbsep"></span>';
  strip.innerHTML=bridge+'<span style="color:var(--txt-dim);white-space:nowrap">signed record →</span>'+evs.map(e=>{
    const lamp=((VERDICT[e.verdict]||{}).col)||verdictLamp(e.verdict); const t=(e.ts||'').replace('T',' ').replace('Z','');
    const lbl=(e.event||'event')+(e.state?'·'+e.state:'');
    const ttl=(e.verdict?('verdict '+e.verdict+' · '):'no verdict on this event · ')+'actor '+(e.actor||'?')+(e.signed?' · signed':' · UNSIGNED')+' · audit '+(e.audit_id||'')+' — click to open the record';
    return '<span class="tchip" tabindex="0" role="button" title="'+escA(esc(ttl))+'" onclick="openAuditPanel()" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openAuditPanel();}" style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap;border:1px solid var(--line);border-radius:7px;padding:3px 8px;cursor:pointer">'
      +'<span style="width:8px;height:8px;border-radius:50%;background:'+lamp+';flex:none"></span>'
      +'<b style="color:var(--txt)">'+esc(lbl)+'</b><span style="color:var(--txt-dim)">'+esc(e.actor||'')+'</span>'
      +(e.signed?'':'<span style="color:#d98b8b">⚠ unsigned</span>')
      +'<span style="color:var(--txt-dim);font-size:9px">'+esc(t)+'</span></span>';
  }).join('');
}

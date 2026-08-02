/* Knob bindings — MIDI-learn knob-to-governance bindings + All-Stop.
   A knob is a REQUEST, not authority: MIDI in -> the UI asks the server; the
   server decides + clamps. Lamps show the SERVER value. Tighten is instant;
   loosen confirms + is recorded. Bindings are DISCRETE steps; a CC never sets a
   continuous value on a reserved control. Bindings persist client-side only.
   Shell chrome, not plane-provided content: loaded unconditionally by
   compose_classic(), no panel-mount registration. (allStopAll — the
   workspace rail's master All-Stop — is not controller-private: it lives in
   app/src/shell/wsrail.js alongside the rest of the rail.) */
const _CTRL_LS='rvnd.controller.bindings.v1';
let _midiLearn=null, _midiHooked=null;
const _CTRL_ACTIONS={
  ov_tighten:{label:'Oversight — tighten',dir:'tighten'},
  ov_loosen:{label:'Oversight — loosen',dir:'loosen'},
  floor_up:{label:'Grounding floor — raise',dir:'tighten'},
  floor_down:{label:'Grounding floor — lower',dir:'loosen'},
  all_stop:{label:'ALL-STOP (suspend every active party)',dir:'tighten'}};
function _ctrlBindings(){ try{ return JSON.parse(localStorage.getItem(_CTRL_LS)||'[]'); }catch(_){ return []; } }
function _ctrlSave(b){ try{ localStorage.setItem(_CTRL_LS,JSON.stringify(b)); }catch(_){ } }
async function openControllerPanel(){
  let cp=document.getElementById('ctrlpanel'); if(cp){ cp.remove(); return; }
  const opener=document.activeElement;
  cp=document.createElement('div'); cp.id='ctrlpanel'; cp.setAttribute('role','dialog'); cp.setAttribute('aria-label','Knob bindings — MIDI knobs + All-Stop');
  cp.style.cssText='position:absolute;top:12px;right:12px;width:460px;max-width:94%;max-height:86%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  cp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Knob bindings — MIDI knobs + All-Stop</b><span style="flex:1"></span><span id="ctlx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">A knob is a <b>request</b>, never authority. MIDI in → the UI asks the server; the <b>server</b> decides and clamps. Lamps show the server value. Tighten is instant; loosen confirms and is recorded.</div>'
    +'<button class="del" id="ctl-allstop" style="width:100%;margin:4px 0 10px;padding:10px;border-color:#cf463c;color:#fff;background:#3a1d1b;font-weight:600">■ ALL-STOP — suspend every active party</button>'
    +'<div id="ctl-state" class="ro" style="font-size:11px"></div><div id="ctl-midi" class="ro" style="font-size:11px;margin-top:8px"></div><div id="ctl-bindings" style="margin-top:8px"></div>';
  stage.appendChild(cp);
  const restore=modalize(cp,opener);
  const close=()=>{cp.remove();restore();}; const x=cp.querySelector('#ctlx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  cp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  cp.querySelector('#ctl-allstop').addEventListener('click',()=>allStop('on-screen button'));
  await loadController(); await initMidi();
}
async function loadController(){
  const st=document.getElementById('ctl-state'); if(!st) return;
  if(!S.path){ st.innerHTML='<span style="color:var(--txt-dim)">open a workspace to drive its governance</span>'; renderBindings(); return; }
  const get=async(t,op,extra)=>{ try{ return await tool(t,{op:op,params:Object.assign({folder_context:S.path},extra||{})}); }catch(e){ return {error:(e&&e.message)||'failed'}; } };
  const [pol,thr,pl]=await Promise.all([get('workspace_policy','snapshot'),get('workspace_lock','threshold_get'),get('workspace_policy','party_list')]);
  const ovl=(pol&&pol.oversight_default_level)||'?';
  const floor=(thr&&!thr.error&&thr.ok!==false)?Number(thr.threshold||0):null;
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active');
  const lamp=(c)=>'<span style="width:8px;height:8px;border-radius:50%;display:inline-block;background:'+c+';margin-right:6px"></span>';
  let h='';
  h+='<div style="margin:3px 0">'+lamp(ovl==='?'?'#5b6472':'#92c4ac')+'Oversight (server) <b style="color:#fff">'+esc(ovl)+'</b></div>';
  h+='<div style="margin:3px 0">'+lamp(floor==null?'#5b6472':(floor>0?'#e6b483':'#5b6472'))+'Grounding floor (server) <b style="color:#fff">'+(floor==null?'unread':(floor<=0?'no filter':esc(floor.toFixed(2))))+'</b></div>';
  h+='<div style="margin:3px 0">'+lamp(active.length?'#92c4ac':'#5b6472')+'Active parties <b style="color:#fff">'+esc(active.length)+' / '+esc(arr.length)+'</b></div>';
  h+='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">These lamps are the granted state read back from the server — a knob never sets them directly.</div>';
  st.innerHTML=h; renderBindings();
}
async function ctrlSubmit(key,src){
  const a=_CTRL_ACTIONS[key]; if(!a||!S.path) return;
  const via=src?(' via '+src):'';
  if(a.dir==='loosen'){ if(!confirm('Loosen “'+a.label+'”? This raises autonomy / lowers a guard — it round-trips to the server and is recorded.')) return; }
  try{
    if(key==='all_stop'){ await allStop(src||'controller'); return; }
    if(key==='ov_tighten'||key==='ov_loosen'){
      const pol=await tool('workspace_policy',{op:'snapshot',params:{folder_context:S.path}});
      if(pol&&(pol.error||pol.ok===false)){ await loadController(); announce('could not read oversight level — request not sent'); return; }
      const cur=(pol&&pol.oversight_default_level)||'approve';
      let i=_OV_ORDER.indexOf(cur); if(i<0)i=_OV_ORDER.indexOf('approve');
      const want=(key==='ov_tighten')?Math.min(_OV_ORDER.length-1,i+1):Math.max(0,i-1); const lvl=_OV_ORDER[want];
      if(lvl===cur){ announce('oversight already at '+cur); await loadController(); return; }
      const r=await tool('workspace_policy',{op:'set_oversight_level',params:{folder_context:S.path,level:lvl,actor:'app-user'}});
      await loadController(); announce((r&&(r.ok===false||r.error))?('could not set oversight: '+esc(r.error||'failed')):('oversight → '+esc(lvl)+' (server granted)'+via));
    } else if(key==='floor_up'||key==='floor_down'){
      const cur=await tool('workspace_lock',{op:'threshold_get',params:{folder_context:S.path}});
      if(cur&&(cur.error||cur.ok===false)){ await loadController(); announce('could not read the grounding floor — request not sent'); return; }
      const c=Number((cur&&cur.threshold)||0);
      let want=(key==='floor_up')?c+0.1:c-0.1; want=Math.max(0,Math.min(1,want));
      const r=await tool('workspace_lock',{op:'threshold_set',params:{folder_context:S.path,threshold:want,actor:'app-user'}});
      await loadController();
      if(r&&(r.ok===false||r.error)) announce('could not set floor: '+esc(r.error||'failed'));
      else announce('grounding floor → '+Number(r.threshold).toFixed(2)+' (server granted)'+via);   // granted from the server, never the request
    }
  }catch(e){ await loadController(); announce('controller request failed: '+((e&&e.message)||'error')); }
}
async function allStop(src){
  if(!S.path){ announce('open a workspace first'); return; }
  let pl; try{ pl=await tool('workspace_policy',{op:'party_list',params:{folder_context:S.path}}); }catch(e){ announce('ALL-STOP could not read parties: '+((e&&e.message)||'error')); return; }
  if(pl&&(pl.error||pl.ok===false)){ announce('ALL-STOP could not read parties: '+esc(pl.error||'failed')+' — nothing suspended'); await loadController(); return; }
  const arr=Array.isArray(pl)?pl:((pl&&(pl.parties||pl.rows))||[]); const active=arr.filter(p=>(p.status||'active')==='active');
  if(!active.length){ announce('ALL-STOP: no active party to suspend'); await loadController(); return; }
  let n=0; for(const p of active){ const pid=p.party_id||p.id; if(!pid) continue; try{ await tool('workspace_policy',{op:'party_status',params:{folder_context:S.path,party_id:pid,status:'suspended',actor:'app-user',reason:'ALL-STOP ('+(src||'panic')+')'}}); n++; }catch(_){ } }
  await loadController(); announce('ALL-STOP: suspended '+n+' of '+active.length+' active part'+(active.length===1?'y':'ies')+' — each signed');
}
async function initMidi(){
  const box=document.getElementById('ctl-midi'); if(!box) return;
  if(!navigator.requestMIDIAccess){ box.innerHTML='<div style="color:#e6b483;font-size:11px"><b>No Web MIDI access</b> — bindings disabled, but ALL-STOP and the on-screen Fire buttons still work.</div>'; return; }
  try{ const acc=await navigator.requestMIDIAccess({sysex:false}); box.innerHTML='<div style="font-size:11px;color:#92c4ac">MIDI ready — listening to any device. Hit <b>Learn</b> on a binding, then move a knob or pad.</div>';
    const hook=inp=>{ if(_midiHooked&&_midiHooked.has(inp)) return; (_midiHooked=_midiHooked||new WeakSet()).add(inp); inp.onmidimessage=onMidi; };
    acc.inputs.forEach(hook); acc.onstatechange=e=>{ if(e.port&&e.port.type==='input'&&e.port.state==='connected') hook(e.port); };
  }catch(e){ box.innerHTML='<div style="color:#e6b483;font-size:11px"><b>MIDI access denied</b> ('+esc((e&&e.message)||'denied')+') — ALL-STOP + Fire still work.</div>'; }
}
function onMidi(ev){
  const d=ev.data||[]; const type=(d[0]&255)&0xf0, d1=d[1]|0, d2=d[2]|0;
  const isCC=(type===0xb0), isNote=(type===0x90&&d2>0); if(!isCC&&!isNote) return;
  const note=(isCC?'cc':'note')+':'+d1;
  if(_midiLearn){ const k=_midiLearn; _midiLearn=null; const b=_ctrlBindings().filter(x=>x.note!==note); b.push({note:note,action:k}); _ctrlSave(b); renderBindings(); announce('bound '+note+' → '+(_CTRL_ACTIONS[k]?_CTRL_ACTIONS[k].label:k)); return; }
  const bind=_ctrlBindings().find(x=>x.note===note); if(!bind) return; ctrlSubmit(bind.action,'MIDI '+note);
}
function renderBindings(){
  const wrap=document.getElementById('ctl-bindings'); if(!wrap) return; const noMidi=!navigator.requestMIDIAccess;
  const binds=_ctrlBindings(); const by={}; binds.forEach(b=>{(by[b.action]=by[b.action]||[]).push(b.note);});
  let h='<b style="font-size:12px">Bindings</b><div class="ro" style="font-size:10px;color:var(--txt-dim);margin:2px 0 6px">Each is a discrete governance step or trigger — never a continuous dial.</div>';
  for(const k of Object.keys(_CTRL_ACTIONS)){ const a=_CTRL_ACTIONS[k]; const notes=by[k]||[];
    const chip=a.dir==='tighten'?'<span class="gradechip" style="background:#5aa886">tighten · instant</span>':'<span class="gradechip" style="background:#c8a23f">loosen · recorded</span>';
    h+='<div class="psrow" data-action="'+esc(k)+'"><div style="display:flex;gap:8px;align-items:center"><span style="flex:1;font-size:11px">'+esc(a.label)+'</span>'+chip+'</div><div style="display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap"><button class="psbtn ctl-learn" data-act="'+esc(k)+'"'+(noMidi?' disabled':'')+'>Learn</button><button class="psbtn ctl-fire" data-act="'+esc(k)+'">Fire</button><span style="flex:1"></span>'+(notes.length?notes.map(n=>'<span class="gradechip" style="background:#21252e;color:#cdd2dc">'+esc(n)+' <span class="ctl-rm" role="button" tabindex="0" data-note="'+escA(n)+'" style="cursor:pointer;color:#e2554a">✕</span></span>').join(''):'<span class="ro" style="font-size:10px;color:var(--txt-dim)">unbound</span>')+'</div></div>';
  }
  wrap.innerHTML=h;
  wrap.querySelectorAll('.ctl-learn').forEach(b=>b.addEventListener('click',()=>{ if(!navigator.requestMIDIAccess){ announce('no MIDI access'); return; } _midiLearn=b.dataset.act; announce('learning '+(_CTRL_ACTIONS[_midiLearn]?_CTRL_ACTIONS[_midiLearn].label:_midiLearn)+' — move a knob or hit a pad'); }));
  wrap.querySelectorAll('.ctl-fire').forEach(b=>b.addEventListener('click',()=>ctrlSubmit(b.dataset.act,'on-screen')));
  wrap.querySelectorAll('.ctl-rm').forEach(s=>{ const rm=()=>{ const note=s.dataset.note; _ctrlSave(_ctrlBindings().filter(x=>x.note!==note)); renderBindings(); announce('removed binding '+note); }; s.addEventListener('click',rm); s.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();rm();}}); });
}

// Environment save/open (signed .rvnd bundle: build + verify + adopt) — shell
// chrome, not plane-provided content: loaded unconditionally by compose_classic(),
// no panel-mount registration.
/* Session save/load — a .rvnd file = the .lg patch (governed structure) + a
   layout sidecar (positions, router nodes, solo, view). The governance record
   itself lives in the workspace's signed log and is never overwritten by a file;
   loading restores the LAYOUT instantly, and only APPLIES the .lg patch to the
   chain on explicit confirm (a recorded write). Save/Open use the File System
   Access API when present, else download + a file-input. */
function _downloadText(name,text){ try{ const b=new Blob([text],{type:'application/json'}); const u=URL.createObjectURL(b); const a=document.createElement('a'); a.href=u; a.download=name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),1000); }catch(e){ announce('Download failed: '+((e&&e.message)||'')); } }
function _pickFileText(){ return new Promise(res=>{ const inp=document.createElement('input'); inp.type='file'; inp.accept='.rvnd,.json,application/json'; inp.style.display='none'; document.body.appendChild(inp); inp.addEventListener('change',async()=>{ const f=inp.files&&inp.files[0]; inp.remove(); if(!f){ res(null); return; } try{ res(await f.text()); }catch(_){ res(null); } }); inp.click(); }); }
// Session = the signed environment, the ONE .rvnd format (the old single-patch
// layout-session was retired: env save captures layout too, in `presentation`).
// Chains/configs are captured server-side; the browser holds the signed file.
//
// A stable, filesystem-safe workspace id derived from its path (NOT the path
// itself: restore does dest_root/<id>, and an absolute id would override
// dest_root and overwrite the original folder). Deterministic per path.
function _wsId(p){ p=(p||'').replace(/\/+$/,''); const base=(p.split('/').pop()||'ws').replace(/[^\w.-]/g,'_');
  let h=0; for(let i=0;i<p.length;i++){ h=(h*31+p.charCodeAt(i))|0; } return base+'-'+((h>>>0).toString(36)); }
function _envWorkspaces(){
  const list=(LIVE&&workspaces.length)?workspaces:(S.path?[{path:S.path}]:[]);
  return list.map(w=>({folder_context:w.path, id:_wsId(w.path), name:bn(w.path)||w.path,
    presentation:(window.POS&&window.POS[w.path])||{}}));
}
async function saveEnvironment(){
  const wss=_envWorkspaces();
  if(!wss.length){ announce('No workspace to save.'); return null; }
  // rail references workspace IDS (not paths); focused MUST be one of them or
  // referential integrity (S4) rejects the bundle.
  const sid=_wsId(S.path);
  const focused=wss.some(w=>w.id===sid)?sid:wss[0].id;
  const rail={ order:wss.map(w=>w.id), focused,
    global_view:{view:window._view||'patch'}, view_solo:[...(window._solo||new Set())] };
  let r; try{ r=await tool('workspace_session',{op:'build',params:{workspaces:wss,rail,name:'environment'}}); }
  catch(e){ r={ok:false,error:(e&&e.message)||'failed'}; }
  if(!r||!r.ok){ announce('Save env failed: '+esc((r&&r.error)||'unknown')); return null; }
  _downloadText('environment.rvnd', JSON.stringify(r.bundle,null,1));
  const n=(r.card&&r.card.workspace_count)||wss.length;
  announce('Saved environment — '+n+' workspace'+(n===1?'':'s')+', signed'+(window.showSaveFilePicker?'':' (downloaded)'));
  return r;
}
// The Open dialog (S5): a real modal replacing confirm() — verify banner +
// provenance + the replace guard; the view-only variant (no Open button) for a
// cross-machine session. Resolves 'replace' or null (Cancel / view-only).
function _envOpenDialog(card,cont){
  return new Promise(resolve=>{
    const vo=!(cont&&cont.continuable);
    const n=(workspaces||[]).length;
    const chips=((card.workspaces)||[]).map(w=>'<span style="border:1px solid var(--line);border-radius:6px;padding:2px 8px;font-size:10px">'+esc(w.name||w.id)+'</span>').join(' ');
    const el=document.createElement('div'); el.className='modal-scrim'; el.id='envopendlg';
    el.setAttribute('role','dialog'); el.setAttribute('aria-label','Open environment');
    el.style.cssText='position:absolute;inset:0;z-index:200;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center';
    el.innerHTML='<div style="background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:15px 16px;width:min(440px,92vw)">'
      +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:14px;color:#f3f1ea">Open environment</b>'
      +(vo?'<span class="robadge" style="color:#e3a877;border-color:#7a5a36;background:#241a0e">view-only</span>':'<span class="robadge">verify</span>')+'</div>'
      +'<div style="font-family:Space Grotesk,sans-serif;font-weight:600;color:#f3f1ea;font-size:12.5px">'+esc(card.name||'session')+'</div>'
      +'<div style="font-size:10px;color:var(--txt-dim);margin:2px 0 9px">'+(card.signed_by?('signed by '+esc(card.signed_by)+' · '):'')+'role '+esc(card.origin_role||'user')+(card.parent_version?' · from a prior version':'')+'</div>'
      +'<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid #3f6e5c;background:#16241f;border-radius:7px;margin-bottom:9px;color:#7bc0a3;font-size:10.5px"><span style="width:8px;height:8px;border-radius:50%;background:#5aa886;flex:none"></span>signature verified · '+((card.workspace_count)||0)+' chain'+((card.workspace_count===1)?'':'s')+' intact</div>'
      +(chips?'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">'+chips+'</div>':'')
      +(vo
        ? '<div style="font-size:10px;color:#e3a877;margin-bottom:11px;line-height:1.5">Signed on another machine — view-only here (decision B). Open it on the origin machine to keep going.</div>'
        : '<div style="font-size:10px;color:#e3a877;background:#241a0e;border:1px solid #7a5a36;border-radius:6px;padding:6px 9px;margin-bottom:11px">▲ replaces your current environment ('+n+' workspace'+(n===1?'':'s')+') — your folders stay on disk (recoverable)</div>')
      +'<div style="display:flex;gap:8px;align-items:center">'
      +(vo?'':'<button class="tool" id="envdlgok" style="border-color:#3f6e5c;background:#16241f;color:#7bc0a3">Open (replace)</button>')
      +'<span style="flex:1"></span><button class="tool" id="envdlgcancel">'+(vo?'Close':'Cancel')+'</button></div>'
      +'</div>';
    document.body.appendChild(el);
    const done=(x)=>{ try{ el.remove(); }catch(_){ } resolve(x); };
    const ok=el.querySelector('#envdlgok'); if(ok) ok.addEventListener('click',()=>done('replace'));
    el.querySelector('#envdlgcancel').addEventListener('click',()=>done(null));
    el.addEventListener('click',(e)=>{ if(e.target===el) done(null); });   // scrim click cancels
  });
}
// Open a signed environment (S5): upload -> verify_bytes (shows continue vs
// view-only per decision B) -> adopt (replace/beside). Adopt is a non-destructive
// registry swap; the current folders stay on disk. Pass `bundle` to bypass the
// file picker (tests). Returns the adopt/verify result.
async function openEnvironment(bundle, mode){
  if(!bundle){
    let text=null;
    if(window.showOpenFilePicker){ try{ const [h]=await window.showOpenFilePicker({types:[{description:'Rvnd environment',accept:{'application/json':['.rvnd','.json']}}]}); text=await (await h.getFile()).text(); }catch(e){ return null; } }
    else { text=await _pickFileText(); }
    if(!text) return null;
    try{ bundle=JSON.parse(text); }catch(e){ announce('Not a valid session file.'); return null; }
  }
  let v; try{ v=await tool('workspace_session',{op:'verify_bytes',params:{bundle}}); }catch(e){ v={ok:false,error:(e&&e.message)||'failed'}; }
  if(!v||!v.ok){ announce('Session refused: '+esc((v&&v.report&&v.report.refusal&&v.report.refusal.detail)||(v&&v.error)||'failed')); return v; }
  const card=v.card||{}, cont=v.continuation||{};
  // a mode passed explicitly (tests / programmatic) skips the dialog; otherwise
  // show the Open dialog — it renders the verify + provenance + replace guard,
  // and the view-only variant (no Open button) for a cross-machine session.
  const decision = (mode==='replace'||mode==='beside') ? mode : await _envOpenDialog(card,cont);
  if(!decision) return v;                    // Cancel, or view-only (no adopt)
  const first=(workspaces&&workspaces[0]&&workspaces[0].path)||S.path;
  if(!first){ announce('Open a workspace first (need a base folder to restore into).'); return v; }
  const base=first.replace(/\/+$/,'').replace(/\/[^/]+$/,'');
  const dest=base+'/rvnd-restored/'+((card.name||'session').replace(/[^\w.-]/g,'_'))+'-'+((card.version||'').replace(/[^\w]/g,'').slice(6,16));
  let a; try{ a=await tool('workspace_session',{op:'adopt',params:{bundle,dest_root:dest,mode:decision}}); }catch(e){ a={ok:false,error:(e&&e.message)||'failed'}; }
  if(!a||!a.ok){ announce('Open failed: '+esc((a&&a.report&&a.report.refusal&&a.report.refusal.detail)||(a&&a.error)||'failed')); return a; }
  const adopted=Object.keys(a.adopted||{}).length, retired=(a.retired||[]).length;
  announce('Opened “'+esc(card.name||'session')+'” — '+adopted+' workspace'+(adopted===1?'':'s')+' adopted'+(retired?(', '+retired+' retired (folders kept)'):''));
  await boot();   // re-read the registry so the rail switches to the adopted environment
  return a;
}

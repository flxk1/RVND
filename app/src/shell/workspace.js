/* Workspace switcher — list the workspaces you know, switch between them, or
   create a new one. Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. */
async function openWorkspacePanel(){
  let wp=document.getElementById('wspanel'); if(wp){ wp.remove(); return; }
  const opener=document.activeElement;
  wp=document.createElement('div'); wp.id='wspanel'; wp.setAttribute('role','dialog'); wp.setAttribute('aria-label','Workspace — the workspaces you know, and make a new one');
  wp.style.cssText='position:absolute;top:12px;right:12px;width:460px;max-width:94%;max-height:86%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  wp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Workspace — your workspaces</b><span style="flex:1"></span><span id="wskx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">A workspace is a governance space — a folder. Switch between the ones you know, or make a new one (a forward action).</div>'
    +'<div id="wskout"><div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div></div>';
  stage.appendChild(wp);
  const restore=modalize(wp,opener);
  const close=()=>{wp.remove();restore();}; const x=wp.querySelector('#wskx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  wp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  loadWorkspace();
}
async function loadWorkspace(){
  const out=document.getElementById('wskout'); if(!out) return;
  let r; try{ r=await tool('workspace_workspace',{op:'list'}); }catch(e){ out.innerHTML='<div class="finding bad"><span class="ttl">Could not list workspaces</span>'+esc((e&&e.message)||'failed')+'</div>'; return; }
  const ws=(r&&r.workspaces)||[], def=(r&&r.default)||'';
  let h='<div class="psrow"><b style="font-size:12px">Known workspaces</b>';
  if(ws.length) h+='<div style="margin-top:4px">'+ws.map((w,i)=>{ const here=(S.path&&w.path===S.path); return '<div style="display:flex;gap:8px;align-items:center;font-size:11px;border-top:1px solid var(--line);padding:4px 0"><span>'+esc(bn(w.path||'?'))+'</span>'+(w.label?' <span style="color:var(--txt-dim)">'+esc(w.label)+'</span>':'')+(w.path===def?' <span class="pson" style="font-size:10px">default</span>':'')+'<span style="flex:1"></span>'+(here?'<span class="pson" style="font-size:11px">current</span>':'<button class="psbtn" data-sw="'+i+'">Switch</button>')+'</div>'; }).join('')+'</div>';
  else h+='<div class="ro" style="font-size:11px;color:var(--txt-dim);margin-top:4px">no workspaces yet — make one below</div>';
  h+='</div><div class="psrow"><b style="font-size:12px">New workspace</b><div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin:3px 0 6px">give it a full path — a new folder is created and registered</div>'
    +'<input type="text" id="wsnew" placeholder="/path/to/my-workspace" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
    +'<input type="text" id="wslabel" placeholder="label (optional)" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
    +'<button class="psbtn" id="wscreate" style="width:100%">+ Create workspace &amp; switch</button></div>';
  out.innerHTML=h; window._wsList=ws;
  out.querySelectorAll('[data-sw]').forEach(b=>b.addEventListener('click',async()=>{ const i=+b.dataset.sw; out.querySelectorAll('button,input').forEach(el=>el.disabled=true); window._fi=i; const sel=document.getElementById('folder'); if(sel) sel.selectedIndex=i; let okb=true; try{ await boot(); }catch(_){ okb=false; } announce(okb?('Switched to workspace '+bn((ws[i]||{}).path||'')+'.'):('Switched the selection, but the workspace failed to load — its record may be unavailable.')); loadWorkspace(); }));
  const cb=out.querySelector('#wscreate'); if(cb) cb.addEventListener('click',async()=>{
    const path=((out.querySelector('#wsnew')||{}).value||'').trim(), label=((out.querySelector('#wslabel')||{}).value||'').trim();
    if(!path){ announce('enter a path for the new workspace'); return; }
    out.querySelectorAll('button,input').forEach(el=>el.disabled=true);
    try{ const cr=await tool('workspace_folder',{op:'create',params:{path:path}}); if(cr&&(cr.error||cr.ok===false)) throw new Error(cr.error||'could not create folder'); const resolved=(cr&&cr.path)||path;
      const ad=await tool('workspace_workspace',{op:'add',params:{folder_context:resolved,label:label}}); if(ad&&(ad.error||ad.ok===false)) throw new Error(ad.error||'could not register'); const fp=(ad&&ad.path)||resolved;
      const after=await tool('workspace_workspace',{op:'list'}); const list=(after&&after.workspaces)||[]; const idx=list.findIndex(w=>w.path===fp);
      const sel=document.getElementById('folder'); if(idx>=0){ window._fi=idx; if(sel) sel.selectedIndex=idx; await boot(); announce('Workspace created and switched: '+bn(fp)+'.'); }
      else announce('Workspace created and registered ('+bn(fp)+') — pick it from the Workspace list to switch.');
      loadWorkspace();
    }catch(e){ announce('Could not create workspace: '+((e&&e.message)||'failed')); loadWorkspace(); }
  });
}

/* Editor — the third surface (canvas ⇄ netlist ⇄ chain). Type/edit a v0.5
   .lg; Validate is fail-closed; nothing applies until you Apply. Shell
   chrome, not plane-provided content: loaded unconditionally by
   compose_classic(), no panel-mount registration. */
async function openEditorPanel(){
  let ep=document.getElementById('editpanel'); if(ep){ ep.remove(); return; }
  const opener=document.activeElement;
  ep=document.createElement('div'); ep.id='editpanel'; ep.setAttribute('role','dialog'); ep.setAttribute('aria-label','Edit the patch as a .lg netlist');
  ep.style.cssText='position:absolute;left:12px;bottom:12px;width:460px;max-width:92%;max-height:84%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  ep.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Edit patch — .lg</b><span style="flex:1"></span><span id="edx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">Type a patch (actor / human / gate / cord). Validate is fail-closed; nothing applies until you Apply.</div>'
    +'<textarea id="edtext" aria-label="Patch netlist" spellcheck="false" style="width:100%;height:150px;background:var(--panel-2);border:1px solid var(--line);color:#fff;font-family:IBM Plex Mono,monospace;font-size:12px;border-radius:6px;padding:8px"></textarea>'
    +'<div style="display:flex;gap:6px;margin-top:8px"><button class="del" id="edload" style="flex:1">Load current</button><button class="del" id="edval" style="flex:1;border-color:#3a3357;color:#b9acff">Validate</button><button class="del" id="edapply" style="flex:1;border-color:#2f4a3a;color:#8fd1ad">Apply</button></div>'
    +'<div id="edout" role="status" aria-live="polite" style="margin-top:8px"></div>';
  stage.appendChild(ep);
  const restore=modalize(ep,opener);
  const close=()=>{ep.remove();restore();}; const x=ep.querySelector('#edx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  ep.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  ep.querySelector('#edload').addEventListener('click',editorLoad);
  ep.querySelector('#edval').addEventListener('click',editorValidate);
  ep.querySelector('#edapply').addEventListener('click',editorApply);
}
async function editorLoad(){
  const ta=document.getElementById('edtext'); if(!ta||!S.path) return;
  try{ const r=await tool('workspace_workflow',{op:'governance_netlist',params:{folder_context:S.path}}); ta.value=r.netlist||'';
    const out=document.getElementById('edout'); if(out)out.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim)">Loaded structure only — reservations and other declarations live on the chain and aren’t shown here.</div>'; }catch(e){}
}
async function editorValidate(){
  const out=document.getElementById('edout'), ta=document.getElementById('edtext'); if(!out||!ta) return;
  let r; try{ r=await tool('workspace_workflow',{op:'patch_validate',params:{folder_context:S.path||'',netlist:ta.value}}); }
  catch(e){ window._edvalid=false; out.innerHTML='<div class="ro">err: '+esc(e.message)+'</div>'; return; }
  window._edvalid=!!(r&&r.ok);   // observable validate outcome (test/automation hook, like window._twin)
  if(r&&r.ok){ const pr=r.projection||{}; out.innerHTML='<div class="finding ok"><span class="ttl">✓ well-formed</span>'+((pr.nodes||[]).length)+' nodes · '+((pr.cords||[]).length)+' cords</div>'; }
  else out.innerHTML='<div class="finding bad"><span class="ttl">✗ '+((r&&r.stage==='parse')?'parse error':'ill-formed')+'</span>'+esc(((r&&r.errors)||['unknown']).join('; '))+'</div>';
}
async function editorApply(){
  const out=document.getElementById('edout'), ta=document.getElementById('edtext'); if(!out||!ta) return;
  if(!S.path){ out.innerHTML='<div class="ro" style="color:var(--txt-dim)">open a folder to apply</div>'; return; }
  let r; try{ r=await tool('workspace_workflow',{op:'patch_apply',params:{folder_context:S.path,actor:'app-user',netlist:ta.value}}); }
  catch(e){ r={ok:false,errors:[e.message]}; }
  if(!r||r.ok===false){ out.innerHTML='<div class="finding bad"><span class="ttl">Apply failed — nothing written</span>'+esc(((r&&r.errors)||['unknown']).join('; '))+'</div>'; return; }
  out.innerHTML='<div class="finding ok"><span class="ttl">✓ applied</span>written to the chain.</div>';
  await reload();
}

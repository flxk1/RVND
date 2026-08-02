/* workspace_matrix — the autonomy(grade) × oversight policy grid. go=on its own,
   ask=a person signs off, block=not allowed. Click a cell to TIGHTEN one step
   (instant, safe direction); loosening is only via Reset-to-inherited (a
   deliberate, confirmed action — fail-safe direction). The server follows this
   grid; the client renders + requests, it never decides a verdict. */
const _LIGHT_NEXT={go:'ask',ask:'block',block:null};
async function openMatrixPanel(){
  let mp=document.getElementById('matrixpanel'); if(mp){ mp.remove(); return; }
  const opener=document.activeElement;
  mp=document.createElement('div'); mp.id='matrixpanel'; mp.setAttribute('role','dialog'); mp.setAttribute('aria-label','Autonomy by oversight policy matrix');
  mp.style.cssText='position:absolute;top:12px;left:12px;width:520px;max-width:94%;max-height:86%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  mp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Policy matrix — autonomy × oversight</b><span style="flex:1"></span><span id="mxx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">How an action at each autonomy level is handled at each oversight setting. <b style="color:#5aa886">go</b> = on its own · <b style="color:#c8a23f">ask</b> = a person signs off · <b style="color:#cf463c">block</b> = not allowed. Click a cell to <b>tighten</b> (go→ask→block) — instant. Loosening isn’t offered here; <b>Reset</b> restores the inherited floor. The server follows this grid; it still decides each verdict.</div>'
    +'<div id="mxout"><div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div></div>';
  stage.appendChild(mp);
  const restore=modalize(mp,opener);
  const close=()=>{mp.remove();restore();}; const x=mp.querySelector('#mxx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  mp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  loadMatrix();
}
async function loadMatrix(){
  const out=document.getElementById('mxout'); if(!out) return true;
  if(!S.path){ out.innerHTML='<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its matrix</div>'; return true; }
  let r; try{ r=await tool('workspace_matrix',{op:'show',params:{folder_context:S.path}}); }
  catch(e){ // fail-safe: do NOT destroy the grid — surface the error above the last-known view
    const banner='<div class="finding bad" style="margin-bottom:6px"><span class="ttl">Could not refresh the matrix</span>'+esc(e.message||'failed')+'</div>';
    if(out.querySelector('.mxtable')) out.insertAdjacentHTML('afterbegin',banner); else out.innerHTML=banner;
    return false;
  }
  const grades=r.grades||[], ov=r.oversight||[], m=r.matrix||{};
  let h='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-bottom:6px">'+(r.inherits?'inheriting the parent’s matrix (no local override)':'this folder has its own override')+'</div>';
  h+='<table class="mxtable" role="grid" aria-label="autonomy level by oversight setting — go, ask or block"><thead><tr><th></th>'+ov.map(o=>'<th scope="col" class="mxhdr" title="oversight: '+esc(o)+'">'+esc(o)+'</th>').join('')+'</tr></thead><tbody>';
  for(const g of grades){
    h+='<tr><th scope="row" class="mxrowh" title="autonomy '+esc(g)+'">'+esc(g)+'</th>';
    for(const o of ov){ const light=((m[g]||{})[o])||'go'; const next=_LIGHT_NEXT[light];
      h+='<td style="padding:0"><button class="mxcell mx-'+esc(light)+'" data-g="'+esc(g)+'" data-o="'+esc(o)+'" data-light="'+esc(light)+'"'+(next?'':' disabled')+' aria-label="'+esc(g)+' at '+esc(o)+': '+esc(light)+(next?', activate to tighten to '+esc(next):', strictest — cannot tighten further')+'" title="'+esc(g)+' · '+esc(o)+' · '+esc(light)+(next?' (→ '+esc(next)+')':' (strictest)')+'">'+esc(light)+'</button></td>';
    }
    h+='</tr>';
  }
  h+='</tbody></table><button class="del" id="mxreset" style="margin-top:6px;width:100%" aria-label="Reset this folder’s matrix to the inherited floor (asks to confirm)">Reset to inherited</button>';
  out.innerHTML=h;
  const freeze=()=>out.querySelectorAll('.mxcell,#mxreset').forEach(b=>{b.disabled=true;});  // no rapid-click race while a write is in flight
  out.querySelectorAll('.mxcell:not([disabled])').forEach(btn=>btn.addEventListener('click',async()=>{
    const next=_LIGHT_NEXT[btn.dataset.light]; if(!next)return; const g=btn.dataset.g,o=btn.dataset.o;
    freeze();
    let msg;
    try{ await tool('workspace_matrix',{op:'set',params:{folder_context:S.path,grade:g,oversight:o,light:next,actor:'app-user'}});
         msg=((await loadMatrix())===false)?(g+' at '+o+' tightened on the server, but the view could not refresh — reopen Matrix.'):(g+' at '+o+' tightened to '+next+'.'); }
    catch(e){ await loadMatrix(); msg='Could not update the matrix: '+((e&&e.message)||'failed'); }
    announce(msg);
  }));
  const rb=out.querySelector('#mxreset'); if(rb) rb.addEventListener('click',async()=>{
    if(!confirm('Reset this folder’s matrix to the inherited floor? This loosens any local tightening back to the parent policy.')) return;
    freeze();
    let msg;
    try{ await tool('workspace_matrix',{op:'reset',params:{folder_context:S.path,actor:'app-user'}});
         msg=((await loadMatrix())===false)?'Matrix reset on the server, but the view could not refresh.':'Matrix reset to the inherited floor.'; }
    catch(e){ await loadMatrix(); msg='Reset failed: '+((e&&e.message)||'failed'); }
    announce(msg);
  });
  return true;
}

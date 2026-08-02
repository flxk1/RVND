/* MATRIX canvas view — the coverage lens as a third view-toggle state,
   beside PATCH and ARRANGE. Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. Reads
   _loadCoverageMatrix and the cell-rendering constants that stay in
   app/src/index.html (shared with the Coverage panel-mount bundle, which
   keeps its own copy per the contract's ban on a bundle reaching into shell
   internals). Distinct from the Matrix policy drawer (app/src/shell/matrix_modal.js). */
async function renderMatrixView(){
  const host=document.getElementById('matrixview'); if(!host) return;
  if(!host.querySelector('#mxvout')){
    host.innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">'
      +'<b style="font-family:Space Grotesk,sans-serif;font-size:13px;color:#f3f1ea">Matrix — coverage as a grid</b>'
      +'<label for="mxvpreset" style="font-size:11px;color:var(--txt-dim)">Lens</label>'
      +'<select id="mxvpreset" onchange="renderMatrixView()" aria-label="matrix view lens preset" style="font-size:11px;background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:2px 6px">'
      +'<option value="kind_risk">Kind × risk — where autonomy is weak</option>'
      +'<option value="task_role">Task × role — reserved acts vs roles</option>'
      +'<option value="task_agent">Task × agent — grants (revoke here)</option></select>'
      +'<label style="font-size:11px;color:var(--txt-dim);display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="mxvgaps" onchange="mxvZoomOut(true)"> gaps only</label>'
      +'<span id="mxvstrip" style="display:none;font-size:11px;color:var(--txt)"></span></div>'
      +'<div id="mxvout"><div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div></div>';
    host.querySelector('#mxvpreset').addEventListener('change',()=>mxvZoomOut(true));
  }
  const out=host.querySelector('#mxvout');
  const preset=(host.querySelector('#mxvpreset')||{}).value||'kind_risk';
  const gapsOnly=!!(host.querySelector('#mxvgaps')||{}).checked;
  await _loadCoverageMatrix(out,{preset:preset,gapsOnly:gapsOnly,row:window._mxvRow||null});
  // zoom level one -> two: a row header is a control; activating it narrows the
  // field to that row's strip. The strip bar names the row and offers the way back.
  const bar=host.querySelector('#mxvstrip');
  if(bar){ bar.style.display=window._mxvRow?'inline':'none';
    if(window._mxvRow) bar.innerHTML='strip: <b>'+esc(window._mxvRow)+'</b> · <span id="mxvback" role="button" tabindex="0" style="cursor:pointer;border-bottom:1px dotted #2f4358">back to the field</span>';
    const back=host.querySelector('#mxvback');
    if(back){ const go=()=>mxvZoomOut(); back.addEventListener('click',go);
      back.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();go();} }); } }
  if(!window._mxvRow){
    out.querySelectorAll('.cvtable th.mxrowh').forEach(th=>{
      th.setAttribute('role','button'); th.setAttribute('tabindex','0');
      th.setAttribute('aria-label','zoom to the '+th.title+' strip'); th.style.cursor='zoom-in';
      const zoom=()=>{ window._mxvRow=th.title; renderMatrixView(); };
      th.addEventListener('click',zoom);
      th.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();zoom();} });
    });
  }
  // the one write a cell may carry, in the safe direction only: a granted cell
  // revokes (confirm -> authority_revoke -> re-render). Granting is never a
  // click — widening authority stays a deliberate act on the patch.
  out.querySelectorAll('.mxcell.mxedit').forEach(el=>{
    const revoke=async()=>{
      const uc=el.dataset.uc, ag=el.dataset.agent;
      if(!confirm('Revoke '+ag+"'s authority over "+uc+'? Tighten-only — granting stays on the patch.')) return;
      try{ const r=await tool('workspace_workflow',{op:'authority_revoke',params:{folder_context:S.path,use_case_id:uc,agent_id:ag,actor:'app-user'}});
        announce((r&&r.ok===false)?('Could not revoke: '+esc(r.error||'failed')):('Revoked — '+ag+' no longer runs '+uc+' (recorded)')); }
      catch(e){ announce('Could not revoke: '+((e&&e.message)||'failed')); }
      renderMatrixView(); reload();
    };
    el.addEventListener('click',revoke);
    el.addEventListener('keydown',ev=>{ if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();revoke();} });
  });
  if(window._view!=='matrix') host.classList.remove('show');   // toggled away while loading
}
function mxvZoomOut(rerenderOnly){ window._mxvRow=null; renderMatrixView(); return !!rerenderOnly; }

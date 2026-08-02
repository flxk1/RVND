/* Register — a read-only table of agents + tasks (per folder), categorical
   status (verdict, reserved with attributed basis), never a score. Shell
   chrome, not plane-provided content: loaded unconditionally by
   compose_classic(), no panel-mount registration. */
async function openRegisterPanel(){
  let rp=document.getElementById('regpanel'); if(rp){ rp.remove(); return; }
  const opener=document.activeElement;
  rp=document.createElement('div'); rp.id='regpanel'; rp.setAttribute('role','dialog'); rp.setAttribute('aria-label','Agent and task register');
  rp.style.cssText='position:absolute;top:12px;right:12px;width:460px;max-width:92%;max-height:84%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  rp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Register — agents & tasks</b><span style="flex:1"></span><span id="rgx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div><div id="rgout" role="status" aria-live="polite" style="margin-top:8px"><div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div></div>';
  stage.appendChild(rp);
  const restore=modalize(rp,opener);
  const close=()=>{rp.remove();restore();}; const x=rp.querySelector('#rgx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  rp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  loadRegister();
}
async function loadRegister(){
  const out=document.getElementById('rgout'); if(!out) return;
  if(!S.path){ out.innerHTML='<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its register</div>'; return; }
  let r; try{ r=await tool('workspace_workflow',{op:'governance_register',params:{folder_context:S.path}}); }
  catch(e){ out.innerHTML='<div class="ro">err: '+esc(e.message)+'</div>'; return; }
  const agents=(r.rows||[]).filter(x=>x.type==='agent'), ucs=(r.rows||[]).filter(x=>x.type==='use_case');
  let h='<table style="width:100%;border-collapse:collapse;font-size:11px"><thead><tr style="color:var(--txt-dim);text-align:left"><th style="padding:4px 6px">task</th><th>risk</th><th>verdict</th><th>reserved</th></tr></thead><tbody>';
  h+=ucs.map(u=>{const vi=VERDICT[u.verdict]||{}; return '<tr style="border-top:1px solid var(--line)"><td style="padding:4px 6px">'+esc(u.label)+'</td><td>'+esc(u.risk||'')+'</td><td style="color:'+(vi.col||'#888')+'">'+esc(vi.label||u.verdict)+'</td><td>'+regBasisTag(u)+'</td></tr>';}).join('');
  h+='</tbody></table><div class="ro" style="font-size:11px;margin-top:8px;color:var(--txt-dim)">'+agents.length+' agent'+(agents.length===1?'':'s')+' · '+ucs.length+' task'+(ucs.length===1?'':'s')+'</div>';
  h+='<button class="del" id="rgall" style="margin-top:8px;width:100%">All folders…</button>';
  out.innerHTML=h;
  const ab=out.querySelector('#rgall'); if(ab) ab.addEventListener('click',loadRegisterAll);
}
async function loadRegisterAll(){
  const out=document.getElementById('rgout'); if(!out) return;
  let r; try{ r=await tool('workspace_workflow',{op:'governance_register',params:{folder_context:S.path||'',scope:'all'}}); }catch(e){ return; }
  let h='<label>Registered folders ('+(r.count||0)+')</label><div class="ro" style="font-size:10px;color:var(--txt-dim);margin-bottom:4px">workspaces you’ve registered — not a scan of your disk'+((r.skipped)?' · '+r.skipped+' skipped':'')+'</div>';
  h+=(r.folders||[]).map(f=>'<div style="border-top:1px solid var(--line);padding:5px 0;font-size:11px"><b>'+esc(bn(f.folder))+'</b> · '+(f.summary.agents||0)+' agents · '+(f.summary.use_cases||0)+' tasks · '+(f.summary.reserved_use_cases||0)+' reserved</div>').join('')||'<div class="ro" style="color:var(--txt-dim)">no registered folders</div>';
  out.innerHTML=h;
}

/* About — read-only server info (product, version, substrate, tool count,
   signing key, host id). Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. */
async function openAboutPanel(){
  let ab=document.getElementById('aboutpanel'); if(ab){ ab.remove(); return; }
  const opener=document.activeElement;
  ab=document.createElement('div'); ab.id='aboutpanel'; ab.setAttribute('role','dialog'); ab.setAttribute('aria-label','About this governance server');
  ab.style.cssText='position:absolute;top:64px;left:50%;transform:translateX(-50%);width:380px;max-width:92%;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:11;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  ab.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">About</b><span class="robadge" title="this view only reads the signed record — it changes nothing">read-only</span><span style="flex:1"></span><span id="abx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div id="about"><div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div></div>';
  stage.appendChild(ab);
  const restore=modalize(ab,opener);
  const close=()=>{ab.remove();restore();}; const x=ab.querySelector('#abx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  ab.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  loadAbout();
}
async function loadAbout(){
  const out=document.getElementById('about'); if(!out) return;
  let r; try{ r=await tool('server_info',{}); }catch(e){ r={error:(e&&e.message)||'failed'}; }
  if(r.error){ out.innerHTML='<div class="finding warn"><span class="ttl">Could not read server info</span>'+esc(r.error)+'</div>'; return; }
  const row=(k,v)=>'<div style="display:flex;justify-content:space-between;gap:10px;font-size:11px;margin:3px 0"><span style="color:var(--txt-dim)">'+esc(k)+'</span><span style="font-family:IBM Plex Mono,monospace">'+esc(v)+'</span></div>';
  let h='<div style="font-size:12px;margin:4px 0 8px"><b>'+esc(r.product||'Rvnd')+'</b> · '+esc(r.tagline||'')+'</div>';
  h+=row('server',(r.server_name||'')+' '+(r.server_version||''));
  h+=row('substrate',r.os||'');
  h+=row('tools',(r.tool_count!=null?r.tool_count:(r.tools||[]).length)+' MCP tools');
  if(r.public_key_fingerprint) h+=row('signing key',r.public_key_fingerprint);
  if(r.host_id) h+=row('host',r.host_id);
  h+='<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:8px">Local-first. This server <b>declares</b> governance and signs the record; it does not certify compliance.</div>';
  out.innerHTML=h;
}

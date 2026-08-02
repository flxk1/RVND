/* The DESK stage view — same drawLoom renderer, mounted in-stage like MATRIX.
   The Record menu "Desk view" entry is a shortcut to setView('desk'). Shell
   chrome, not plane-provided content: loaded unconditionally by
   compose_classic(), no panel-mount registration. */
async function renderDeskView(){
  const host=document.getElementById('deskview'); if(!host) return;
  host.innerHTML='<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Desk — the governance mixer</b><span class="ro" style="font-size:10px;color:var(--txt-dim)">same patch, re-skinned · verdicts are the server’s call</span></div><div id="deskhost" role="status" aria-live="polite"></div>';
  const dh=host.querySelector('#deskhost');
  if(S.path) drawLoom(S.path, dh); else dh.innerHTML='<div class="ro" style="color:var(--txt-dim)">open a folder to load the desk</div>';
}

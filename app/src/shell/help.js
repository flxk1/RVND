/* Help — the operation reference. Every op-based facade answers op:'help' with
   its operations, params and (where authored) a note or hook — the legal or
   standard basis. This drawer renders that registry live, grouped by the four
   goal sections; standalone tools (no op parameter) carry a fixed one-liner.
   Read-only: it calls only help ops and changes nothing. Shell chrome, not
   plane-provided content: loaded unconditionally by compose_classic(), no
   panel-mount registration. */
const HELP_SECTIONS=[
 ['Set up',[
  ['workspace_workspace','Workspaces','create, list and route between governance workspaces'],
  ['workspace_folder','Folder','the folder behind the workspace — list, create, scan, files'],
  ['workspace_ingest','Bring-in','bring documents, URLs and works inside the boundary'],
  ['workspace_policy','Policy','the governance twin — dials, parties, oversight, access'],
  ['workspace_session','Session','the whole environment as one signed .rvnd file'],
  ['workspace_ask','Governance chat',null,'one governed chat turn over a workspace — retrieve its works, generate local-first, record the turn'],
 ]],
 ['Rules',[
  ['workspace_matrix','Autonomy matrix','go / ask / block per agent × task — tighten-only'],
  ['workspace_lock','Privacy lock','the minimisation gate + the encryption seal'],
  ['workspace_erase','Erasure','remove a person from this machine\'s record'],
  ['workspace_lens','Spend & limits','spend against the cap, declared precedents'],
  ['workspace_contract','Contracts','obligations & rule terms — reviews, state, resolve'],
 ]],
 ['Pending',[
  ['workspace_workflow','Workflows & runs','define, run, approve, resume — the run lifecycle'],
  ['workspace_dispatch','Dispatch','gated dispatch of skills — GO / CONDITIONAL / NO-GO'],
  ['workspace_orchestrate','Orchestrate',null,'route a query across companion workspaces — returns the gated dispatch plan; running is a separate step'],
 ]],
 ['Record',[
  ['workspace_audit','Audit','integrity checks on the signed record'],
  ['workspace_conformity','Conformity','evidence projections — attributed, never certifies'],
  ['workspace_grounder','Sources & gaps','what is grounded, where the gaps are — sources, claims, credit'],
  ['workspace_memory','Memory','recorded pairs and spans — the local memory'],
  ['workspace_capture','Capture','the LLM/web capture ledger'],
  ['workspace_model','Models','backends, cascade, capability routing'],
  ['workspace_mirror','Mirrors','reviewed copies of outside files'],
  ['workspace_legal','Standing facts','subject cards — does not certify compliance'],
  ['cross_workspace_read','Cross-workspace read',null,'a governed read across workspace boundaries — gated and recorded'],
  ['server_info','About this server',null,'name, version and the declared tool list'],
 ]],
];
async function openHelpPanel(){
  let hp=document.getElementById('helppanel'); if(hp){ hp.remove(); return; }
  const opener=document.activeElement;
  hp=document.createElement('div'); hp.id='helppanel'; hp.setAttribute('role','dialog'); hp.setAttribute('aria-label','Help — the operation reference');
  hp.style.cssText='position:absolute;top:12px;left:50%;transform:translateX(-50%);width:680px;max-width:94%;max-height:86%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:11;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  hp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Help — the operation reference</b><span class="robadge" title="this view only reads each tool&#39;s self-description — it changes nothing">read-only</span><span style="flex:1"></span><span id="hlx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">Everything this console can ask of the server, read live from each tool&#39;s own catalogue. Where an operation states its basis, it shows beneath it.</div>'
    +'<input id="hlpq" type="search" placeholder="filter operations…" aria-label="Filter operations" style="width:100%;box-sizing:border-box;background:#10161d;border:1px solid var(--line);border-radius:6px;color:var(--txt);font-size:11px;padding:5px 8px;margin:2px 0 8px" oninput="filterHelp()">'
    +'<div id="hlpout"><div class="ro" style="color:var(--txt-dim);font-size:11px">reading the catalogues…</div></div>';
  stage.appendChild(hp);
  const restore=modalize(hp,opener);
  const close=()=>{hp.remove();restore();}; const x=hp.querySelector('#hlx');
  x.addEventListener('click',close); x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();close();}});
  hp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();close();}});
  loadHelp();
}
async function loadHelp(){
  const out=document.getElementById('hlpout'); if(!out) return;
  const mono='font-family:IBM Plex Mono,monospace';
  const row=(k,head,params,basis)=>'<div class="hlprow" data-k="'+esc(k.toLowerCase())+'" style="padding:3px 0 3px 10px;border-left:2px solid #23303e;margin:2px 0">'
    +'<span style="'+mono+';font-size:11px">'+esc(head)+'</span>'
    +(params?'<span style="font-size:10px;color:var(--txt-dim);margin-left:8px">'+esc(params)+'</span>':'')
    +(basis?'<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:1px">'+esc(basis)+'</div>':'')+'</div>';
  const facadeHtml=async(f)=>{
    const [t,label,blurb,fixed]=f;
    let rows='',n=0;
    if(fixed!=null){ rows=row(t+' '+label+' '+fixed,t,'',fixed); n=1; }
    else{
      let r; try{ r=await tool(t,{op:'help'}); }catch(e){ r={error:(e&&e.message)||'failed'}; }
      if(r.error) rows=row(t+' error',t,'','could not read this catalogue: '+r.error);
      else for(const o of (r.ops||[])){
        n++;
        const req=(o.required||[]).join(', '), opt=(o.optional||[]).join(', ');
        const params=(req?'requires '+req:'')+(req&&opt?' · ':'')+(opt?'optional '+opt:'');
        const basis=o.note||o.hook||o.doc||'';
        rows+=row(t+' '+o.op+' '+params+' '+basis,o.op,params,basis);
      }
    }
    return '<div class="hlpgrp"><div style="display:flex;align-items:baseline;gap:8px;margin:7px 0 2px"><span style="'+mono+';font-size:11px;color:#8fb9d6">'+esc(t)+'</span><span style="font-size:11px">'+esc(label)+'</span><span style="font-size:10px;color:var(--txt-dim)">'+(blurb?esc(blurb)+' · ':'')+n+' operation'+(n===1?'':'s')+'</span></div>'+rows+'</div>';
  };
  let h='';
  for(const [sect,facades] of HELP_SECTIONS){
    const groups=await Promise.all(facades.map(facadeHtml));
    h+='<div class="hlpsect"><div style="font-family:Space Grotesk,sans-serif;font-size:12px;margin-top:10px;border-bottom:1px solid var(--line);padding-bottom:2px">'+esc(sect)+'</div>'+groups.join('')+'</div>';
  }
  out.innerHTML=h;
}
function filterHelp(){
  const q=(document.getElementById('hlpq')||{}).value; const out=document.getElementById('hlpout'); if(!out) return;
  const needle=(q||'').toLowerCase();
  for(const grp of out.querySelectorAll('.hlpgrp')){
    let any=false;
    for(const r of grp.querySelectorAll('.hlprow')){
      const hit=!needle||r.dataset.k.indexOf(needle)>=0;
      r.style.display=hit?'':'none'; if(hit) any=true;
    }
    grp.style.display=any?'':'none';
  }
  for(const s of out.querySelectorAll('.hlpsect')) s.style.display=[...s.querySelectorAll('.hlpgrp')].some(g=>g.style.display!=='none')?'':'none';
}

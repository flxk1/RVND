/* Governance chat — the single-input panel routed to ingest / intake / ask
   (governance_chat op). Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. Calls
   the shared draft-persistence helpers (draftPanelClosed, draftChipHtml,
   draftChipMount, draftDiscard, draftChatPush, chatLogRender) and
   renderMapContract as globals defined elsewhere in the shell — both stay in
   app/src/index.html, shared with the policy paste, map and cards surfaces
   (and, for renderMapContract, with the map panel bundle). */
async function openChatPanel(){
  let cp=document.getElementById('chatpanel');
  if(cp){ draftPanelClosed('chat'); cp.remove(); return; }
  const opener=document.activeElement;
  cp=document.createElement('div'); cp.id='chatpanel'; cp.setAttribute('role','dialog'); cp.setAttribute('aria-label','Governance chat');
  cp.style.cssText='position:absolute;top:12px;left:12px;width:500px;max-width:92%;max-height:86%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;z-index:10;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  cp.innerHTML='<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:13px">Governance chat</b>'+draftChipHtml('chat')+'<span style="flex:1"></span><span id="cpx" role="button" tabindex="0" aria-label="Close" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>'
    +'<div class="ro" style="font-size:11px;color:var(--txt-dim);margin:6px 0">Single input. Paste a policy, describe your use case, or ask a question — Rvnd routes it and shows what it inferred. It does not certify compliance and applies nothing until you confirm.</div>'
    +'<div id="chatlog" style="margin:8px 0"></div>'
    +'<div style="display:flex;gap:8px;align-items:center;margin-top:6px"><input id="chatin" type="text" aria-label="Message" placeholder="paste policy · describe your system · ask a question" style="flex:1;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;font-size:12px;padding:7px 9px"><button class="del" id="chatsend" style="border-color:#3a3357;color:#b9acff">Send</button><button class="del" id="chatdiscard" title="delete the saved chat draft" style="border-color:#5a2f2a;color:#e6b0aa">Discard draft</button></div>';
  stage.appendChild(cp);
  const restore=modalize(cp,opener);
  const closeP=()=>{draftPanelClosed('chat');cp.remove();restore();}; cp._close=closeP;
  const x=cp.querySelector('#cpx'); x.addEventListener('click',closeP);
  x.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();closeP();}});
  cp.addEventListener('keydown',ev=>{if(ev.key==='Escape'){ev.preventDefault();closeP();}});
  cp.querySelector('#chatsend').addEventListener('click',()=>sendChat());
  cp.querySelector('#chatin').addEventListener('keydown',ev=>{if(ev.key==='Enter'){ev.preventDefault();sendChat();}});
  // restore the drafted transcript under a divider; new messages append below
  chatLogRender();
  const clog=cp.querySelector('#chatlog');
  cp.querySelector('#chatdiscard').addEventListener('click',()=>{ clog.innerHTML=''; draftDiscard('chat'); });
  draftChipMount('chat');
}
async function sendChat(){
  const inp=document.getElementById('chatin'); const text=(inp?inp.value:'').trim(); if(!text) return;
  const log=document.getElementById('chatlog'); if(inp) inp.value='';
  if(log) log.insertAdjacentHTML('beforeend','<div style="margin:6px 0"><span style="font-size:11px;color:var(--txt-dim)">you</span><div style="font-size:12px;color:#fff">'+esc(text)+'</div></div>');
  draftChatPush('you',text);
  window._chatPolicy=window._chatPolicy||'';
  let r;
  try{ r=await tool('workspace_workflow',{op:'governance_chat',params:{folder_context:S.path||'',text:text,policy_text:window._chatPolicy}}); }
  catch(e){ if(log) log.insertAdjacentHTML('beforeend','<div class="ro">err: '+esc((e&&e.message)||'failed')+'</div>'); return; }
  if(r.intent==='policy'){ window._chatPolicy=(window._chatPolicy?window._chatPolicy+'\n':'')+text; }
  if(log){ log.insertAdjacentHTML('beforeend','<div style="margin:6px 0"><span style="font-size:11px;color:#8fd1ad">rvnd · '+esc(r.echo||r.intent||'')+'</span>'+renderChatResult(r)+'</div>'); log.scrollTop=log.scrollHeight;
    const lc=log.lastElementChild; draftChatPush('rvnd',(lc&&lc.textContent)||''); }
}
function renderChatResult(r){
  if(!r) return '';
  if(r.kind==='twin'){ const t=r.result||{}; if(!t.ok) return '<div class="ro" style="font-size:11px">could not build twin: '+esc(((t.errors)||['unknown']).join('; '))+'</div>';
    const c=t.classification||{}; return '<div class="ro" style="font-size:11px;margin-top:4px">twin drafted — '+((c.express||[]).length)+' express rules, '+((t.host_handoffs||[]).length)+' host hand-offs. Not applied — open “Ingest a policy” to review + apply.</div>'; }
  if(r.kind==='map') return renderMapContract(r.result);
  if(r.kind==='card'){ const c=r.result||{}; return '<div class="ro" style="font-size:11px;margin-top:4px">use case captured: “'+esc(c.description||'')+'” · '+Math.round((c.completeness||0)*100)+'% complete · to narrow: '+esc((c.unknown_facets||[]).join(', ')||'—')+'</div>'; }
  return '<div class="ro" style="font-size:11px">'+esc(JSON.stringify(r.result||{}).slice(0,200))+'</div>';
}

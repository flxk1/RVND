/* Onboarding wizard — first-run walkthrough (welcome, create a workspace,
   register an agent + task, set an autonomy posture, tour the four
   sections). Shell chrome, not plane-provided content: loaded
   unconditionally by compose_classic(), no panel-mount registration. */
function dismissOnboarding(){ const w=document.getElementById('onboardwiz'); if(!w) return; const r=w._restore; w.remove(); if(r) r(); }
function openOnboardingWizard(){
  if(document.getElementById('onboardwiz')) return;
  const opener=document.activeElement;
  const w=document.createElement('div'); w.id='onboardwiz'; w.setAttribute('role','dialog'); w.setAttribute('aria-label','Welcome — set up your first workspace');
  w.style.cssText='position:absolute;top:44px;left:50%;transform:translateX(-50%);width:540px;max-width:94%;max-height:84%;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;z-index:12;box-shadow:0 10px 40px rgba(0,0,0,.55)';
  stage.appendChild(w);
  w._restore=modalize(w,opener);
  w._step=0; renderWizStep();
  w.addEventListener('keydown',ev=>{ if(ev.key==='Escape'){ ev.preventDefault(); _wizMarkSeen(); dismissOnboarding(); } });
}
function renderWizStep(){
  const w=document.getElementById('onboardwiz'); if(!w) return;
  const step=w._step, TOTAL=5;
  const dots=Array.from({length:TOTAL},(_,i)=>'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:'+(i===step?'#8fb9d6':'#2f4358')+'"></span>').join('');
  const head=(t)=>'<div style="display:flex;align-items:center;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:14px">'+esc(t)+'</b><span style="flex:1"></span><span id="wzskip" role="button" tabindex="0" style="cursor:pointer;color:var(--txt-dim);font-size:11px">skip the tour</span></div><div style="margin:8px 0 10px">'+dots+'</div>';
  const nav=(label)=>'<div style="display:flex;gap:8px;margin-top:12px"><span style="flex:1"></span><button class="psbtn" id="wznext">'+esc(label)+'</button></div>';
  const ro=(t)=>'<div class="ro" style="font-size:11.5px;line-height:1.65;color:var(--txt-dim)">'+t+'</div>';
  let h='';
  if(step===0){
    h=head('Welcome')+ro('This console gives AI agents <b>exactly the authority you intend</b> — and signs every grant, run and refusal into a tamper-evident record. The patch behind this dialog is a <b>demo</b>; your own workspace starts empty. Five short steps set one up.')+nav('Start');
  } else if(step===1){
    h=head('Create your first workspace')
      +ro('A workspace is a governance space — a folder on this machine. Everything the console shows applies to the workspace you have open.')
      +'<input type="text" id="wzpath" placeholder="/path/to/my-workspace" style="width:100%;box-sizing:border-box;margin:10px 0 5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
      +'<input type="text" id="wzlabel" placeholder="label (optional)" style="width:100%;box-sizing:border-box;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
      +'<div id="wzmsg" class="ro" style="font-size:10.5px;color:#e3a877"></div>'+nav('Create & continue');
  } else if(step===2){
    h=head('Put an agent and a task on the canvas')
      +ro('An <b>agent</b> is who acts; a <b>task</b> is a governed action it may be granted. Both are registered into the workspace record. You can wire authority (drag agent → task) on the canvas afterwards.')
      +'<input type="text" id="wzagent" value="assistant" aria-label="agent name" style="width:100%;box-sizing:border-box;margin:10px 0 5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
      +'<div style="display:flex;gap:6px"><input type="text" id="wztask" value="Draft a summary" aria-label="task name" style="flex:1;box-sizing:border-box;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"><select id="wzrisk" aria-label="task risk" style="background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;font-size:11px"><option value="low" selected>low risk</option><option value="high">high risk</option></select></div>'
      +'<div id="wzmsg" class="ro" style="font-size:10.5px;color:#e3a877;margin-top:4px"></div>'+nav('Register both & continue');
  } else if(step===3){
    h=head('Choose an autonomy posture')
      +ro('The matrix decides per cell: <b>go</b> (act), <b>ask</b> (a person confirms first) or <b>block</b>. At runtime it can only tighten. <b>Start cautious</b> turns every <b>go</b> into <b>ask</b> for this workspace — you can loosen deliberately later in Rules → Autonomy.')
      +'<div id="wzmsg" class="ro" style="font-size:10.5px;color:#e3a877;margin-top:4px"></div>'
      +'<div style="display:flex;gap:8px;margin-top:12px"><button class="psbtn" id="wzcautious">Start cautious — every go asks first</button><span style="flex:1"></span><button class="psbtn" id="wznext">Keep the defaults</button></div>';
  } else {
    const tips=[...document.querySelectorAll('.sectbtn')].map(b=>'<div style="border-top:1px solid var(--line);padding:5px 0"><b style="font-size:11.5px">'+esc(b.textContent.replace(/▾$/,'').trim())+'</b><span style="font-size:11px;color:var(--txt-dim)"> — '+esc(b.getAttribute('title')||'')+'</span></div>').join('');
    h=head('The four sections')+ro('Everything lives under four questions in the toolbar:')+'<div style="margin-top:8px">'+tips+'</div>'
      +ro('<span style="display:block;margin-top:8px">The <b>?</b> button is the full operation reference; <b>ⓘ</b> is this server. Recording is always on.</span>')+nav('Finish');
  }
  w.innerHTML=h;
  const skip=w.querySelector('#wzskip'); if(skip){ const done=()=>{ _wizMarkSeen(); dismissOnboarding(); }; skip.addEventListener('click',done); skip.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();done();}}); }
  const msg=(t)=>{ const m=w.querySelector('#wzmsg'); if(m) m.textContent=t; };
  const next=w.querySelector('#wznext');
  if(next) next.addEventListener('click',async()=>{
    if(step===1){
      const path=((w.querySelector('#wzpath')||{}).value||'').trim(), label=((w.querySelector('#wzlabel')||{}).value||'').trim();
      if(!path){ msg('give the workspace a path'); return; }
      w._active=true; next.disabled=true;
      try{
        const cr=await tool('workspace_folder',{op:'create',params:{path:path}}); if(cr&&(cr.error||cr.ok===false)) throw new Error(cr.error||'could not create folder');
        const resolved=(cr&&cr.path)||path;
        const ad=await tool('workspace_workspace',{op:'add',params:{folder_context:resolved,label:label}}); if(ad&&(ad.error||ad.ok===false)) throw new Error(ad.error||'could not register');
        const fp=(ad&&ad.path)||resolved;
        const after=await tool('workspace_workspace',{op:'list'}); const list=(after&&after.workspaces)||[]; const idx=list.findIndex(x=>x.path===fp);
        if(idx>=0){ window._fi=idx; const sel=document.getElementById('folder'); if(sel) sel.selectedIndex=idx; }
        await boot();
        announce('Workspace created and switched: '+bn(fp)+'.');
        w._step=2; renderWizStep();
      }catch(e){ next.disabled=false; msg('could not create it: '+((e&&e.message)||'failed')); }
      return;
    }
    if(step===2){
      const an=((w.querySelector('#wzagent')||{}).value||'').trim(), tn=((w.querySelector('#wztask')||{}).value||'').trim(), risk=((w.querySelector('#wzrisk')||{}).value)||'low';
      if(!an||!tn){ msg('name the agent and the task'); return; }
      if(!S.path){ msg('no workspace open — go back one step'); return; }
      next.disabled=true;
      try{
        const aid='agent-'+(Date.now()%100000), uid='uc-'+(Date.now()%100000);
        await tool('workspace_policy',{op:'party_register',params:{folder_context:S.path,party_id:aid,kind:'agent',name:an,actor:'app-user'}});
        await tool('workspace_workflow',{op:'use_case_register',params:{folder_context:S.path,use_case_id:uid,name:tn,fingerprint:{},risk:risk,allowed_agents:[],actor:'app-user'}});
        await reload();
        w._step=3; renderWizStep();
      }catch(e){ next.disabled=false; msg('could not register: '+((e&&e.message)||'failed')); }
      return;
    }
    if(step===3){ w._step=4; renderWizStep(); return; }
    if(step===4){ _wizMarkSeen(); dismissOnboarding(); return; }
    w._step=step+1; renderWizStep();
  });
  const caut=w.querySelector('#wzcautious');
  if(caut) caut.addEventListener('click',async()=>{
    if(!S.path){ msg('no workspace open — go back'); return; }
    caut.disabled=true;
    try{
      const cur=await tool('workspace_matrix',{op:'show',params:{folder_context:S.path}});
      const grid=(cur&&(cur.matrix||cur.grid))||{}; const tightened={};
      for(const g of Object.keys(grid)){ tightened[g]={}; for(const o of Object.keys(grid[g])) tightened[g][o]=(grid[g][o]==='go'?'ask':grid[g][o]); }
      const r=await tool('workspace_matrix',{op:'set_all',params:{folder_context:S.path,matrix:tightened,actor:'app-user'}});
      if(r&&(r.error||r.ok===false)) throw new Error(r.error||'the matrix write was refused');
      announce('Autonomy posture set: every go now asks first.');
      w._step=4; renderWizStep();
    }catch(e){ caut.disabled=false; msg('could not set the posture: '+((e&&e.message)||'failed')); }
  });
}

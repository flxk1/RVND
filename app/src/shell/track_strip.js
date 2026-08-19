/* The track channel strip — per-track selection detail in the Inspector
   (docs/concepts/per-track-binding-concept.md, "The track channel strip"). A read-only
   render of the server's track_strip projection: status LED, the per-grade
   oversight ladder (a law-basis reservation locks it — tighten only, no
   loosening affordance rendered), the governance inserts (competences, channel
   floors, reservations, the routed m-of-n sign-off meter), the per-track
   verdict meter, and — on egress connectors only — the cable (reference + arm
   status, never the secret). Every state is word + glyph + colour, never
   colour alone. The server decides; this renders. Shell chrome, not
   plane-provided content: loaded unconditionally by compose_classic(), no
   panel-mount registration. */
function _tsMeter(m){ const t=(m&&m.verdicts)||{}; const ks=Object.keys(t).sort();
  return ks.length?ks.map(k=>esc(k)+' '+t[k]).join(' · '):'no verdicts recorded'; }
async function fillTrackStrip(addr){
  const box=document.getElementById('trackstrip'); if(!box) return;
  if(!S.path){ box.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim)">open a folder to see this track’s governance</div>'; return; }
  let r; try{ r=await tool('workspace_workflow',{op:'track_strip',params:Object.assign({folder_context:S.path},addr)}); }
  catch(e){ box.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim)">track unavailable — '+esc((e&&e.message)||'failed')+'</div>'; return; }
  if(!r||!r.ok||!r.strip){ box.innerHTML='<div class="ro" style="font-size:10px;color:var(--txt-dim)">'+esc((r&&r.reason)||'no track data')+'</div>'; return; }
  const s=r.strip; let h='<label>Track</label>';
  if(r.kind==='party'){
    const st=s.status||'active';
    h+='<div class="ro" aria-label="status: '+escA(st)+'"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+(_TS_STATUS[st]||'#5a616f')+';margin-right:5px"></span>'+esc(st)+(s.role?' · '+esc(s.role):'')+'</div>';
    const g=_tsGrade(s.ladder&&s.ladder.grade);
    if(g!=null){ h+='<div style="display:flex;gap:3px;margin:5px 0" role="img" aria-label="oversight ladder: L'+g+(s.ladder.locked?', locked by law — tighten only':'')+'">';
      for(let i=0;i<GRADE_NAME.length;i++) h+='<div class="fcell'+(i===g?' earned':'')+'"'+(i===g?' style="background:'+GHEX[g]+'"':'')+' title="L'+i+' · '+GRADE_NAME[i]+'">L'+i+'</div>';
      h+='</div>';
      if(s.ladder.locked){ const lk=(s.ladder.locks||[])[0]||{};
        h+='<div class="ro" style="font-size:10px;color:#e2554a" title="'+escA(lk.source||'')+'">🔒 locked by law — tighten only'+(lk.act_type?' ('+esc(lk.act_type)+')':'')+'</div>'; } }
    if((s.competences||[]).length) h+='<div class="ro" style="font-size:10px">competences: '+s.competences.map(esc).join(' · ')+'</div>';
    for(const c of (s.channels||[])){
      if(!c.registered){ h+='<div class="ro" style="font-size:10px;color:#e2554a">channel '+esc(c.connector_id)+' — not registered (dangling)</div>'; continue; }
      const fl=_EG_FLOOR[c.floor]||_EG_FLOOR.permit;
      h+='<div class="ro" style="font-size:10px" aria-label="channel '+escA(c.connector_id)+' floor '+escA(fl.word)+'"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+fl.col+';margin-right:4px"></span>'+esc(c.connector_id)+' · '+esc(c.role||'')+' · floor '+esc(fl.word)+'</div>';
    }
    const ap=(s.approvals&&s.approvals.pending)||[];
    if(ap.length){ h+='<div class="ro" style="font-size:10px"><b>'+ap.length+'</b> sign-off'+(ap.length===1?'':'s')+' await this hand</div>';
      for(const q of ap) h+='<div class="ro" style="font-size:10px;color:var(--txt-dim)">'+esc(q.form||'sign-off')+' · signed '+q.signed+' of '+(q.required==null?'?':q.required)+'</div>';
    } else h+='<div class="ro" style="font-size:10px;color:var(--txt-dim)">no sign-offs waiting</div>';
    h+='<div class="ro" style="font-size:10px;color:var(--txt-dim)">meter: '+_tsMeter(s.meter)+' · '+((s.use_cases||[]).length)+' task'+((s.use_cases||[]).length===1?'':'s')+'</div>';
  } else {
    const fl=_EG_FLOOR[s.floor]||_EG_FLOOR.permit;
    h+='<div class="ro" aria-label="floor: '+escA(fl.word)+'"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+fl.col+';margin-right:5px"></span>floor '+esc(fl.word)+(s.group?' · group '+esc(s.group):'')+'</div>';
    if(s.egress){ const cred=(s.egress.credential)||{}; const arm=_EG_ARM[cred.status]||_EG_ARM.no_cable;
      h+='<div class="ro" style="font-size:10px;color:'+arm.col+'" aria-label="cable: '+escA(arm.word)+'">'+arm.glyph+' '+esc(arm.word)+(cred.credential_ref?' <span style="color:var(--txt-dim)">· '+esc(cred.credential_ref)+'</span>':'')+'</div>'; }
    h+='<div class="ro" style="font-size:10px;color:var(--txt-dim)">'+((s.parties||[]).length)+' driver'+((s.parties||[]).length===1?'':'s')+' · '+((s.use_cases||[]).length)+' task'+((s.use_cases||[]).length===1?'':'s')+((s.reservations||[]).length?' · '+s.reservations.length+' reservation'+(s.reservations.length===1?'':'s'):'')+'</div>';
    h+='<div class="ro" style="font-size:10px;color:var(--txt-dim)">meter: '+_tsMeter(s.meter)+'</div>';
  }
  box.innerHTML=h;
}

/* P9 — DAW control-surface skin. A re-skin of the SAME governance_graph
   projection as a mixing desk: master bus, per-task governed faders (the law
   clamp), agent lanes, oversight-real readout. Presentational; verdicts stay the
   server's call; never a 0–1 dial or % meter (the faders are discrete L-cells).
   Shell chrome, not plane-provided content: loaded unconditionally by
   compose_classic(), no panel-mount registration. */
async function drawLoom(folder, host){
  host.innerHTML='<div class="ro" style="color:var(--txt-dim);font-size:11px">loading desk…</div>';
  let g; try{ g=await tool('workspace_workflow',{op:'governance_graph',params:{folder_context:folder}}); }
  catch(e){ host.innerHTML='<div class="ro">err: '+esc(e.message)+'</div>'; return; }
  const nodes=g.nodes||[], edges=g.edges||[];
  const egress=edges.filter(e=>e.kind==='egress');
  const vOf=e=>{ const uc=nodes.find(n=>n.id===e.from); return resolveEgressVerdict(e.verdict, !!(uc&&uc.reserved&&uc.reserved.length)); };
  // separate tallies — a server 'prohibited' (severed, no sign-off) must NOT be
  // folded into the 'reserved by law' count (E6); it gets its own lamp.
  const nAuto=egress.filter(e=>vOf(e)==='auto').length, nReserved=egress.filter(e=>vOf(e)==='reserved').length, nProhibited=egress.filter(e=>vOf(e)==='prohibited').length, nPpl=egress.filter(e=>vOf(e)==='human').length;
  let h='';
  // master bus (the refusing limiter) — tallies from server verdicts
  h+='<div id="loom-master" data-auto="'+nAuto+'" data-reserved="'+nReserved+'" data-prohibited="'+nProhibited+'" style="background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:10px"><div style="font-family:Space Grotesk,sans-serif;font-size:12px;font-weight:600;margin-bottom:6px">Master bus — the boundary</div>'
    +'<div class="lamp'+(nAuto?' on':'')+'"><span class="b" style="background:'+(nAuto?'#5aa886':'#2c313c')+'"></span>releasing · '+nAuto+'</div>'
    +'<div class="lamp'+(nPpl?' on':'')+'"><span class="b" style="background:'+(nPpl?'#df8b46':'#2c313c')+'"></span>needs a person · '+nPpl+'</div>'
    +'<div class="lamp'+(nReserved?' on':'')+'"><span class="b" style="background:'+(nReserved?'#e2554a':'#2c313c')+'"></span>reserved · '+nReserved+'</div>'
    +'<div class="lamp proh'+(nProhibited?' on':'')+'"><span class="b" style="background:'+(nProhibited?'#a8332b':'#2c313c')+'"></span>not allowed · '+nProhibited+'</div></div>';
  // embedded patch (the wiring), egress lines coloured by server verdict
  h+='<svg id="patchsvg" width="100%" height="'+Math.max(40,egress.length*18+12)+'" style="background:var(--bg);border:1px solid var(--line);border-radius:6px;margin-bottom:10px">';
  egress.forEach((e,i)=>{ const vi=VERDICT[vOf(e)]||VERDICT.permitted; const y=14+i*18; h+='<text x="8" y="'+(y+3)+'" font-family="IBM Plex Mono,monospace" font-size="10" fill="#cdd2dc">'+esc(e.from.replace(/^uc:/,''))+'</text><line x1="120" y1="'+y+'" x2="86%" y2="'+y+'" stroke="'+vi.col+'" stroke-width="'+(vi.dbl?4:2)+'"'+(vi.dash?' stroke-dasharray="'+vi.dash+'"':'')+'></line><text x="87%" y="'+(y+3)+'" font-size="9" fill="'+vi.col+'">'+esc(vi.label||'')+'</text>'; });
  h+='</svg>';
  // governed faders — one channel strip per task; the law clamp on reserved
  const ucs=nodes.filter(n=>n.kind==='use_case');
  // identical strip widths: the same cell sits at the same x on every strip, so
  // which-track-is-at-which-level is one horizontal scan
  h+='<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px">'+ucs.map(u=>{
    const reserved=!!(u.reserved&&u.reserved.length), lvl=Math.max(0,Math.min(4,u.grade||0)), cap=(typeof u.grade_ceiling==='number')?u.grade_ceiling:lvl;  // M8: server-composed ceiling, client renders it
    let f='<div style="border:1px solid var(--line);border-radius:8px;padding:8px;flex:0 0 128px;box-sizing:border-box"><div style="font-size:11px;font-weight:600;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="'+escA(u.label)+'">'+esc(u.label)+'</div>';
    f+='<div class="fader'+(reserved?' clamped':'')+'" data-fader="'+esc(u.id)+'" data-reserved="'+(reserved?'true':'false')+'" role="group" aria-disabled="true" aria-label="read-only · autonomy level L'+lvl+', ceiling L'+cap+(reserved?', clamped, '+reservedPhrase(u):'')+' — server-decided, read-only">';
    for(let i=0;i<5;i++) f+='<div class="fcell'+(i===lvl?' earned':'')+(i>cap?' over':'')+'"'+(i===lvl?' style="background:'+GHEX[lvl]+'"':'')+' title="L'+i+' · '+GRADE_NAME[i]+'">L'+i+'</div>';
    f+='</div><div class="ro" style="font-size:10px;margin-top:3px">risk '+esc(u.risk||'low')+(reserved?' · <b style="color:#e2877f">'+esc(reservedPhrase(u))+'</b>':'')+'</div></div>';
    return f;
  }).join('')+'</div>';
  // agent lanes (tracks)
  const agents=nodes.filter(n=>n.kind==='agent');
  h+='<div id="loom-lanes" style="margin-bottom:10px">'+(agents.map(a=>'<div class="card" style="background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:7px 10px;margin-bottom:6px;font-size:11px"><b>'+esc(a.label)+'</b> · '+esc(a.status||'active')+' · authority over '+edges.filter(e=>e.kind==='authority'&&e.from===a.id).length+'</div>').join('')||'<div class="ro" style="color:var(--txt-dim)">no agents</div>')+'</div>';
  // oversight-real readout (declare-the-gap, no score)
  h+='<div id="loom-adequacy" style="border-top:1px solid var(--line);padding-top:8px;font-size:11px;color:var(--txt-dim)"><b style="color:var(--txt)">Oversight-real</b> · Lens drift budget cap: not declared · calibration: not assessed</div>';
  host.innerHTML=h;
}

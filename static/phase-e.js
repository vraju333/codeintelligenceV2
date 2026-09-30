let phaseEAvailableProjects=[];

async function loadPhaseEProjects(){
  const h=document.getElementById("phaseEProjectSelector");
  if(!h)return;
  try{
    const r=await fetch("/api/cross-project/projects"),d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    phaseEAvailableProjects=d.projects||[];
    h.innerHTML=phaseEAvailableProjects.length
      ? phaseEAvailableProjects.map(p=>`<label class="phase-e-project-option"><input type="checkbox" class="phase-e-project-checkbox" value="${escapeHtml(p.name)}"><span><strong>${escapeHtml(p.name)}</strong><small>${p.java_files} Java files</small></span></label>`).join("")
      : '<div class="muted-box">No registered Java projects found.</div>';
  }catch(e){h.innerHTML=renderError(e.message)}
}

function selectedPhaseEProjects(){
  return [...document.querySelectorAll(".phase-e-project-checkbox:checked")].map(x=>x.value);
}

function phaseEProjectList(names){
  return `<div class="phase-e-projects"><strong>Projects analyzed:</strong>${(names||[]).map(x=>`<span class="phase-e-project-chip">${escapeHtml(x)}</span>`).join("")}</div>`;
}

function phaseECrossLinks(items){
  if(!items||!items.length)return '<div class="muted-text">No deterministic cross-project dependency links found.</div>';
  return `<details class="phase-e-links" open><summary>Cross-Project Dependencies (${items.length})</summary>${items.map(x=>`<div class="phase-e-link-row"><strong>${escapeHtml(x.source_project||"Unknown")}</strong><span>→ ${escapeHtml(x.relationship||"DEPENDS_ON_PROJECT")} →</span><strong>${escapeHtml(x.target_project||"Unknown")}</strong>${x.evidence?`<div class="muted-text">Evidence: ${escapeHtml(x.evidence)}</div>`:""}</div>`).join("")}</details>`;
}

async function syncPhaseEGraph(){
  const p=selectedPhaseEProjects(),b=document.getElementById("phaseEResult");
  if(!p.length){b.innerHTML=renderError("Select at least one Java project.");return}
  try{
    const r=await fetch("/api/cross-project/sync",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({projects:p})}),d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    b.innerHTML=`<div><strong>${d.projects}</strong> selected project${d.projects===1?"":"s"} · <strong>${d.nodes}</strong> nodes · <strong>${d.relationships}</strong> relationships · <strong>${d.cross_project_relationships}</strong> cross-project links</div>${phaseEProjectList(d.project_names)}${d.cross_project_status==="ADD_ANOTHER_PROJECT"?'<div class="muted-box">Knowledge Graph ready. Select another Java project to discover cross-project dependencies.</div>':phaseECrossLinks(d.cross_project_details)}<div class="muted-text phase-e-neo4j">Neo4j: ${escapeHtml(d.neo4j?.status||"UNKNOWN")}</div>`;
  }catch(e){b.innerHTML=renderError(e.message)}
}

function phaseENodeCard(n, extraClass=""){
  return `<div class="phase-e-node-card ${extraClass}">
    <div><strong>${escapeHtml(n.name)}</strong> <span class="tag">${escapeHtml(n.type)}</span></div>
    <div class="phase-e-node-project">Project: <strong>${escapeHtml(n.project||"Unknown")}</strong></div>
  </div>`;
}

function phaseECompactImpact(d){
  const matches=d.matches||[];
  const impacted=d.impacted||[];
  const selected=d.selected_projects||[];
  const matchIds=new Set(matches.map(n=>n.id));
  const supporting=impacted.filter(n=>!matchIds.has(n.id));

  const projectGroups={};
  impacted.forEach(n=>{
    const p=n.project||"Unknown";
    (projectGroups[p]||(projectGroups[p]=[])).push(n);
  });

  const crossRels=(d.relationships||[]).filter(r=>r.type==="DEPENDS_ON_PROJECT");
  const crossPairs=[];
  const seen=new Set();
  crossRels.forEach(r=>{
    const s=impacted.find(n=>n.id===r.source);
    const t=impacted.find(n=>n.id===r.target);
    if(!s||!t||s.project===t.project)return;
    const key=`${s.project}|${t.project}`;
    if(seen.has(key))return;
    seen.add(key);
    crossPairs.push({source:s.project,target:t.project,evidence:r.properties?.evidence});
  });

  let bridgeHtml="";
  if(crossPairs.length){
    bridgeHtml=`<div class="phase-e-bridge-list">${crossPairs.map(x=>`
      <div class="phase-e-bridge">
        <span class="phase-e-bridge-project">${escapeHtml(x.source)}</span>
        <span class="phase-e-bridge-arrow">→ CROSS-PROJECT →</span>
        <span class="phase-e-bridge-project">${escapeHtml(x.target)}</span>
        ${x.evidence?`<div class="phase-e-bridge-evidence">${escapeHtml(x.evidence)}</div>`:""}
      </div>`).join("")}</div>`;
  }else if(selected.length>1){
    bridgeHtml='<div class="muted-box">No deterministic cross-project dependency was found in this impact path.</div>';
  }

  const directHtml=matches.length
    ? `<div class="phase-e-compact-section"><div class="phase-e-compact-title">Direct matches (${matches.length})</div><div class="phase-e-direct-grid">${matches.map(n=>phaseENodeCard(n,"phase-e-direct-node")).join("")}</div></div>`
    : '<div class="muted-box">No direct graph evidence found.</div>';

  const projectHtml=Object.keys(projectGroups).length
    ? `<div class="phase-e-compact-section"><div class="phase-e-compact-title">Affected projects</div><div class="phase-e-project-impact-grid">${Object.entries(projectGroups).map(([p,nodes])=>{
        const useful=nodes.filter(n=>!["PROJECT"].includes(n.type)).slice(0,4);
        return `<div class="phase-e-project-impact-card"><strong>${escapeHtml(p)}</strong><div class="phase-e-project-impact-count">${nodes.length} connected node${nodes.length===1?"":"s"}</div>${useful.map(n=>`<div class="phase-e-mini-node">${escapeHtml(n.name)} <span>${escapeHtml(n.type)}</span></div>`).join("")}</div>`;
      }).join("")}</div></div>`
    : "";

  const supportHtml=supporting.length
    ? `<details class="phase-e-supporting"><summary>View supporting details (${supporting.length})</summary><div class="phase-e-supporting-grid">${supporting.map(n=>phaseENodeCard(n)).join("")}</div></details>`
    : "";

  return `<div class="phase-e-impact-summary">
      <div class="phase-e-impact-status">${escapeHtml(d.status)}</div>
      <div class="phase-e-impact-query">${escapeHtml(d.query||"")}</div>
      <div class="phase-e-impact-metrics"><strong>${matches.length}</strong> direct match${matches.length===1?"":"es"} · <strong>${selected.length}</strong> project${selected.length===1?"":"s"} analyzed · <strong>${crossPairs.length}</strong> cross-project dependenc${crossPairs.length===1?"y":"ies"}</div>
    </div>
    ${phaseEProjectList(selected)}
    ${directHtml}
    ${bridgeHtml}
    ${projectHtml}
    ${supportHtml}`;
}

async function runPhaseEImpact(){
  const q=document.getElementById("phaseEQuery").value.trim(),b=document.getElementById("phaseEResult");
  if(!q){b.innerHTML=renderError("Enter an attribute, method, class, endpoint or project.");return}
  b.innerHTML='<div class="muted-text">Analyzing selected project graph...</div>';
  try{
    const r=await fetch("/api/cross-project/impact?q="+encodeURIComponent(q)),d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    b.innerHTML=phaseECompactImpact(d);
  }catch(e){b.innerHTML=renderError(e.message)}
}

document.addEventListener("DOMContentLoaded",loadPhaseEProjects);

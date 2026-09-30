async function analyseAttributeImpact() {
    const input = document.getElementById("attributeImpactInput");
    const container = document.getElementById("attributeImpactResult");
    const attribute = (input?.value || "").trim();

    if (!attribute) {
        container.innerHTML = `<div class="warning">Enter an attribute name first.</div>`;
        return;
    }

    container.innerHTML = "Analysing attribute impact across the active Java project...";

    try {
        const response = await fetch(`/api/knowledge/attribute?attribute=${encodeURIComponent(attribute)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        container.innerHTML = renderUnifiedAttributeKnowledge(data);
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

function renderAttributeImpactCore(data) {
    const layers = data.layers || [];
    const endpoints = data.affected_endpoints || [];
    const scenarios = data.affected_scenarios || [];
    const confidence = data.confidence || {};
    const relatedJiras = data.related_jiras || [];
    const history = data.historical_traceability || [];

    const layerHtml = layers.length ? layers.map(layer => `
        <div class="attribute-layer-card">
            <div class="attribute-layer-role">${escapeHtml(layer.role || "JAVA_CLASS")}</div>
            ${(layer.classes || []).map(name => `<div class="attribute-class-name">${escapeHtml(name)}</div>`).join("")}
            ${(layer.methods || []).length ? `
                <div class="muted-text attribute-methods">${(layer.methods || []).map(escapeHtml).join(" · ")}</div>
            ` : ""}
        </div>
    `).join("") : `<div class="muted-box">No Java occurrence found for this attribute.</div>`;

    const endpointHtml = endpoints.length ? endpoints.map(endpoint => `
        <div class="attribute-impact-row">
            <div>
                <strong>${escapeHtml(endpoint.http_method)} ${escapeHtml(endpoint.endpoint)}</strong>
                <div class="muted-text">${escapeHtml(endpoint.controller || "")} . ${escapeHtml(endpoint.method_name || "")}</div>
            </div>
            <span class="tag">${escapeHtml(endpoint.relevance || "FLOW")}</span>
            ${(endpoint.matched_methods || []).length ? `<div class="attribute-evidence">Direct methods: ${endpoint.matched_methods.map(escapeHtml).join(", ")}</div>` : ""}
            ${(endpoint.matched_classes || []).length ? `<div class="attribute-evidence">Classes: ${endpoint.matched_classes.map(escapeHtml).join(", ")}</div>` : ""}
            ${(endpoint.branch_evidence || []).length ? `
                <div class="attribute-branch-evidence">
                    ${(endpoint.branch_evidence || []).map(branch => `
                        <div class="attribute-evidence">
                            <strong>${escapeHtml(branch.branch_type || "IF")}:</strong>
                            ${escapeHtml(branch.condition || "")}
                            ${branch.class_name && branch.method_name ? ` · ${escapeHtml(branch.class_name)}.${escapeHtml(branch.method_name)}` : ""}
                        </div>
                    `).join("")}
                </div>
            ` : ""}
            ${(endpoint.dependency_path || []).length ? `
                <div class="dependency-path-inline">
                    ${(endpoint.dependency_path || []).map(step => `<span>${escapeHtml(step)}</span>`).join(`<b>→</b>`)}
                </div>
            ` : ""}
        </div>
    `).join("") : `<div class="muted-box">No endpoint flow currently intersects this attribute.</div>`;

    const jiraHtml = relatedJiras.length ? relatedJiras.map(jira => `
        <div class="attribute-impact-row">
            <div>
                <strong>${escapeHtml(jira.jira_id || "")}</strong>
                <div class="muted-text">${escapeHtml(jira.title || "")}</div>
            </div>
            <span class="tag">${escapeHtml(jira.relationship || "SEMANTIC_RELATED")}</span>
            ${(jira.reasons || []).map(reason => `<div class="attribute-evidence">• ${escapeHtml(reason)}</div>`).join("")}
            <div class="attribute-evidence">${escapeHtml(jira.requirement || "")}</div>
        </div>
    `).join("") : `<div class="muted-box">No saved JIRA requirement matched this attribute yet.</div>`;

    const scenarioHtml = scenarios.length ? scenarios.map(scenario => `
        <div class="attribute-impact-row">
            <div>
                <strong>${escapeHtml(scenario.scenario_code || "")}</strong>
                <div class="muted-text">${escapeHtml(scenario.http_method || "")} ${escapeHtml(scenario.endpoint || "")}</div>
            </div>
            ${(scenario.reasons || []).map(reason => `<div class="attribute-evidence">• ${escapeHtml(reason)}</div>`).join("")}
        </div>
    `).join("") : `<div class="muted-box">No registered scenario intersects this attribute yet.</div>`;

    const historyHtml = history.length ? history.map(item => {
        const jiraIds = item.jira_ids || [];
        const jiraDetails = item.jiras || [];
        const releaseVersion = item.release_version ? `V${item.release_version}` : "";
        const codeVersion = item.code_baseline_version ? `V${item.code_baseline_version}` : "";
        return `
            <div class="attribute-impact-row">
                <div>
                    <strong>${escapeHtml(item.scenario_code || "")}</strong>
                    <div class="muted-text">${escapeHtml(item.http_method || "")} ${escapeHtml(item.endpoint || "")}</div>
                </div>
                <div class="attribute-evidence"><strong>Test baseline:</strong> ${escapeHtml(item.test_baseline || "No test baseline")}${item.test_status ? ` · ${escapeHtml(item.test_status)}` : ""}</div>
                <div class="attribute-evidence"><strong>Release:</strong> ${escapeHtml(item.release || "Legacy")} ${escapeHtml(releaseVersion)}</div>
                <div class="attribute-evidence"><strong>Code baseline:</strong> ${escapeHtml(codeVersion || "—")}</div>
                <div class="attribute-evidence"><strong>JIRA:</strong> ${jiraIds.length ? jiraIds.map(escapeHtml).join(", ") : "—"}</div>
                ${jiraDetails.filter(j => j && j.title).map(j => `<div class="muted-text">${escapeHtml(j.jira_id || "")} · ${escapeHtml(j.title || "")}</div>`).join("")}
            </div>
        `;
    }).join("") : `<div class="muted-box">No captured release/test baseline is traceable to this attribute yet.</div>`;

    return `
        <div class="attribute-impact-summary">
            <div><span>Attribute</span><strong>${escapeHtml(data.attribute || "")}</strong></div>
            <div><span>Occurrences</span><strong>${data.total_occurrences || 0}</strong></div>
            <div><span>Endpoints</span><strong>${endpoints.length}</strong></div>
            <div><span>Scenarios</span><strong>${scenarios.length}</strong></div>
            <div><span>Confidence</span><strong>${escapeHtml(confidence.level || "LOW")} · ${confidence.score || 0}%</strong></div>
        </div>

        <div class="attribute-impact-section">
            <h3>Attribute path through code</h3>
            <div class="attribute-layer-grid">${layerHtml}</div>
        </div>

        <div class="attribute-impact-two-column">
            <div class="attribute-impact-section">
                <h3>Affected endpoints</h3>
                ${endpointHtml}
            </div>
            <div class="attribute-impact-section">
                <h3>Affected scenarios</h3>
                ${scenarioHtml}
            </div>
        </div>

        <div class="attribute-impact-section">
            <h3>Historical Traceability</h3>
            ${historyHtml}
        </div>

        <div class="attribute-impact-section">
            <h3>Related JIRAs</h3>
            ${jiraHtml}
        </div>

        <div class="muted-text attribute-local-note">LangGraph parallel analysis · local Java analysis + local JIRA RAG · no external LLM required.</div>
    `;
}


function renderUnifiedAttributeKnowledge(payload) {
    const local = payload.attribute_impact || {};
    const graph = payload.knowledge_graph || {};
    const projects = graph.projects || [];
    const matches = graph.direct_matches || [];
    const impacted = graph.impacted_nodes || [];
    const relationships = graph.relationships || [];

    const byProject = {};
    impacted.forEach(node => {
        const project = node.project || "Unknown";
        (byProject[project] ||= []).push(node);
    });

    const projectHtml = projects.length ? Object.entries(byProject).map(([project, nodes]) => {
        const useful = nodes.filter(n => n.type !== "PROJECT").slice(0, 8);
        return `<div class="attribute-layer-card">
            <div class="attribute-layer-role">PROJECT</div>
            <div class="attribute-class-name">${escapeHtml(project)}</div>
            <div class="muted-text">${nodes.length} connected graph nodes</div>
            ${useful.map(n => `<div class="attribute-evidence">${escapeHtml(n.type)} · ${escapeHtml(n.name)}</div>`).join("")}
        </div>`;
    }).join("") : `<div class="muted-box">${graph.status === "GRAPH_NOT_BUILT" ? "Build the Knowledge Graph to see cross-project impact." : "No cross-project graph evidence for this attribute."}</div>`;

    const nodeById = new Map(impacted.map(n => [n.id, n]));
    const cross = relationships.filter(r => r.type === "DEPENDS_ON_PROJECT");
    const crossHtml = cross.length ? cross.map(r => {
        const source = nodeById.get(r.source);
        const target = nodeById.get(r.target);
        return `<div class="attribute-impact-row">
            <strong>${escapeHtml(source?.name || source?.project || r.source)}</strong>
            <span class="tag">DEPENDS ON</span>
            <strong>${escapeHtml(target?.name || target?.project || r.target)}</strong>
            ${r.properties?.evidence ? `<div class="attribute-evidence">Evidence: ${escapeHtml(r.properties.evidence)}</div>` : ""}
        </div>`;
    }).join("") : `<div class="muted-box">No deterministic cross-project dependency is connected to this attribute.</div>`;

    return `
        <div class="attribute-impact-summary">
            <div><span>Graph Projects</span><strong>${projects.length}</strong></div>
            <div><span>Direct Graph Matches</span><strong>${matches.length}</strong></div>
            <div><span>Connected Nodes</span><strong>${impacted.length}</strong></div>
            <div><span>Cross-Project Links</span><strong>${graph.cross_project_relationship_count || 0}</strong></div>
        </div>
        ${renderAttributeImpactCore(local)}
        <div class="attribute-impact-section">
            <h3>Cross-Project Knowledge Graph</h3>
            <div class="muted-text">Attribute Impact and Cross-Project Intelligence are consolidated here. The graph is built from deterministic code evidence and mirrored to Neo4j when enabled.</div>
            <div class="attribute-layer-grid" style="margin-top:12px">${projectHtml}</div>
        </div>
        <div class="attribute-impact-section">
            <h3>Cross-Project Dependencies</h3>
            ${crossHtml}
        </div>`;
}

async function rebuildAttributeKnowledgeGraph() {
    const container = document.getElementById("attributeImpactResult");
    try {
        const projectsResponse = await fetch("/api/cross-project/projects");
        const projectsData = await projectsResponse.json();
        if (!projectsResponse.ok) throw new Error(projectsData.detail || JSON.stringify(projectsData));
        const projects = (projectsData.projects || []).map(p => p.name);
        if (!projects.length) throw new Error("No registered Java projects were found.");

        container.innerHTML = `Building Knowledge Graph for ${projects.length} registered Java project(s)...`;
        const response = await fetch("/api/cross-project/sync", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({projects})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));
        container.innerHTML = `<div class="muted-box"><strong>Knowledge Graph ready.</strong> ${data.projects} project(s), ${data.nodes} nodes, ${data.relationships} relationships, ${data.cross_project_relationships} cross-project links. Neo4j: ${escapeHtml(data.neo4j?.status || "UNKNOWN")}. Now analyze an attribute.</div>`;
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

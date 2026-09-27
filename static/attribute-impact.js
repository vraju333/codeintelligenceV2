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
        const response = await fetch(`/api/attribute-lineage/impact?attribute=${encodeURIComponent(attribute)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        container.innerHTML = renderAttributeImpact(data);
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

function renderAttributeImpact(data) {
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

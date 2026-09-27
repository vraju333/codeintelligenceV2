async function loadHackathonDashboard() {
    const result = document.getElementById("hackathonDashboardResult");
    if (!result) return;
    clearCodeDashboardOutputs("dashboard");
    result.innerHTML = "Loading release intelligence...";
    try {
        const response = await fetch("/api/regression/release-intelligence");
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to load release intelligence");
        window.releaseIntelligence = data;
        window.codeDashboardRows = (data.regression_recommendations || []).map(item => ({
            scenario_id: item.scenario_id,
            scenario_code: item.scenario_code || "",
            scenario_name: "",
            endpoint: `${item.http_method || ""} ${item.endpoint || ""}`.trim(),
            latest_version: (item.test_baselines || [])[0]?.code_baseline_version
                ? `V${(item.test_baselines || [])[0].code_baseline_version}` : "—",
            action: item.recommended_action || ""
        }));
        result.innerHTML = renderReleaseIntelligence(data);
    } catch (error) {
        result.innerHTML = `<div class="jira-impact-error">${escapeHackathon(String(error.message || error))}</div>`;
    }
}

function renderReleaseIntelligence(data) {
    const s = data.summary || {};
    const recommendations = data.regression_recommendations || [];
    const gaps = data.coverage_gaps || [];
    const testInfo = data.test_code_analysis || {};
    const recHtml = recommendations.length ? recommendations.map(item => {
        const baselines = item.test_baselines || [];
        const autoTests = item.automated_test_evidence || [];
        return `<div class="phasec-item">
            <div class="phasec-title">${escapeHackathon(item.scenario_code || "Scenario")}
                <span class="tag">${escapeHackathon(item.impact_status || "")}</span>
            </div>
            <div class="muted-text">${escapeHackathon(item.http_method || "")} ${escapeHackathon(item.endpoint || "")}</div>
            ${(item.reasons || []).map(x => `<div>• ${escapeHackathon(x)}</div>`).join("")}
            <div class="phasec-evidence">
                <strong>Captured baselines:</strong> ${baselines.length}
                &nbsp; · &nbsp; <strong>JUnit/static evidence:</strong> ${autoTests.length}
            </div>
            ${autoTests.slice(0, 4).map(t => `<div class="muted-text">Test: ${escapeHackathon(t.test_class)}.${escapeHackathon(t.test_method)} · assertions ${Number(t.assertion_count || 0)}</div>`).join("")}
            <div class="phasec-action">${escapeHackathon(item.recommended_action || "")}</div>
        </div>`;
    }).join("") : `<div class="muted-text">No affected scenarios detected from the current Git changes.</div>`;

    const gapHtml = gaps.length ? gaps.map(g => `<div class="phasec-gap">
        <strong>${escapeHackathon(g.scenario_code || "")}</strong> · ${escapeHackathon(g.gap_type || "")}
        <div class="muted-text">${escapeHackathon(g.detail || "")}</div>
    </div>`).join("") : `<div class="phasec-ok">No evidence gaps detected for the currently affected scenarios.</div>`;

    return `
        <div class="hackathon-kpi-grid phasec-kpis">
            <div><span>Changed files</span><strong>${Number(s.changed_files || 0)}</strong></div>
            <div><span>Changed methods</span><strong>${Number(s.changed_methods || 0)}</strong></div>
            <div><span>Affected scenarios</span><strong>${Number(s.affected_scenarios || 0)}</strong></div>
            <div><span>Coverage gaps</span><strong>${Number(s.coverage_gaps || 0)}</strong></div>
            <div><span>Failed baselines</span><strong>${Number(s.failed_test_baselines || 0)}</strong></div>
            <div><span>JUnit tests found</span><strong>${Number(s.junit_test_methods_discovered || 0)}</strong></div>
        </div>
        <div class="phasec-readiness"><strong>Release evidence:</strong> ${escapeHackathon(data.release_readiness?.message || "")}
            <div class="muted-text">${escapeHackathon(data.release_readiness?.note || "")}</div>
        </div>
        <h3>Regression Recommendations</h3>${recHtml}
        <h3>Coverage Gaps</h3>${gapHtml}
        <details class="phasec-test-details"><summary>Test Code Analysis</summary>
            <div>Test files: <strong>${Number(testInfo.test_files || 0)}</strong> · Test methods: <strong>${Number(testInfo.test_methods || 0)}</strong></div>
            <div class="muted-text">${escapeHackathon(testInfo.limitations || "")}</div>
        </details>
    `;
}

async function runScenarioImpactGraph() {
    const file = document.getElementById("scenarioImpactFile")?.value?.trim() || "";
    const result = document.getElementById("scenarioImpactResult");
    if (!result) return;
    clearCodeDashboardOutputs("impact");
    if (!file) {
        result.innerHTML = `<div class="jira-impact-error">Enter a changed file or class name.</div>`;
        return;
    }
    if (!Array.isArray(window.codeDashboardRows)) {
        await loadHackathonDashboard();
    }
    const fileTokens = dashboardTokens(file);
    result.innerHTML = "Finding impacted scenarios...";
    const backendMatches = await findScenariosByAttributeImpact(fileTokens);
    if (backendMatches.length) {
        result.innerHTML = backendMatches.map(item => `
            <div class="jira-impact-item">
                <div class="jira-impact-item-title">${escapeHackathon(item.scenario_code || "")}</div>
                <div class="jira-impact-item-meta">${escapeHackathon(item.http_method || "")} ${escapeHackathon(item.endpoint || "")}</div>
                ${(item.reasons || []).map(reason => `<div class="jira-impact-reason">• ${escapeHackathon(reason)}</div>`).join("")}
            </div>
        `).join("");
        return;
    }

    const matches = (window.codeDashboardRows || []).filter(row => {
        const rowTokens = dashboardTokens(`${row.scenario_code} ${row.scenario_name} ${row.endpoint}`);
        return fileTokens.some(token => rowTokens.includes(token));
    });
    result.innerHTML = matches.length
        ? matches.map(row => `
            <div class="jira-impact-item">
                <div class="jira-impact-item-title">${escapeHackathon(row.scenario_code)}</div>
                <div class="jira-impact-item-meta">${escapeHackathon(row.endpoint)} · ${escapeHackathon(row.latest_version)}</div>
                <div class="jira-impact-reason">Matched changed file/module token with scenario or endpoint naming.</div>
                <div class="jira-impact-reason">Action: ${escapeHackathon(row.action)}</div>
            </div>
        `).join("")
        : renderNoImpactMatch(file, fileTokens);
}

function dashboardTokens(value) {
    const cleaned = String(value || "")
        .replace(/\.(java|py)$/ig, "")
        .replace(/schema|model|entity|mapper|repository|service|controller|request|response|details/ig, " ")
        .replace(/[^A-Za-z0-9]+/g, " ")
        .toLowerCase()
        .split(/\s+/)
        .filter(token => token.length > 2);
    const tokens = new Set();
    for (const token of cleaned) {
        tokens.add(token);
        if (token.endsWith("ies") && token.length > 4) tokens.add(`${token.slice(0, -3)}y`);
        if (token.endsWith("s") && token.length > 3) tokens.add(token.slice(0, -1));
    }
    return [...tokens];
}

async function findScenariosByAttributeImpact(tokens) {
    const preferred = tokens.filter(token => !["main", "init", "config", "database"].includes(token));
    for (const token of preferred) {
        try {
            const response = await fetch(`/api/attribute-lineage/impact?attribute=${encodeURIComponent(token)}`);
            const data = await response.json();
            if (!response.ok) continue;
            const scenarios = Array.isArray(data.affected_scenarios) ? data.affected_scenarios : [];
            if (scenarios.length) return scenarios;
        } catch (_) {
            // Keep trying remaining tokens; this is a best-effort dashboard shortcut.
        }
    }
    return [];
}

function renderNoImpactMatch(file, tokens) {
    const allRows = window.codeDashboardRows || [];
    const fallback = allRows.slice(0, 5).map(row => `
        <div class="jira-impact-item">
            <div class="jira-impact-item-title">${escapeHackathon(row.scenario_code)}</div>
            <div class="jira-impact-item-meta">${escapeHackathon(row.endpoint)} · ${escapeHackathon(row.latest_version)}</div>
            <div class="jira-impact-reason">No direct match for ${escapeHackathon(file)}. Review if this shared module is used by this scenario.</div>
        </div>
    `).join("");
    return `
        <div class="jira-impact-empty">
            No direct scenario match found for <strong>${escapeHackathon(file)}</strong>.
            Tried tokens: ${tokens.map(escapeHackathon).join(", ") || "none"}.
        </div>
        ${fallback ? `<div class="jira-impact-note">Showing current scenarios for manual review:</div>${fallback}` : ""}
    `;
}


function clearCodeDashboardOutputs(active) {
    const placeholders = {
        dashboard: "Load the dashboard to see scenario risk and release readiness.",
        impact: "Changed file impact appears here."
    };
    for (const key of Object.keys(placeholders)) {
        if (key === active) continue;
        const id = key === "dashboard" ? "hackathonDashboardResult" : "scenarioImpactResult";
        const element = document.getElementById(id);
        if (element) element.innerHTML = `<div class="muted-text">${placeholders[key]}</div>`;
    }
}

function escapeHackathon(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
}

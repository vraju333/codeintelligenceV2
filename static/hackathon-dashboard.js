async function loadHackathonDashboard() {
    const result = document.getElementById("hackathonDashboardResult");
    if (!result) return;
    clearCodeDashboardOutputs("dashboard");
    result.innerHTML = "Loading dashboard...";
    try {
        const [overviewResponse, scenarioResponse] = await Promise.all([
            fetch("/api/scenario-baselines/overview"),
            fetch("/api/scenarios/active-project")
        ]);
        const overview = overviewResponse.ok ? await overviewResponse.json() : [];
        const scenarios = scenarioResponse.ok ? await scenarioResponse.json() : [];
        const rows = buildHackathonDashboardRows(Array.isArray(overview) ? overview : [], Array.isArray(scenarios) ? scenarios : []);
        window.codeDashboardRows = rows;
        result.innerHTML = renderHackathonDashboard(rows);
    } catch (error) {
        result.innerHTML = `<div class="jira-impact-error">${escapeHackathon(String(error.message || error))}</div>`;
    }
}

function buildHackathonDashboardRows(overview, scenarios) {
    const scenarioById = new Map(scenarios.map(item => [String(item.id), item]));
    const rows = overview.map(item => {
        const scenario = scenarioById.get(String(item.scenario_id)) || {};
        const tests = collectHackathonTests(item);
        const failed = tests.filter(test => String(test.status || "").toUpperCase() === "FAIL").length;
        const notRun = tests.filter(test => !["PASS", "FAIL"].includes(String(test.status || "").toUpperCase())).length;
        const jiras = [...new Set(tests.flatMap(test => Array.isArray(test.jira_ids) ? test.jira_ids : []))];
        const latest = latestHackathonVersion(item);
        const hasActiveBaseline = Boolean(item.baseline_captured || item.active_baseline_id || latest);
        const risk = failed > 0 ? "HIGH" : notRun > 0 ? "MEDIUM" : latest ? "LOW" : "UNKNOWN";
        const action = failed > 0
            ? "Review failed test data and changed files"
            : notRun > 0
                ? "Run or capture missing test baseline"
                : hasActiveBaseline
                    ? "Ready for release review"
                    : "Create first baseline";
        return {
            scenario_id: item.scenario_id || scenario.id,
            scenario_code: item.scenario_code || scenario.scenario_code || "",
            scenario_name: item.scenario_name || scenario.scenario_name || "",
            endpoint: `${item.http_method || scenario.http_method || ""} ${item.endpoint || scenario.endpoint || ""}`.trim(),
            latest_version: latest
                ? `${latest.baseline_name || "Release"} V${latest.release_version || latest.baseline_version || 1}`
                : item.active_baseline_id
                    ? `${item.active_baseline_name || "Release"} V${item.active_release_version || item.active_baseline_version || 1}`
                    : "No baseline",
            changed_files: Number(latest?.changed_source_files || item.changed_source_files || 0),
            failed_tests: failed,
            risk: failed > 0 ? "HIGH" : notRun > 0 ? "MEDIUM" : hasActiveBaseline ? "LOW" : "UNKNOWN",
            jiras,
            action,
        };
    });
    return rows.sort((a, b) => riskRank(b.risk) - riskRank(a.risk) || a.scenario_code.localeCompare(b.scenario_code));
}

function collectHackathonTests(item) {
    const tests = [];
    for (const release of item.releases || []) {
        for (const version of release.versions || []) {
            tests.push(...(version.tests || []));
        }
    }
    tests.push(...(item.test_baselines || []));
    return tests;
}

function latestHackathonVersion(item) {
    const versions = [];
    for (const release of item.releases || []) {
        for (const version of release.versions || []) versions.push({...version, baseline_name: release.release});
    }
    return versions.sort((a, b) => Number(b.internal_version || b.baseline_version || 0) - Number(a.internal_version || a.baseline_version || 0))[0];
}

function riskRank(risk) {
    return {HIGH: 3, MEDIUM: 2, LOW: 1, UNKNOWN: 0}[String(risk || "").toUpperCase()] || 0;
}

function renderHackathonDashboard(rows) {
    const total = rows.length;
    const high = rows.filter(row => row.risk === "HIGH").length;
    const medium = rows.filter(row => row.risk === "MEDIUM").length;
    const failed = rows.reduce((sum, row) => sum + row.failed_tests, 0);
    const table = rows.length ? rows.map(row => `
        <tr>
            <td><strong>${escapeHackathon(row.scenario_code)}</strong><div class="muted-text">${escapeHackathon(row.scenario_name)}</div></td>
            <td>${escapeHackathon(row.latest_version)}</td>
            <td>${Number(row.changed_files || 0)}</td>
            <td>${Number(row.failed_tests || 0)}</td>
            <td><span class="tag risk-${escapeHackathon(row.risk.toLowerCase())}">${escapeHackathon(row.risk)}</span></td>
            <td>${row.jiras.length ? row.jiras.map(id => `<span class="testing-jira-chip">${escapeHackathon(id)}</span>`).join("") : `<span class="muted-text">None</span>`}</td>
            <td>${escapeHackathon(row.action)}</td>
        </tr>
    `).join("") : `<tr><td colspan="7">No scenario baselines found yet.</td></tr>`;
    return `
        <div class="hackathon-kpi-grid">
            <div><span>Total scenarios</span><strong>${total}</strong></div>
            <div><span>High risk</span><strong>${high}</strong></div>
            <div><span>Medium risk</span><strong>${medium}</strong></div>
            <div><span>Failed tests</span><strong>${failed}</strong></div>
        </div>
        <table class="hackathon-dashboard-table">
            <thead><tr><th>Scenario</th><th>Latest version</th><th>Files</th><th>Failed</th><th>Risk</th><th>JIRAs</th><th>Action needed</th></tr></thead>
            <tbody>${table}</tbody>
        </table>
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

/*
 * Release-aware Version History.
 * Hierarchy: Release -> Version -> Test Scenarios/JIRAs.
 */

let versionHistoryState = {scenarioId: null, scenarioCode: "", scenarioLabel: "", versions: [], tests: []};

function vhReleaseVersion(item) {
    return Number(item?.release_version || item?.baseline_version || 1);
}

function vhReleaseName(item) {
    return String(item?.baseline_name || "Legacy").trim() || "Legacy";
}

function vhGroupedVersions() {
    const groups = new Map();
    for (const item of versionHistoryState.versions) {
        const name = vhReleaseName(item);
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push(item);
    }
    for (const list of groups.values()) list.sort((a,b) => vhReleaseVersion(a) - vhReleaseVersion(b));
    return groups;
}

async function loadBaselineHistory() {
    const select = document.getElementById("baselineScenarioSelect");
    const scenarioId = select?.value;
    const selectedOption = select?.options?.[select.selectedIndex];
    const scenarioLabel = selectedOption?.textContent?.trim() || "";
    const overviewItem = (typeof baselineOverviewData !== "undefined" && Array.isArray(baselineOverviewData))
        ? baselineOverviewData.find(x => String(x.scenario_id) === String(scenarioId))
        : null;
    const scenarioCode = overviewItem?.scenario_code || scenarioLabel.split("—")[1]?.trim() || "Scenario";
    const container = document.getElementById("baselineResult");
    if (!scenarioId) {
        container.innerHTML = "Select an operation scenario first.";
        return;
    }
    container.innerHTML = "Loading release/version history...";
    try {
        const [historyResponse, testingResponse] = await Promise.all([
            fetch(`/api/scenario-baselines/history/${scenarioId}`),
            fetch(`/api/scenario-baselines/testing/${scenarioId}`)
        ]);
        const history = await historyResponse.json();
        const testing = await testingResponse.json();

        // Do not render a late response for a scenario the user has already switched away from.
        if (String(document.getElementById("baselineScenarioSelect")?.value || "") !== String(scenarioId)) return;
        if (!historyResponse.ok) throw new Error(JSON.stringify(history));
        if (!testingResponse.ok) throw new Error(JSON.stringify(testing));

        versionHistoryState = {
            scenarioId: Number(scenarioId),
            scenarioCode,
            scenarioLabel,
            versions: (Array.isArray(history) ? history : []).filter(x =>
                x.scenario_id == null || Number(x.scenario_id) === Number(scenarioId)
            ),
            tests: (Array.isArray(testing) ? testing : []).filter(x =>
                x.scenario_id == null || Number(x.scenario_id) === Number(scenarioId)
            )
        };
        if (!versionHistoryState.versions.length) {
            container.innerHTML = `<div class="muted-box">No baseline versions captured yet.</div>`;
            return;
        }
        const groups = vhGroupedVersions();
        const active = versionHistoryState.versions.find(x => x.is_active) || versionHistoryState.versions[0];
        const releaseNames = [...groups.keys()];
        const defaultRelease = groups.has(vhReleaseName(active)) ? vhReleaseName(active) : releaseNames[0];

        const flatOptions = [...versionHistoryState.versions]
            .sort((a,b) => Number(a.baseline_version) - Number(b.baseline_version))
            .map(v => `<option value="${v.baseline_version}">${escapeHtml(vhReleaseName(v))} · V${vhReleaseVersion(v)}</option>`).join("");

        container.innerHTML = `
            <div class="version-history-scenario-context" style="margin-bottom:16px;padding:14px 16px;border:1px solid #dbe4ef;border-radius:12px;background:#f8fafc;">
                <div class="muted-text">Version History</div>
                <strong style="font-size:18px;">${escapeHtml(scenarioCode)}</strong>
                <div class="muted-text">${escapeHtml(overviewItem?.http_method || "")} ${escapeHtml(overviewItem?.endpoint || "")}</div>
                <div class="muted-text">Only releases, versions, test scenarios and JIRAs for this scenario are shown below.</div>
            </div>
            <div class="release-history-toolbar">
                <label>Release
                    <select id="historyReleaseSelect" onchange="renderSelectedReleaseHistory()">
                        ${releaseNames.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("")}
                    </select>
                </label>
            </div>
            <div id="historyReleaseVersions"></div>
            ${versionHistoryState.versions.length >= 2 ? `
                <div class="version-auto-change-card release-compare-card">
                    <div class="version-auto-change-header">
                        <div>
                            <strong>Compare Baseline Versions</strong>
                            <div class="muted-text">Compare October V1 → October V2, or October V3 → November V1.</div>
                        </div>
                    </div>
                    <div class="scenario-create-grid">
                        <label>From<select id="compareFromVersion">${flatOptions}</select></label>
                        <label>To<select id="compareToVersion">${flatOptions}</select></label>
                    </div>
                    <button type="button" onclick="compareBaselineVersions()">Compare</button>
                    <div id="versionCompareResult" class="inline-version-changes"></div>
                </div>` : ``}
        `;
        const releaseSelect = document.getElementById("historyReleaseSelect");
        if (releaseSelect) releaseSelect.value = defaultRelease;
        renderSelectedReleaseHistory();

        if (versionHistoryState.versions.length >= 2) {
            const ordered = [...versionHistoryState.versions].sort((a,b) => Number(a.baseline_version)-Number(b.baseline_version));
            document.getElementById("compareFromVersion").value = String(ordered[Math.max(0, ordered.length-2)].baseline_version);
            document.getElementById("compareToVersion").value = String(ordered[ordered.length-1].baseline_version);
        }
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

function renderSelectedReleaseHistory() {
    const release = document.getElementById("historyReleaseSelect")?.value;
    const target = document.getElementById("historyReleaseVersions");
    if (!release || !target) return;
    const versions = (vhGroupedVersions().get(release) || []);
    target.innerHTML = versions.map(v => {
        const tests = versionHistoryState.tests.filter(t =>
            Number(t.baseline_id || 0) === Number(v.id) ||
            (!t.baseline_id && Number(t.code_baseline_version) === Number(v.baseline_version))
        );
        return `
            <div class="active-baseline-card release-version-card">
                <div>
                    <span class="muted-text">${escapeHtml(release)}</span>
                    <h3>V${vhReleaseVersion(v)} ${v.is_active ? `<span class="tag">ACTIVE</span>` : ``}</h3>
                </div>
                <div class="baseline-detail">${escapeHtml(v.http_method)} ${escapeHtml(v.endpoint)}</div>
                <div class="baseline-detail">Captured: ${v.created_at ? escapeHtml(new Date(v.created_at).toLocaleString()) : "-"}</div>
                <div class="testing-version-tests">
                    <strong>Test Scenarios (${tests.length})</strong>
                    ${tests.length ? tests.map(t => `
                        <div class="testing-history-jiras version-test-row">
                            <div>
                                <strong>${escapeHtml(t.baseline_name || "Test Scenario")}</strong>
                                <span class="tag">${escapeHtml(t.status || "NOT_RUN")}</span>
                            </div>
                            <div class="testing-jira-list">
                                ${(Array.isArray(t.jira_ids) && t.jira_ids.length) ? t.jira_ids.map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("") : `<span class="muted-text">No JIRA</span>`}
                            </div>
                        </div>`).join("") : `<div class="muted-box">No test scenarios saved under ${escapeHtml(release)} V${vhReleaseVersion(v)}.</div>`}
                </div>
            </div>`;
    }).join("");
}

async function compareBaselineVersions() {
    const scenarioId = versionHistoryState.scenarioId || document.getElementById("baselineScenarioSelect")?.value;
    const fromVersion = document.getElementById("compareFromVersion")?.value;
    const toVersion = document.getElementById("compareToVersion")?.value;
    const result = document.getElementById("versionCompareResult");
    if (!scenarioId || !fromVersion || !toVersion || !result) return;
    if (fromVersion === toVersion) {
        result.innerHTML = `<div class="warning">Choose two different versions.</div>`;
        return;
    }
    result.innerHTML = "Comparing versions...";
    try {
        const response = await fetch(`/api/scenario-baselines/compare/${scenarioId}?from_version=${fromVersion}&to_version=${toVersion}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        result.innerHTML = renderReleaseVersionComparison(data);
    } catch (error) {
        result.innerHTML = renderError(error.message);
    }
}

function renderReleaseVersionComparison(data) {
    const fromLabel = `${data.from_release_name || "Release"} V${data.from_release_version || data.from_version}`;
    const toLabel = `${data.to_release_name || "Release"} V${data.to_release_version || data.to_version}`;
    const source = data.source_comparison || {};
    const summary = data.change_summary || {};
    const testing = data.testing_comparison || {};
    const changedFiles = source.changed_files || [];
    const changedFileRows = changedFiles.map(file => `
        <div class="version-source-row version-file-change-row">
            <strong>${escapeHtml(file.status || "MODIFIED")}</strong>
            <span>${escapeHtml(file.file_path || "")}</span>
        </div>
    `).join("");
    const codeChangeRows = (source.changes || []).map(change => `
        <div class="version-source-row">
            <strong>${escapeHtml(String(change.change_type || "CHANGED").replaceAll("_"," "))}</strong>
            <span>${escapeHtml(change.file_path || change.symbol || "")}</span>
        </div>
    `).join("");

    return `
        <div class="version-transition-banner">
            <strong>${escapeHtml(fromLabel)} → ${escapeHtml(toLabel)}</strong>
            <span>${escapeHtml(data.scenario_code || "")}</span>
            <span class="tag">${data.scenario_impact?.changed ? "CHANGED" : "NO MATERIAL CHANGE"}</span>
        </div>
        <div class="version-change-summary-grid">
            <div><span>Source files changed</span><strong>${Number(summary.changed_source_files || 0)}</strong></div>
            <div><span>Source changes</span><strong>${Number(summary.classified_source_changes || 0)}</strong></div>
            <div><span>Flow methods + / -</span><strong>${Number(summary.flow_methods_added || 0) + Number(summary.flow_methods_removed || 0)}</strong></div>
            <div><span>Testing changed</span><strong>${testing.available ? (testing.changed ? "YES" : "NO") : "N/A"}</strong></div>
        </div>
        ${codeChangeRows ? `
            <div class="version-change-section">
                <h4>Code changes</h4>
                ${codeChangeRows}
            </div>
        ` : `
            <div class="muted-box">No classified source change stored for this comparison.</div>
        `}
        ${changedFileRows ? `
            <div class="version-change-section">
                <h4>Changed source files</h4>
                ${changedFileRows}
            </div>
        ` : ""}
        ${testing.available ? `<div class="version-change-section"><h4>Test/JIRA comparison</h4><div class="muted-text">${escapeHtml(testing.from_name || "No test")} → ${escapeHtml(testing.to_name || "No test")}</div><div class="testing-jira-list">${(testing.from_jiras || []).map(id=>`<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("") || "None"} <b>→</b> ${(testing.to_jiras || []).map(id=>`<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("") || "None"}</div></div>` : ``}
    `;
}

async function loadInlineVersionChanges(
    scenarioId,
    fromVersion,
    toVersion
) {
    const container = document.getElementById(
        "inlineVersionChanges"
    );

    if (!container) return;

    container.innerHTML = "Comparing versions...";

    try {
        const response = await fetch(
            `/api/scenario-baselines/compare/${scenarioId}`
            + `?from_version=${fromVersion}`
            + `&to_version=${toVersion}`
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                JSON.stringify(data)
            );
        }

        container.innerHTML = renderVersionSourceSummary(
            data
        );

    } catch (error) {
        container.innerHTML = renderError(
            error.message
        );
    }
}


function renderVersionSourceSummary(data) {
    const source = data.source_comparison || {};
    const changes = source.changes || [];
    const changedFiles = source.changed_files || [];

    const sourceRows = changes.map(change => {
        const type = change.change_type || "SOURCE_CHANGED";
        const cssClass =
            type.includes("ADDED") ? "version-added" :
            type.includes("REMOVED") ? "version-removed" :
            "version-modified";

        const symbol = change.symbol
            ? `<strong>${escapeHtml(change.symbol)}</strong>`
            : "";

        const typeName = change.data_type
            ? ` : ${escapeHtml(change.data_type)}`
            : "";

        return `
            <div class="version-source-row ${cssClass}">
                <span class="version-change-symbol">${
                    type.includes("ADDED") ? "+" :
                    type.includes("REMOVED") ? "−" : "~"
                }</span>
                <div>
                    <div>
                        <strong>${escapeHtml(type.replaceAll("_", " "))}</strong>
                        ${change.line_number ? `<span class="muted-text"> · line ${change.line_number}</span>` : ""}
                    </div>
                    <div class="version-source-symbol">${symbol}${typeName}</div>
                    ${change.file_path ? `<div class="muted-text">${escapeHtml(change.file_path)}</div>` : ""}
                    ${change.code ? `<code>${escapeHtml(change.code)}</code>` : ""}
                </div>
            </div>
        `;
    }).join("");

    const fileRows = changedFiles.map(file => `
        <div class="version-file-row">
            <strong>${escapeHtml(file.status || "MODIFIED")}</strong>
            <span>${escapeHtml(file.file_path || "")}</span>
        </div>
    `).join("");

    const flowAdded = data.added_methods || [];
    const flowRemoved = data.removed_methods || [];
    const response = data.response_changes || {};

    const responseCount =
        (response.added_attributes || []).length
        + (response.removed_attributes || []).length
        + (response.changed_attributes || []).length;

    const expectedResponse = data.expected_response_changes || {};
    const expectedCount =
        (expectedResponse.added_attributes || []).length
        + (expectedResponse.removed_attributes || []).length
        + (expectedResponse.changed_attributes || []).length;
    const summary = data.change_summary || {};
    const scenarioImpact = data.scenario_impact || {};
    const testing = data.testing_comparison || {};

    const renderAttributeRows = (value) => [
        ...(value.added_attributes || []).map(item => `<div class="version-source-row version-added"><span class="version-change-symbol">+</span><div><strong>${escapeHtml(item.path || "")}</strong><div class="muted-text">${escapeHtml(String(item.value ?? ""))}</div></div></div>`),
        ...(value.removed_attributes || []).map(item => `<div class="version-source-row version-removed"><span class="version-change-symbol">−</span><div><strong>${escapeHtml(item.path || "")}</strong><div class="muted-text">${escapeHtml(String(item.value ?? ""))}</div></div></div>`),
        ...(value.changed_attributes || []).map(item => `<div class="version-source-row version-modified"><span class="version-change-symbol">~</span><div><strong>${escapeHtml(item.path || "")}</strong><div class="muted-text">${escapeHtml(String(item.from ?? ""))} → ${escapeHtml(String(item.to ?? ""))}</div></div></div>`),
    ].join("");

    return `
        <div class="version-transition-banner">
            <strong>${escapeHtml(scenarioImpact.version_transition || `V${data.from_version} → V${data.to_version}`)}</strong>
            <span>${escapeHtml(data.scenario_code || "")}</span>
            <span class="tag">${scenarioImpact.changed ? "CHANGED" : "NO MATERIAL CHANGE"}</span>
        </div>

        ${source.message ? `
            <div class="version-snapshot-note">
                ${escapeHtml(source.message)}
            </div>
        ` : ""}

        <div class="version-snapshot-note">
            Dependency-set differences show classes entering/leaving the stored execution scenario. They do not mean Java source files were added or deleted.
        </div>

        <div class="version-change-summary-grid">
            <div>
                <span>Source files changed</span>
                <strong>${changedFiles.length}</strong>
            </div>
            <div>
                <span>Source changes</span>
                <strong>${changes.length}</strong>
            </div>
            <div>
                <span>Flow methods added/removed</span>
                <strong>${flowAdded.length + flowRemoved.length}</strong>
            </div>
            <div>
                <span>Response changes</span>
                <strong>${responseCount}</strong>
            </div>
            <div>
                <span>Expected response changes</span>
                <strong>${expectedCount}</strong>
            </div>
            <div>
                <span>Endpoint changed</span>
                <strong>${data.endpoint_changed ? "YES" : "NO"}</strong>
            </div>
            <div>
                <span>DB effect changed</span>
                <strong>${data.db_effect_changed ? "YES" : "NO"}</strong>
            </div>
            <div>
                <span>Scenario dependency-set changes</span>
                <strong>${(data.added_classes || []).length + (data.removed_classes || []).length}</strong>
            </div>
        </div>

        <div class="version-change-section">
            <h4>Code changes</h4>
            ${sourceRows || `<div class="muted-box">No classified source change was stored.</div>`}
        </div>

        ${fileRows ? `
            <details class="technical-details">
                <summary>Changed source files (${changedFiles.length})</summary>
                ${fileRows}
            </details>
        ` : ""}

        ${(flowAdded.length || flowRemoved.length || (data.added_classes || []).length || (data.removed_classes || []).length) ? `
            <details class="technical-details">
                <summary>Technical baseline differences (${flowAdded.length + flowRemoved.length + (data.added_classes || []).length + (data.removed_classes || []).length})</summary>
                <div class="version-snapshot-note">Stored flow/dependency differences are diagnostic evidence only. They do not mean Java source files were added or deleted.</div>
                ${(flowAdded.length || flowRemoved.length) ? `
                    <div class="version-change-section">
                        <h4>Execution-flow changes</h4>
                        ${flowAdded.map(method => `<div class="version-source-row version-added"><span class="version-change-symbol">+</span><strong>${escapeHtml(method)}</strong></div>`).join("")}
                        ${flowRemoved.map(method => `<div class="version-source-row version-removed"><span class="version-change-symbol">−</span><strong>${escapeHtml(method)}</strong></div>`).join("")}
                    </div>
                ` : ""}
                ${((data.added_classes || []).length || (data.removed_classes || []).length) ? `
                    <div class="version-change-section">
                        <h4>Scenario dependency-set changes</h4>
                        ${(data.added_classes || []).map(name => `<div class="version-source-row version-added"><span class="version-change-symbol">+</span><strong>${escapeHtml(name)}</strong></div>`).join("")}
                        ${(data.removed_classes || []).map(name => `<div class="version-source-row version-removed"><span class="version-change-symbol">−</span><strong>${escapeHtml(name)}</strong></div>`).join("")}
                    </div>
                ` : ""}
            </details>
        ` : ""}

        ${responseCount ? `
            <div class="version-change-section"><h4>Successful response changes</h4>${renderAttributeRows(response)}</div>
        ` : ""}

        ${expectedCount ? `
            <div class="version-change-section"><h4>Expected response changes</h4>${renderAttributeRows(expectedResponse)}</div>
        ` : ""}

        ${testing.available ? `
            <div class="version-change-section">
                <h4>Testing baseline changes</h4>
                ${testing.changed ? `
                    ${renderAttributeRows(testing.request_changes || {})}
                    ${renderAttributeRows(testing.expected_response_changes || {})}
                    ${renderAttributeRows(testing.actual_response_changes || {})}
                    ${testing.status_changed ? `<div class="version-source-row version-modified"><span class="version-change-symbol">~</span><div><strong>Status</strong><div class="muted-text">${escapeHtml(testing.from_status || "-")} → ${escapeHtml(testing.to_status || "-")}</div></div></div>` : ""}
                    ${testing.db_effect_changed ? `<div class="version-source-row version-modified"><span class="version-change-symbol">~</span><div><strong>Expected DB effect</strong><div class="muted-text">${escapeHtml(testing.from_db_effect || "-")} → ${escapeHtml(testing.to_db_effect || "-")}</div></div></div>` : ""}
                    ${testing.jira_changed ? `<div class="version-source-row version-modified"><span class="version-change-symbol">~</span><div><strong>Related JIRAs</strong><div class="muted-text">${escapeHtml((testing.from_jiras || []).join(", ") || "None")} → ${escapeHtml((testing.to_jiras || []).join(", ") || "None")}</div></div></div>` : ""}
                ` : `<div class="muted-box">Testing evidence is unchanged between these two versions.</div>`}
            </div>
        ` : ""}

        ${data.endpoint_changed ? `
            <div class="version-change-section">
                <h4>Endpoint change</h4>
                <div class="version-endpoint-change"><span>${escapeHtml(data.from_endpoint || "")}</span><b>→</b><span>${escapeHtml(data.to_endpoint || "")}</span></div>
            </div>
        ` : ""}

        ${data.db_effect_changed ? `
            <div class="version-change-section">
                <h4>Database-effect change</h4>
                <div class="version-db-change"><div><strong>Before</strong><p>${escapeHtml(data.from_db_effect || "Not stored")}</p></div><b>→</b><div><strong>After</strong><p>${escapeHtml(data.to_db_effect || "Not stored")}</p></div></div>
            </div>
        ` : ""}

        ${source.raw_diff ? `
            <details class="technical-details">
                <summary>View exact source diff V${data.from_version} → V${data.to_version}</summary>
                <pre>${escapeHtml(source.raw_diff)}</pre>
            </details>
        ` : ""}
    `;
}

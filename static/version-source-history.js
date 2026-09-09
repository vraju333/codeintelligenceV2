
/*
 * Source-aware Version History.
 * Loaded after app.js and intentionally overrides loadBaselineHistory.
 */

async function loadBaselineHistory() {
    const select = document.getElementById("baselineScenarioSelect");
    const scenarioId = select?.value;
    const container = document.getElementById("baselineResult");

    if (!scenarioId) {
        container.innerHTML = "Select a scenario first.";
        return;
    }

    container.innerHTML = "Loading baseline history...";

    try {
        const response = await fetch(`/api/scenario-baselines/history/${scenarioId}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(JSON.stringify(data));
        }

        if (!Array.isArray(data) || data.length === 0) {
            container.innerHTML = `<div class="warning">No baseline has been captured for this scenario yet.</div>`;
            return;
        }

        populateVersionCompare(data);

        const versions = [...data].sort(
            (a, b) => b.baseline_version - a.baseline_version
        );

        const active = versions.find(item => item.is_active) || versions[0];
        const previous = versions.filter(item => item.id !== active.id);
        const previousVersion = versions.find(
            item => item.baseline_version < active.baseline_version
        );

        container.innerHTML = `
            <div class="active-baseline-card">
                <div>
                    <span class="muted-text">Current baseline</span>
                    <h3>${escapeHtml(active.scenario_code)} · V${active.baseline_version}</h3>
                </div>
                <span class="tag">ACTIVE</span>
                <div class="baseline-detail">${escapeHtml(active.http_method)} ${escapeHtml(active.endpoint)}</div>
                <div class="baseline-detail">Flow: ${active.endpoint_flow ? "stored" : "not stored"}</div>
            </div>

            ${previousVersion ? `
                <div class="version-auto-change-card">
                    <div class="version-auto-change-header">
                        <div>
                            <strong>Changes from V${previousVersion.baseline_version} → V${active.baseline_version}</strong>
                            <div class="muted-text">What changed when this baseline version became the new working version.</div>
                        </div>
                        <button type="button"
                                class="secondary-button"
                                onclick="loadInlineVersionChanges(
                                    ${Number(scenarioId)},
                                    ${previousVersion.baseline_version},
                                    ${active.baseline_version}
                                )">
                            Show Changes
                        </button>
                    </div>
                    <div id="inlineVersionChanges" class="inline-version-changes">
                        Click <strong>Show Changes</strong> to compare the two versions.
                    </div>
                </div>
            ` : ""}

            ${previous.length ? `
                <details class="technical-details">
                    <summary>Previous versions (${previous.length})</summary>
                    ${previous.map(b => {
                        const next = versions
                            .filter(v => v.baseline_version > b.baseline_version)
                            .sort((x, y) => x.baseline_version - y.baseline_version)[0];
                        return `
                            <div class="history-row version-history-row">
                                <strong>V${b.baseline_version}</strong>
                                <span>${escapeHtml(b.http_method)} ${escapeHtml(b.endpoint)}</span>
                                <span>Flow: ${b.endpoint_flow ? "stored" : "not stored"}</span>
                                ${next ? `<button type="button" class="secondary-button version-row-button" onclick="loadVersionRowChanges(${Number(scenarioId)}, ${b.baseline_version}, ${next.baseline_version}, this)">Changes → V${next.baseline_version}</button>` : ""}
                            </div>
                            <div class="version-row-result" data-from="${b.baseline_version}" data-to="${next ? next.baseline_version : ""}"></div>
                        `;
                    }).join("")}
                </details>
            ` : `<div class="muted-box">No previous versions.</div>`}
        `;

    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
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


async function loadVersionRowChanges(scenarioId, fromVersion, toVersion, button) {
    const row = button?.closest(".version-history-row");
    const container = row?.nextElementSibling;
    if (!container) return;
    container.innerHTML = "Comparing versions...";
    try {
        const response = await fetch(`/api/scenario-baselines/compare/${scenarioId}?from_version=${fromVersion}&to_version=${toVersion}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        container.innerHTML = renderVersionSourceSummary(data);
    } catch (error) {
        container.innerHTML = renderError(error.message);
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
                <summary>View Git diff captured with V${data.to_version}</summary>
                <pre>${escapeHtml(source.raw_diff)}</pre>
            </details>
        ` : ""}
    `;
}

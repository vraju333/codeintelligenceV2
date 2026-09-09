
function resetProjectDrivenUi(message = "Loading selected project...") {
    try { discoveredEndpoints = []; } catch (_) {}

    ["flowEndpoint", "chartEndpoint", "investigationEndpoint"].forEach(id => {
        const select = document.getElementById(id);
        if (select) select.innerHTML = `<option value="">${escapeHtml(message)}</option>`;
    });

    const panels = {
        flowResult: "Select an endpoint and click Analyse Flow.",
        flowchartResult: "Generate a scenario flowchart.",
        investigationResult: "Investigation result will appear here.",
        scenarioList: "Loading scenarios...",
        baselineOverview: "Loading scenario baselines...",
        baselineResult: "Select a scenario above to view older baseline versions.",
        regressionResult: "Regression impact will refresh for the selected project."
    };

    Object.entries(panels).forEach(([id, text]) => {
        const element = document.getElementById(id);
        if (element) element.innerHTML = escapeHtml(text);
    });

    const pager = document.getElementById("scenarioPager");
    if (pager) pager.innerHTML = "";

    const baselineSelect = document.getElementById("baselineScenarioSelect");
    if (baselineSelect) baselineSelect.innerHTML = '<option value="">Select a scenario...</option>';

    // Defect JSON from the previous Java project is misleading after a project switch.
    ["inputJson", "expectedJson", "actualJson"].forEach(id => {
        const field = document.getElementById(id);
        if (field) field.value = "";
    });

    if (window.currentFlowchart) window.currentFlowchart = null;
    if (window.currentDefectTraces) window.currentDefectTraces = {};
}

let scenarioPage = 1;
const scenarioPageSize = 5;

window.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    await loadScenarios(1);
});

async function loadProjects() {
    const select = document.getElementById("projectSelect");
    const pathLabel = document.getElementById("activeProjectPath");
    if (!select) return;

    try {
        const response = await fetch("/api/projects");
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to load projects");

        const projects = data.projects || [];
        select.innerHTML = projects.map(project =>
            `<option value="${escapeHtml(project.path)}" ${project.active ? "selected" : ""}>${escapeHtml(project.name)}${project.exists ? "" : " (missing)"}</option>`
        ).join("");

        if (pathLabel) pathLabel.textContent = data.active_project_path || "No project selected";
    } catch (error) {
        if (pathLabel) pathLabel.textContent = `Project load failed: ${error.message}`;
    }
}

async function switchProject() {
    const select = document.getElementById("projectSelect");
    const button = document.getElementById("projectSwitchButton");
    const pathLabel = document.getElementById("activeProjectPath");
    if (!select?.value) return;

    if (button) {
        button.disabled = true;
        button.textContent = "Switching...";
    }

    resetProjectDrivenUi("Switching project...");

    try {
        const response = await fetch("/api/projects/select", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({project_path: select.value})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));

        if (pathLabel) pathLabel.textContent = data.project_path;
        const health = document.getElementById("healthStatus");
        if (health) {
            const ragStatus = data.rag?.status ? ` · RAG ${data.rag.status}` : " · RAG ready";
            health.textContent = `Ready · ${data.total_java_files} Java files${ragStatus}`;
            health.className = "status-badge status-success";
        }

        scenarioPage = 1;
        await loadProjectEndpoints();
        await loadScenarios(1);
        if (typeof loadBaselineOverview === "function") await loadBaselineOverview();

        // Regression Impact is project-specific. Re-run it after the project
        // switch completes so results from the previous repository never remain.
        if (typeof analyseRegression === "function") {
            await analyseRegression();
        }
    } catch (error) {
        alert(`Project switch failed: ${error.message}`);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Use Project";
        }
    }
}

async function addProject() {
    const path = prompt("Enter the Java project folder path:");
    if (!path?.trim()) return;

    const button = document.querySelector('button[onclick="addProject()"]');
    if (button) {
        button.disabled = true;
        button.textContent = "Indexing...";
    }

    resetProjectDrivenUi("Indexing new project...");

    try {
        const response = await fetch("/api/projects/register", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({project_path: path.trim()})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));

        await loadProjects();

        const select = document.getElementById("projectSelect");
        if (select) select.value = data.project.path;

        const pathLabel = document.getElementById("activeProjectPath");
        if (pathLabel) pathLabel.textContent = data.project.path;

        const health = document.getElementById("healthStatus");
        if (health) {
            const ragStatus = data.rag?.status ? ` · RAG ${data.rag.status}` : " · RAG ready";
            health.textContent = `Ready · ${data.total_java_files} Java files${ragStatus}`;
            health.className = "status-badge status-success";
        }

        scenarioPage = 1;
        await loadProjectEndpoints();
        await loadScenarios(1);
        if (typeof loadBaselineOverview === "function") await loadBaselineOverview();

        // Regression Impact is project-specific. Re-run it after the project
        // switch completes so results from the previous repository never remain.
        if (typeof analyseRegression === "function") {
            await analyseRegression();
        }
    } catch (error) {
        alert(`Could not add/index project: ${error.message}`);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Add Project";
        }
    }
}

async function loadScenarios(page = scenarioPage) {
    const container = document.getElementById("scenarioList");
    const pager = document.getElementById("scenarioPager");
    if (!container) return;

    scenarioPage = Math.max(1, Number(page) || 1);
    container.innerHTML = "Loading...";

    try {
        const response = await fetch(`/api/scenarios/page?page=${scenarioPage}&page_size=${scenarioPageSize}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));

        const scenarios = data.items || [];
        if (!scenarios.length) {
            container.innerHTML = `<div class="empty-project-state">No registered scenarios match the selected project.</div>`;
        } else {
            container.innerHTML = scenarios.map(scenario => `
                <div class="scenario">
                    <div class="method">${escapeHtml(scenario.http_method)}</div>
                    <div><strong>${escapeHtml(scenario.scenario_code)}</strong><div>${escapeHtml(scenario.scenario_name || "")}</div></div>
                    <div class="endpoint">${escapeHtml(scenario.endpoint)}</div>
                    <div><span class="tag">${escapeHtml(scenario.status || "ACTIVE")}</span></div>
                </div>
            `).join("");
        }

        renderScenarioPager(data, pager);
    } catch (error) {
        container.innerHTML = typeof renderError === "function" ? renderError(error.message) : escapeHtml(error.message);
        if (pager) pager.innerHTML = "";
    }
}

function renderScenarioPager(data, pager) {
    if (!pager) return;
    const totalPages = Number(data.total_pages || 0);
    const page = Number(data.page || 1);
    const total = Number(data.total || 0);

    if (totalPages <= 1) {
        pager.innerHTML = total ? `<span>${total} scenario${total === 1 ? "" : "s"}</span>` : "";
        return;
    }

    pager.innerHTML = `
        <div class="scenario-page-info">${total} scenarios · Page ${page} of ${totalPages}</div>
        <div class="scenario-page-actions">
            <button ${page <= 1 ? "disabled" : ""} onclick="loadScenarios(${page - 1})">Previous</button>
            <button ${page >= totalPages ? "disabled" : ""} onclick="loadScenarios(${page + 1})">Next</button>
        </div>
    `;
}

// ------------------------------------------------------
// Scenario Registry - create a new scenario
// ------------------------------------------------------
function openNewScenarioModal() {
    const modal = document.getElementById("newScenarioModal");
    if (!modal) return;

    ["newScenarioCode", "newScenarioName", "newScenarioDescription"].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.value = "";
    });
    const error = document.getElementById("newScenarioError");
    if (error) error.textContent = "";

    refreshNewScenarioEndpoints();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    setTimeout(() => document.getElementById("newScenarioCode")?.focus(), 0);
}

function closeNewScenarioModal() {
    const modal = document.getElementById("newScenarioModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
}

function refreshNewScenarioEndpoints() {
    const method = (document.getElementById("newScenarioMethod")?.value || "").toUpperCase();
    const select = document.getElementById("newScenarioEndpoint");
    if (!select) return;

    const matching = (Array.isArray(discoveredEndpoints) ? discoveredEndpoints : [])
        .filter(item => String(item.http_method || item.method || "").toUpperCase() === method)
        .map(item => String(item.endpoint || item.path || ""))
        .filter(Boolean)
        .filter((value, index, values) => values.indexOf(value) === index)
        .sort();

    select.innerHTML = '<option value="">Select a discovered endpoint...</option>' +
        matching.map(endpoint => `<option value="${escapeHtml(endpoint)}">${escapeHtml(endpoint)}</option>`).join("");

    if (!matching.length) {
        select.innerHTML = `<option value="">No ${escapeHtml(method)} endpoints discovered</option>`;
    }
}

async function registerNewScenario() {
    const code = (document.getElementById("newScenarioCode")?.value || "").trim().toUpperCase();
    const name = (document.getElementById("newScenarioName")?.value || "").trim();
    const description = (document.getElementById("newScenarioDescription")?.value || "").trim();
    const method = (document.getElementById("newScenarioMethod")?.value || "").trim().toUpperCase();
    const endpoint = (document.getElementById("newScenarioEndpoint")?.value || "").trim();
    const error = document.getElementById("newScenarioError");
    const button = document.getElementById("registerScenarioButton");

    if (error) error.textContent = "";
    if (!code || !name || !method || !endpoint) {
        if (error) error.textContent = "Scenario Code, Scenario Name, HTTP Method and Endpoint are required.";
        return;
    }
    if (!/^[A-Z0-9_]+$/.test(code)) {
        if (error) error.textContent = "Scenario Code can contain only letters, numbers and underscores.";
        return;
    }

    if (button) { button.disabled = true; button.textContent = "Registering..."; }
    try {
        const response = await fetch("/api/scenarios", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                scenario_code: code,
                scenario_name: name,
                http_method: method,
                endpoint: endpoint,
                description: description || null,
                status: "ACTIVE"
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));

        closeNewScenarioModal();
        scenarioPage = 1;
        await loadScenarios(1);
        if (typeof loadBaselineOverview === "function") await loadBaselineOverview();
    } catch (e) {
        if (error) error.textContent = e.message || "Could not register scenario.";
    } finally {
        if (button) { button.disabled = false; button.textContent = "Register Scenario"; }
    }
}

document.addEventListener("keydown", event => {
    if (event.key === "Escape" && document.getElementById("newScenarioModal")?.classList.contains("open")) {
        closeNewScenarioModal();
    }
});

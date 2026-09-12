
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

    activeProjectScenarios = [];
    const defectScenarioSelect = document.getElementById("defectScenarioSelect");
    if (defectScenarioSelect) {
        defectScenarioSelect.innerHTML = '<option value="">Loading scenarios for selected project...</option>';
    }
    const defectScenarioContext = document.getElementById("defectScenarioContext");
    if (defectScenarioContext) {
        defectScenarioContext.textContent = "Loading scenarios for selected project...";
    }
    const dbEffectBox = document.getElementById("defectExpectedDbEffect");
    if (dbEffectBox) dbEffectBox.hidden = true;

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
let activeProjectScenarios = [];

window.addEventListener("DOMContentLoaded", async () => {
    await loadProjects();
    await loadScenarios(1);
    await loadDefectScenarioOptions();
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
        await loadDefectScenarioOptions();
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
        await loadDefectScenarioOptions();
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


function toggleDefectManualMode() {
    const toggle = document.getElementById("defectManualMode");
    const row = document.getElementById("defectManualEndpointRow");
    const selectedFlow = document.getElementById("defectSelectedFlow");
    const scenarioSelect = document.getElementById("defectScenarioSelect");
    const enabled = !!toggle?.checked;
    if (row) row.hidden = !enabled;
    if (enabled) {
        if (scenarioSelect) scenarioSelect.value = "";
        if (selectedFlow) selectedFlow.textContent = "Manual endpoint selection enabled.";
        if (typeof refreshEndpointDropdown === "function") refreshEndpointDropdown("investigationMethod", "investigationEndpoint");
    } else {
        if (selectedFlow) selectedFlow.textContent = "Select a scenario or enable manual endpoint selection.";
    }
}

async function loadDefectScenarioOptions() {
    const select = document.getElementById("defectScenarioSelect");
    const context = document.getElementById("defectScenarioContext");
    if (!select) return;

    try {
        const response = await fetch("/api/scenarios/active-project");
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || JSON.stringify(data));

        activeProjectScenarios = Array.isArray(data) ? data : [];
        select.innerHTML =
            '<option value="">Select a registered scenario to prefill test data...</option>' +
            activeProjectScenarios.map(scenario => {
                const jira = scenario.jira_id ? ` · ${scenario.jira_id}` : "";
                return `<option value="${scenario.id}">${escapeHtml(scenario.scenario_code)}${escapeHtml(jira)} · ${escapeHtml(scenario.http_method)} ${escapeHtml(scenario.endpoint)}</option>`;
            }).join("");

        if (context) {
            context.textContent = activeProjectScenarios.length
                ? `${activeProjectScenarios.length} scenario(s) available for the selected project.`
                : "No registered scenarios are available for the selected project.";
        }
        if (!activeProjectScenarios.length) {
            ["inputJson", "expectedJson", "actualJson"].forEach(id => {
                const field = document.getElementById(id);
                if (field) field.value = "";
            });
        }
    } catch (error) {
        activeProjectScenarios = [];
        select.innerHTML = '<option value="">Unable to load registered scenarios</option>';
        if (context) context.textContent = `Scenario load failed: ${error.message}`;
    }
}

function parseScenarioJsonOrFallback(rawValue, fallbackValue) {
    if (rawValue && String(rawValue).trim()) {
        try {
            return JSON.parse(rawValue);
        } catch (_) {
            return fallbackValue;
        }
    }
    return fallbackValue;
}

function applyDefectScenario() {
    const select = document.getElementById("defectScenarioSelect");
    if (!select?.value) {
        ["inputJson", "expectedJson", "actualJson"].forEach(id => {
            const field = document.getElementById(id);
            if (field) field.value = "";
        });
        const context = document.getElementById("defectScenarioContext");
        if (context) {
            context.textContent = "Select a scenario to reuse its endpoint and test data, or continue manually below.";
        }
        const dbEffectBox = document.getElementById("defectExpectedDbEffect");
        if (dbEffectBox) dbEffectBox.hidden = true;
        return;
    }

    const scenario = activeProjectScenarios.find(item => String(item.id) === String(select.value));
    if (!scenario) return;

    const methodSelect = document.getElementById("investigationMethod");
    const endpointSelect = document.getElementById("investigationEndpoint");
    const manualToggle = document.getElementById("defectManualMode");
    const manualRow = document.getElementById("defectManualEndpointRow");
    const selectedFlow = document.getElementById("defectSelectedFlow");
    if (manualToggle) manualToggle.checked = false;
    if (manualRow) manualRow.hidden = true;
    if (selectedFlow) selectedFlow.textContent = `${String(scenario.http_method || "").toUpperCase()} ${scenario.endpoint || ""}`;

    if (methodSelect) {
        methodSelect.value = String(scenario.http_method || "POST").toUpperCase();
        if (typeof refreshEndpointDropdown === "function") {
            refreshEndpointDropdown("investigationMethod", "investigationEndpoint");
        }
    }

    if (endpointSelect) {
        const targetEndpoint = String(scenario.endpoint || "");
        const matchingOption = Array.from(endpointSelect.options)
            .find(option => option.value === targetEndpoint);
        if (matchingOption) endpointSelect.value = targetEndpoint;
    }

    const sample = typeof scenarioSampleTestData === "function"
        ? scenarioSampleTestData(scenario.scenario_code)
        : {input: {}, expected: {}, output: {}};

    const input = parseScenarioJsonOrFallback(scenario.request_json, sample.input || {});
    const expected = parseScenarioJsonOrFallback(scenario.expected_response_json, sample.expected || {});
    const actual = sample.output || {};

    const inputField = document.getElementById("inputJson");
    const expectedField = document.getElementById("expectedJson");
    const actualField = document.getElementById("actualJson");

    if (inputField) inputField.value = JSON.stringify(input, null, 2);
    if (expectedField) expectedField.value = JSON.stringify(expected, null, 2);
    if (actualField) actualField.value = JSON.stringify(actual, null, 2);

    const context = document.getElementById("defectScenarioContext");
    if (context) {
        const jira = scenario.jira_id ? ` · JIRA ${scenario.jira_id}` : "";
        context.innerHTML =
            `<strong>${escapeHtml(scenario.scenario_code)}</strong>${escapeHtml(jira)} · ` +
            `${escapeHtml(scenario.http_method)} ${escapeHtml(scenario.endpoint)}`;
    }

    const dbEffectBox = document.getElementById("defectExpectedDbEffect");
    const dbEffectValue = document.getElementById("defectExpectedDbEffectValue");
    if (dbEffectBox && dbEffectValue) {
        const effect = String(scenario.expected_db_effect || "").trim();
        if (effect) {
            dbEffectValue.textContent = effect;
            dbEffectBox.hidden = false;
        } else {
            dbEffectBox.hidden = true;
            dbEffectValue.textContent = "";
        }
    }
}

async function refreshScenarioRegistryAndDefect() {
    await loadScenarios(1);
    if (typeof loadDefectScenarioOptions === "function") {
        await loadDefectScenarioOptions();
    }
}

let scenarioRegistryScenarioMap = new Map();

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
        scenarioRegistryScenarioMap = new Map(
            scenarios.map(item => [String(item.scenario_code || ""), item])
        );
        if (!scenarios.length) {
            container.innerHTML = `<div class="empty-project-state">No registered scenarios match the selected project.</div>`;
        } else {
            container.innerHTML = scenarios.map(scenario => `
                <div class="scenario-registry-item">
                    <div class="scenario">
                        <div class="method">${escapeHtml(scenario.http_method)}</div>
                        <div><strong>${escapeHtml(scenario.scenario_code)}</strong><div>${escapeHtml(scenario.scenario_name || "")}</div></div>
                        <div class="endpoint">${escapeHtml(scenario.endpoint)}</div>
                        <div class="scenario-row-actions">
                            <button class="scenario-test-data-button"
                                    type="button"
                                    onclick="toggleScenarioTestData('${escapeJsString(scenario.scenario_code || "")}', this)">
                                <span class="scenario-test-data-icon" aria-hidden="true">&lt;/&gt;</span>
                                <span class="scenario-test-data-text">Test Data</span>
                            </button>
                        </div>
                    </div>
                    <div class="scenario-test-data-panel"
                         data-scenario-code="${escapeHtml(scenario.scenario_code || "")}"
                         hidden></div>
                </div>
            `).join("");
        }

        renderScenarioPager(data, pager);
    } catch (error) {
        container.innerHTML = typeof renderError === "function" ? renderError(error.message) : escapeHtml(error.message);
        if (pager) pager.innerHTML = "";
    }
}


function escapeJsString(value) {
    return String(value ?? "")
        .replace(/\\/g, "\\\\")
        .replace(/'/g, "\\'")
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n");
}

function scenarioSampleTestData(scenarioCode) {
    const code = String(scenarioCode || "").toUpperCase();

    const samples = {
        STUDENT_ADD: {
            input: {
                type: "STUDENT",
                studentCode: "STU101",
                firstName: "Aarav",
                lastName: "Kumar",
                gpa: 8.2,
                emailDetails: {
                    primaryEmail: "aarav.kumar@example.com",
                    secondaryEmail: "aarav.alt@example.com"
                },
                contactDetails: {
                    primaryContact: "9876543210",
                    secondaryContact: "9123456780"
                },
                address: {
                    line1: "12 Lake View Road",
                    line2: "Bengaluru"
                }
            },
            expected: {
                type: "STUDENT",
                studentCode: "STU101",
                firstName: "Aarav",
                lastName: "Kumar",
                gpa: 8.2,
                status: "CREATED"
            },
            output: {
                id: 101,
                type: "STUDENT",
                studentCode: "STU101",
                firstName: "Aarav",
                lastName: "Kumar",
                gpa: 8.2,
                status: "CREATED"
            }
        },
        STUDENT_UPDATE: {
            input: {
                id: 101,
                type: "STUDENT",
                firstName: "Aarav",
                lastName: "Kumar",
                gpa: 8.6
            },
            expected: {
                id: 101,
                type: "STUDENT",
                gpa: 8.6,
                status: "UPDATED"
            },
            output: {
                id: 101,
                type: "STUDENT",
                gpa: 8.6,
                status: "UPDATED"
            }
        },
        STUDENT_DELETE: {
            input: { id: 101, type: "STUDENT" },
            expected: { id: 101, deleted: true },
            output: { id: 101, deleted: true }
        },
        STUDENT_ADDRESS_UPDATE: {
            input: {
                id: 101,
                type: "STUDENT",
                address: {
                    line1: "45 New Campus Road",
                    line2: "Hyderabad"
                }
            },
            expected: {
                address: {
                    line1: "45 New Campus Road",
                    line2: "Hyderabad"
                },
                status: "UPDATED"
            },
            output: {
                address: {
                    line1: "45 New Campus Road",
                    line2: "Hyderabad"
                },
                status: "UPDATED"
            }
        },
        STUDENT_CONTACT_UPDATE: {
            input: {
                id: 101,
                type: "STUDENT",
                primaryContact: "9876543210",
                secondaryContact: "9123456780"
            },
            expected: {
                primaryContact: "9876543210",
                secondaryContact: "9123456780",
                status: "UPDATED"
            },
            output: {
                primaryContact: "9876543210",
                secondaryContact: "9123456780",
                status: "UPDATED"
            }
        },
        EMPLOYEE_ADD: {
            input: {
                type: "EMPLOYEE",
                employeeCode: "EMP101",
                firstName: "Ravi",
                lastName: "Sharma",
                emailDetails: { primaryEmail: "ravi.sharma@example.com" },
                contactDetails: {
                    primaryContact: "9876501234",
                    secondaryContact: "9000012345"
                }
            },
            expected: {
                type: "EMPLOYEE",
                employeeCode: "EMP101",
                status: "CREATED"
            },
            output: {
                id: 201,
                type: "EMPLOYEE",
                employeeCode: "EMP101",
                status: "CREATED"
            }
        },
        EMPLOYEE_UPDATE: {
            input: {
                id: 201,
                type: "EMPLOYEE",
                firstName: "Ravi",
                lastName: "Sharma",
                department: "Engineering"
            },
            expected: {
                id: 201,
                department: "Engineering",
                status: "UPDATED"
            },
            output: {
                id: 201,
                department: "Engineering",
                status: "UPDATED"
            }
        },
        EMPLOYEE_DELETE: {
            input: { id: 201, type: "EMPLOYEE" },
            expected: { id: 201, deleted: true },
            output: { id: 201, deleted: true }
        },
        EMPLOYEE_EMAIL_UPDATE: {
            input: {
                id: 201,
                type: "EMPLOYEE",
                primaryEmail: "ravi.new@example.com",
                secondaryEmail: "ravi.backup@example.com"
            },
            expected: {
                primaryEmail: "ravi.new@example.com",
                secondaryEmail: "ravi.backup@example.com",
                status: "UPDATED"
            },
            output: {
                primaryEmail: "ravi.new@example.com",
                secondaryEmail: "ravi.backup@example.com",
                status: "UPDATED"
            }
        },
        EMPLOYEE_CONTACT_UPDATE: {
            input: {
                id: 201,
                type: "EMPLOYEE",
                primaryContact: "9988776655",
                secondaryContact: "8877665544"
            },
            expected: {
                primaryContact: "9988776655",
                secondaryContact: "8877665544",
                status: "UPDATED"
            },
            output: {
                primaryContact: "9988776655",
                secondaryContact: "8877665544",
                status: "UPDATED"
            }
        }
    };

    return samples[code] || {
        input: { note: "Sample input for " + (scenarioCode || "scenario") },
        expected: { status: "SUCCESS" },
        output: {
            status: "SUCCESS",
            note: "Sample output only — not executed against the application."
        }
    };
}

function formatScenarioJson(value) {
    return escapeHtml(JSON.stringify(value, null, 2));
}

function parseStoredScenarioJson(rawValue) {
    if (rawValue === null || rawValue === undefined || String(rawValue).trim() === "") {
        return null;
    }
    if (typeof rawValue === "object") return rawValue;
    try {
        return JSON.parse(String(rawValue));
    } catch (_) {
        return null;
    }
}

function scenarioDataBlock(label, value, emptyText) {
    const hasValue = value !== null && value !== undefined;
    return `
        <div class="scenario-test-data-card">
            <div class="scenario-test-data-label">${escapeHtml(label)}</div>
            ${hasValue
                ? `<pre>${formatScenarioJson(value)}</pre>`
                : `<div class="scenario-test-data-empty">${escapeHtml(emptyText || "Not available")}</div>`}
        </div>
    `;
}

async function loadGeneratedScenarioTestData(scenario) {
    const method = String(scenario?.http_method || "").toUpperCase().trim();
    const endpoint = String(scenario?.endpoint || "").trim();
    if (!method || !endpoint) return null;

    try {
        const response = await fetch(
            `/api/endpoint-flow/sample-data?http_method=${encodeURIComponent(method)}&endpoint=${encodeURIComponent(endpoint)}`
        );
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
        if (!response.ok) return null;
        return data;
    } catch (error) {
        console.warn("Could not generate endpoint test data", error);
        return null;
    }
}

async function parseStoredScenarioJson(rawValue) {
    if (rawValue === null || rawValue === undefined || String(rawValue).trim() === "") {
        return null;
    }
    if (typeof rawValue === "object") return rawValue;
    try {
        return JSON.parse(String(rawValue));
    } catch (_) {
        return null;
    }
}

function scenarioDataBlock(label, value, emptyText) {
    const hasValue = value !== null && value !== undefined;
    return `
        <div class="scenario-test-data-card">
            <div class="scenario-test-data-label">${escapeHtml(label)}</div>
            ${hasValue
                ? `<pre>${formatScenarioJson(value)}</pre>`
                : `<div class="scenario-test-data-empty">${escapeHtml(emptyText || "Not available")}</div>`}
        </div>
    `;
}

async function loadGeneratedScenarioTestData(scenario) {
    const method = String(scenario?.http_method || "").toUpperCase().trim();
    const endpoint = String(scenario?.endpoint || "").trim();
    if (!method || !endpoint) return null;

    try {
        const response = await fetch(
            `/api/endpoint-flow/sample-data?http_method=${encodeURIComponent(method)}&endpoint=${encodeURIComponent(endpoint)}`
        );
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
        if (!response.ok) return null;
        return data;
    } catch (error) {
        console.warn("Could not generate endpoint test data", error);
        return null;
    }
}

async function toggleScenarioTestData(scenarioCode, button) {
    const item = button?.closest(".scenario-registry-item");
    const panel = item?.querySelector(".scenario-test-data-panel");
    if (!panel) return;

    const opening = panel.hidden;
    if (!opening) {
        panel.hidden = true;
        const label = button.querySelector(".scenario-test-data-text");
        if (label) label.textContent = "Test Data";
        button.classList.remove("active");
        return;
    }

    const scenario = scenarioRegistryScenarioMap.get(String(scenarioCode || ""));
    if (!scenario) {
        panel.innerHTML = `<div class="scenario-test-data-empty">Scenario data is not available for the selected project.</div>`;
        panel.hidden = false;
        return;
    }

    button.disabled = true;
    const label = button.querySelector(".scenario-test-data-text");
    if (label) label.textContent = "Loading...";

    // Scenario Registry test data is intentionally independent from Main/Test
    // Baselines. A baseline capture must never replace these values.
    const sample = scenarioSampleTestData(scenario.scenario_code);

    function meaningful(value) {
        if (value === null || value === undefined) return false;
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === "object") return Object.keys(value).length > 0;
        return String(value).trim() !== "";
    }

    const storedInput = parseStoredScenarioJson(scenario.request_json);
    const storedExpected = parseStoredScenarioJson(scenario.expected_response_json);
    const storedActual = parseStoredScenarioJson(
        scenario.actual_response_json ?? scenario.actual_json ?? scenario.output_json ?? null
    );

    let generated = null;
    if (!meaningful(storedInput) || !meaningful(storedExpected)) {
        generated = await loadGeneratedScenarioTestData(scenario);
    }

    // Priority: useful saved scenario data -> useful generated endpoint data ->
    // the original Scenario Registry sample data. Empty {} values do not hide
    // the useful sample data anymore.
    const generatedInput = generated?.request_json;
    const generatedExpected = generated?.expected_response_json;

    const input = meaningful(storedInput)
        ? storedInput
        : (meaningful(generatedInput) ? generatedInput : sample.input);

    const expected = meaningful(storedExpected)
        ? storedExpected
        : (meaningful(generatedExpected) ? generatedExpected : sample.expected);

    const actual = meaningful(storedActual) ? storedActual : sample.output;

    const hasUsefulStored = meaningful(storedInput) || meaningful(storedExpected) || meaningful(storedActual);
    const hasUsefulGenerated = meaningful(generatedInput) || meaningful(generatedExpected);
    const sourceText = hasUsefulStored
        ? "Saved scenario data"
        : (hasUsefulGenerated ? "Generated from the selected endpoint model" : "Scenario Registry test data");

    panel.innerHTML = `
        <div class="scenario-test-data-header">
            <div>
                <strong>Test Data</strong>
                <span>${escapeHtml(sourceText)}</span>
            </div>
        </div>
        <div class="scenario-test-data-grid">
            ${scenarioDataBlock("INPUT", input, "No request body for this scenario")}
            ${scenarioDataBlock("EXPECTED", expected, "No expected response saved")}
            ${scenarioDataBlock("ACTUAL", actual, "Not executed yet")}
        </div>
    `;

    panel.hidden = false;
    if (label) label.textContent = "Hide Data";
    button.classList.add("active");
    button.disabled = false;
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

let scenarioCoveredOperationKeys = new Set();

function scenarioOperationKey(method, endpoint) {
    return `${String(method || "").toUpperCase().trim()} ${String(endpoint || "").trim()}`;
}

async function openNewScenarioModal() {
    const modal = document.getElementById("newScenarioModal");
    if (!modal) return;

    ["newScenarioCode", "newScenarioName", "newScenarioJiraId", "newScenarioDescription",
     "newScenarioRequestJson", "newScenarioExpectedResponse", "newScenarioExpectedDbEffect"].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.value = "";
    });

    const error = document.getElementById("newScenarioError");
    if (error) error.textContent = "";

    const details = document.getElementById("newScenarioDetails");
    if (details) details.classList.add("hidden");

    const registerButton = document.getElementById("registerScenarioButton");
    if (registerButton) registerButton.disabled = true;

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");

    await loadAvailableScenarioOperations();
}

function closeNewScenarioModal() {
    const modal = document.getElementById("newScenarioModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
}

async function loadAvailableScenarioOperations() {
    const methodSelect = document.getElementById("newScenarioMethod");
    const endpointSelect = document.getElementById("newScenarioEndpoint");
    const status = document.getElementById("existingOperationScenario");
    const registerButton = document.getElementById("registerScenarioButton");

    if (!methodSelect || !endpointSelect) return;

    scenarioCoveredOperationKeys = new Set();

    try {
        // One scenario is allowed per project + method + endpoint.
        // Therefore registration only presents operations not already represented.
        const response = await fetch("/api/scenarios/active-project");
        const scenarios = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(scenarios));

        (Array.isArray(scenarios) ? scenarios : []).forEach(item => {
            scenarioCoveredOperationKeys.add(
                scenarioOperationKey(item.http_method, item.endpoint)
            );
        });

        const available = (Array.isArray(discoveredEndpoints) ? discoveredEndpoints : [])
            .map(item => ({
                method: String(item.http_method || item.method || "").toUpperCase().trim(),
                endpoint: String(item.endpoint || item.path || "").trim()
            }))
            .filter(item => item.method && item.endpoint)
            .filter((item, index, values) =>
                values.findIndex(other =>
                    other.method === item.method && other.endpoint === item.endpoint
                ) === index
            )
            .filter(item =>
                !scenarioCoveredOperationKeys.has(
                    scenarioOperationKey(item.method, item.endpoint)
                )
            );

        const methods = [...new Set(available.map(item => item.method))].sort();
        methodSelect.innerHTML = methods.length
            ? methods.map(method => `<option value="${escapeHtml(method)}">${escapeHtml(method)}</option>`).join("")
            : '<option value="">No uncaptured operations</option>';

        if (!methods.length) {
            endpointSelect.innerHTML = '<option value="">All discovered operations already have scenarios</option>';
            if (status) {
                status.className = "scenario-operation-check existing";
                status.innerHTML = "<strong>All discovered operations are already represented.</strong> Open Scenario Baselines and add a new testing baseline to the existing operation instead of creating another scenario.";
            }
            if (registerButton) registerButton.disabled = true;
            return;
        }

        refreshNewScenarioEndpoints();
    } catch (error) {
        methodSelect.innerHTML = '<option value="">Unable to load operations</option>';
        endpointSelect.innerHTML = '<option value="">Unable to load operations</option>';
        if (status) {
            status.className = "scenario-operation-check error";
            status.textContent = error.message || "Could not load available operations.";
        }
        if (registerButton) registerButton.disabled = true;
    }
}

function refreshNewScenarioEndpoints() {
    /* dynamic-test-data-clear */
    ["newScenarioRequestJson", "newScenarioExpectedResponse", "newScenarioExpectedDbEffect"].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = "";
    });
    const method = (document.getElementById("newScenarioMethod")?.value || "").toUpperCase();
    const select = document.getElementById("newScenarioEndpoint");
    const status = document.getElementById("existingOperationScenario");
    const details = document.getElementById("newScenarioDetails");
    const button = document.getElementById("registerScenarioButton");
    if (!select) return;

    const matching = (Array.isArray(discoveredEndpoints) ? discoveredEndpoints : [])
        .filter(item => String(item.http_method || item.method || "").toUpperCase() === method)
        .map(item => String(item.endpoint || item.path || "").trim())
        .filter(Boolean)
        .filter(endpoint =>
            !scenarioCoveredOperationKeys.has(scenarioOperationKey(method, endpoint))
        )
        .filter((value, index, values) => values.indexOf(value) === index)
        .sort();

    select.innerHTML = '<option value="">Select an available endpoint...</option>' +
        matching.map(endpoint => `<option value="${escapeHtml(endpoint)}">${escapeHtml(endpoint)}</option>`).join("");

    if (!matching.length) {
        select.innerHTML = `<option value="">No uncaptured ${escapeHtml(method)} operations</option>`;
    }

    if (details) details.classList.add("hidden");
    if (status) {
        status.className = "scenario-operation-check";
        status.innerHTML = matching.length
            ? `Select an endpoint. Only ${escapeHtml(method)} operations without an existing scenario are listed.`
            : `All discovered ${escapeHtml(method)} operations already have scenarios.`;
    }
    if (button) button.disabled = true;
}

function suggestScenarioIdentity(method, endpoint) {
    const codeInput = document.getElementById("newScenarioCode");
    const nameInput = document.getElementById("newScenarioName");
    if (!codeInput || !nameInput) return;

    const words = String(endpoint || "")
        .replace(/\{[^}]+\}/g, "")
        .split("/")
        .filter(Boolean)
        .flatMap(part => part.split(/[-_]/g))
        .filter(Boolean);

    let suffix = words.slice(-2).join("_").toUpperCase();
    if (!suffix) suffix = "OPERATION";

    if (!codeInput.value.trim()) {
        codeInput.value = `${suffix}_${String(method || "").toUpperCase()}`;
    }
    if (!nameInput.value.trim()) {
        const title = words.slice(-2)
            .map(word => word.charAt(0).toUpperCase() + word.slice(1))
            .join(" ");
        nameInput.value = `${title || "Operation"} ${String(method || "").toUpperCase()}`;
    }
}

async function populateScenarioTestData() {
    const method = (document.getElementById("newScenarioMethod")?.value || "").trim().toUpperCase();
    const endpoint = (document.getElementById("newScenarioEndpoint")?.value || "").trim();
    if (!method || !endpoint) return;
    const req = document.getElementById("newScenarioRequestJson");
    const exp = document.getElementById("newScenarioExpectedResponse");
    const db = document.getElementById("newScenarioExpectedDbEffect");
    try {
        const r = await fetch(`/api/endpoint-flow/sample-data?http_method=${encodeURIComponent(method)}&endpoint=${encodeURIComponent(endpoint)}`);
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        if (req) req.value = data.request_json == null ? "" : JSON.stringify(data.request_json, null, 2);
        if (exp) exp.value = data.expected_response_json == null ? "" : JSON.stringify(data.expected_response_json, null, 2);
        if (db) db.value = data.expected_db_effect || "";
    } catch (e) {
        console.error("Could not generate scenario test data", e);
    }
}

async function checkExistingOperationScenario() {
    const method = (document.getElementById("newScenarioMethod")?.value || "").toUpperCase();
    const endpoint = (document.getElementById("newScenarioEndpoint")?.value || "").trim();
    const status = document.getElementById("existingOperationScenario");
    const button = document.getElementById("registerScenarioButton");
    const details = document.getElementById("newScenarioDetails");
    if (!status || !button) return;

    if (!method || !endpoint) {
        status.className = "scenario-operation-check";
        status.innerHTML = "Select an available operation.";
        button.disabled = true;
        if (details) details.classList.add("hidden");
        return;
    }

    const key = scenarioOperationKey(method, endpoint);
    if (scenarioCoveredOperationKeys.has(key)) {
        status.className = "scenario-operation-check existing";
        status.innerHTML = "<strong>This operation already has a scenario.</strong> Open its Scenario Baseline and add another testing baseline instead.";
        button.disabled = true;
        if (details) details.classList.add("hidden");
        return;
    }

    // Backend re-check prevents stale UI/project-switch races.
    status.className = "scenario-operation-check checking";
    status.innerHTML = `Checking ${escapeHtml(method)} ${escapeHtml(endpoint)}...`;
    button.disabled = true;

    try {
        const response = await fetch(
            `/api/scenarios/by-operation?http_method=${encodeURIComponent(method)}&endpoint=${encodeURIComponent(endpoint)}`
        );
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));

        if (Array.isArray(data) && data.length) {
            scenarioCoveredOperationKeys.add(key);
            status.className = "scenario-operation-check existing";
            status.innerHTML = `<strong>${escapeHtml(method)} ${escapeHtml(endpoint)} already has a scenario.</strong> It has been removed from the list.`;
            if (details) details.classList.add("hidden");
            button.disabled = true;
            refreshNewScenarioEndpoints();
            return;
        }

        status.className = "scenario-operation-check available";
        status.innerHTML = `<strong>${escapeHtml(method)} ${escapeHtml(endpoint)} is available.</strong> Enter the scenario details below.`;
        if (details) details.classList.remove("hidden");
        suggestScenarioIdentity(method, endpoint);
        button.disabled = false;
        await populateScenarioTestData();
    } catch (e) {
        status.className = "scenario-operation-check error";
        status.textContent = e.message || "Could not check existing scenarios.";
        if (details) details.classList.add("hidden");
        button.disabled = true;
    }
}

function openExistingOperationScenario(scenarioId) {
    closeNewScenarioModal();
    if (typeof selectBaselineScenario === "function") {
        selectBaselineScenario(scenarioId);
    }
    const select = document.getElementById("baselineScenarioSelect");
    if (select) {
        select.value = String(scenarioId);
        if (typeof loadBaselineHistory === "function") loadBaselineHistory();
    }
    const baselineCard = Array.from(document.querySelectorAll(".accordion-card"))
        .find(card => card.querySelector('[data-accordion-title="Scenario Baselines"]'));
    if (baselineCard) {
        baselineCard.classList.remove("is-collapsed");
        baselineCard.scrollIntoView({behavior: "smooth", block: "start"});
    }
}

async function registerNewScenario() {
    const code = (document.getElementById("newScenarioCode")?.value || "").trim().toUpperCase();
    const name = (document.getElementById("newScenarioName")?.value || "").trim();
    const description = (document.getElementById("newScenarioDescription")?.value || "").trim();
    const jiraId = (document.getElementById("newScenarioJiraId")?.value || "").trim().toUpperCase();
    const requestJson = (document.getElementById("newScenarioRequestJson")?.value || "").trim();
    const expectedResponseJson = (document.getElementById("newScenarioExpectedResponse")?.value || "").trim();
    const expectedDbEffect = (document.getElementById("newScenarioExpectedDbEffect")?.value || "").trim();
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

    for (const [label, value] of [["Request JSON", requestJson], ["Expected Response JSON", expectedResponseJson]]) {
        if (value) {
            try { JSON.parse(value); }
            catch (_) {
                if (error) error.textContent = `${label} must contain valid JSON.`;
                return;
            }
        }
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
                jira_id: jiraId || null,
                request_json: requestJson || null,
                expected_response_json: expectedResponseJson || null,
                expected_db_effect: expectedDbEffect || null,
                status: "ACTIVE"
            })
        });
        const responseText = await response.text();
        let data = {};
        try {
            data = responseText ? JSON.parse(responseText) : {};
        } catch (_) {
            data = {detail: responseText || `HTTP ${response.status}`};
        }
        if (!response.ok) {
            throw new Error((typeof data.detail === "object" ? data.detail.message : data.detail) || `Scenario registration failed (HTTP ${response.status})`);
        }

        closeNewScenarioModal();
        scenarioPage = 1;
        await loadScenarios(1);

        // Defect Investigation is scenario-driven. Refresh its dropdown
        // immediately so a newly registered scenario is selectable without
        // reloading the browser.
        if (typeof loadDefectScenarioOptions === "function") {
            await loadDefectScenarioOptions();
        }

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

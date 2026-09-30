let discoveredEndpoints = [];
let baselineOverviewData = [];


document.addEventListener(
    "DOMContentLoaded",
    async () => {

        checkHealth();
        loadScenarios();
        loadBaselineOverview();

        // Endpoint Flow UI was intentionally removed. Guard every optional
        // control so a missing section never stops the rest of the page
        // (especially Scenario Flowchart) from loading its endpoints.
        const flowMethod = document.getElementById("flowMethod");
        if (flowMethod) {
            flowMethod.addEventListener(
                "change",
                () => refreshEndpointDropdown("flowMethod", "flowEndpoint")
            );
        }

        const chartMethod = document.getElementById("chartMethod");
        if (chartMethod) {
            chartMethod.addEventListener(
                "change",
                () => refreshEndpointDropdown("chartMethod", "chartEndpoint")
            );
        }

        const investigationMethod = document.getElementById("investigationMethod");
        if (investigationMethod) {
            investigationMethod.addEventListener(
                "change",
                () => refreshEndpointDropdown("investigationMethod", "investigationEndpoint")
            );
        }

        await loadProjectEndpoints();
    }
);


async function loadProjectEndpoints() {

    try {

        const response =
            await fetch(
                "/api/endpoint-flow/endpoints"
            );

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                JSON.stringify(data)
            );
        }

        if (Array.isArray(data)) {

            discoveredEndpoints =
                data;

        } else if (
            Array.isArray(
                data.endpoints
            )
        ) {

            discoveredEndpoints =
                data.endpoints;

        } else {

            discoveredEndpoints =
                [];
        }

        refreshEndpointDropdown(
            "flowMethod",
            "flowEndpoint"
        );

        refreshEndpointDropdown(
            "chartMethod",
            "chartEndpoint"
        );

        refreshEndpointDropdown(
            "investigationMethod",
            "investigationEndpoint"
        );

    } catch (error) {

        console.error(
            "Failed to load endpoints",
            error
        );

        showEndpointLoadError(
            "flowEndpoint"
        );

        showEndpointLoadError(
            "chartEndpoint"
        );

        showEndpointLoadError(
            "investigationEndpoint"
        );
    }
}


function refreshEndpointDropdown(
    methodElementId,
    endpointElementId
) {

    const methodElement =
        document.getElementById(
            methodElementId
        );

    const endpointSelect =
        document.getElementById(
            endpointElementId
        );

    if (
        !methodElement ||
        !endpointSelect
    ) {
        return;
    }

    const selectedMethod =
        String(
            methodElement.value
        ).toUpperCase();

    const matchingEndpoints =
        discoveredEndpoints
            .filter(
                item => {

                    const method =
                        String(
                            item.http_method
                            ||
                            item.httpMethod
                            ||
                            item.method
                            ||
                            item.request_method
                            ||
                            ""
                        ).toUpperCase();

                    return (
                        method ===
                        selectedMethod
                    );
                }
            )
            .sort(
                (
                    first,
                    second
                ) => {

                    return getEndpointPath(
                        first
                    ).localeCompare(
                        getEndpointPath(
                            second
                        )
                    );
                }
            );

    endpointSelect.innerHTML =
        "";

    if (
        matchingEndpoints.length ===
        0
    ) {

        const option =
            document.createElement(
                "option"
            );

        option.value =
            "";

        option.textContent =
            "No "
            + selectedMethod
            + " endpoints found";

        endpointSelect.appendChild(
            option
        );

        return;
    }

    for (
        const item
        of matchingEndpoints
    ) {

        const endpoint =
            getEndpointPath(
                item
            );

        if (!endpoint) {
            continue;
        }

        const controller =
            item.controller
            || {};

        const controllerClass =
            item.controller_class
            ||
            item.controllerClass
            ||
            item.class_name
            ||
            controller.class_name
            ||
            controller.className
            ||
            "";

        const controllerMethod =
            item.controller_method
            ||
            item.controllerMethod
            ||
            item.method_name
            ||
            controller.method_name
            ||
            controller.methodName
            ||
            "";

        const option =
            document.createElement(
                "option"
            );

        option.value =
            endpoint;

        if (
            controllerClass &&
            controllerMethod
        ) {

            option.textContent =
                endpoint
                + " — "
                + controllerClass
                + "."
                + controllerMethod;

        } else {

            option.textContent =
                endpoint;
        }

        endpointSelect.appendChild(
            option
        );
    }
}


function getEndpointPath(
    item
) {

    return (
        item.endpoint
        ||
        item.path
        ||
        item.url
        ||
        item.request_path
        ||
        ""
    );
}


function showEndpointLoadError(
    endpointElementId
) {

    const endpointSelect =
        document.getElementById(
            endpointElementId
        );

    if (!endpointSelect) {
        return;
    }

    endpointSelect.innerHTML =
        "";

    const option =
        document.createElement(
            "option"
        );

    option.value =
        "";

    option.textContent =
        "Unable to load endpoints";

    endpointSelect.appendChild(
        option
    );
}


async function checkHealth() {

    const element =
        document.getElementById(
            "healthStatus"
        );

    try {

        const response =
            await fetch(
                "/health"
            );

        if (!response.ok) {
            throw new Error(
                "Health check failed"
            );
        }

        const data =
            await response.json();

        const initialization =
            data.project_initialization;

        if (
            initialization
            && initialization.status === "READY"
        ) {
            const javaFiles =
                initialization.scan
                    ? initialization.scan.total_java_files
                    : 0;

            element.textContent =
                `Ready · ${javaFiles} Java files · RAG indexed`;

            element.className =
                "status-badge status-success";

        } else if (
            initialization
            && initialization.status === "ERROR"
        ) {
            element.textContent =
                "Backend Online · Project initialization failed";

            element.className =
                "status-badge status-error";

        } else {
            element.textContent =
                "Backend Online";

            element.className =
                "status-badge status-success";
        }

    } catch (error) {

        element.textContent =
            "Backend Offline";

        element.className =
            "status-badge status-error";
    }
}


async function loadScenarios() {

    const container =
        document.getElementById(
            "scenarioList"
        );

    container.innerHTML =
        "Loading...";

    try {

        const response =
            await fetch(
                "/api/scenarios"
            );

        const scenarios =
            await response.json();

        if (!response.ok) {

            throw new Error(
                JSON.stringify(
                    scenarios
                )
            );
        }

        if (
            !Array.isArray(
                scenarios
            )
            ||
            scenarios.length === 0
        ) {

            container.innerHTML =
                "No scenarios found.";

            return;
        }

        container.innerHTML =
            scenarios
                .map(
                    scenario => `
                        <div class="scenario">

                            <div class="method">

                                ${escapeHtml(
                                    scenario.http_method
                                )}

                            </div>

                            <div>

                                <strong>

                                    ${escapeHtml(
                                        scenario.scenario_code
                                    )}

                                </strong>

                                <div>

                                    ${escapeHtml(
                                        scenario.scenario_name
                                        || ""
                                    )}

                                </div>

                            </div>

                            <div class="endpoint">

                                ${escapeHtml(
                                    scenario.endpoint
                                )}

                            </div>

                            <div>

                                <span class="tag">

                                    ${escapeHtml(
                                        scenario.status
                                        || "ACTIVE"
                                    )}

                                </span>

                            </div>

                        </div>
                    `
                )
                .join("");

    } catch (error) {

        container.innerHTML =
            renderError(
                error.message
            );
    }
}


async function analyseEndpointFlow() {

    const method =
        document.getElementById(
            "flowMethod"
        ).value;

    const endpoint =
        document.getElementById(
            "flowEndpoint"
        ).value;

    const container =
        document.getElementById(
            "flowResult"
        );

    if (!endpoint) {

        container.innerHTML =
            renderError(
                "Please select an endpoint."
            );

        return;
    }

    container.innerHTML =
        "Analysing...";

    try {

        const url =
            "/api/endpoint-flow/analyze"
            + "?http_method="
            + encodeURIComponent(
                method
            )
            + "&endpoint="
            + encodeURIComponent(
                endpoint
            );

        const response =
            await fetch(
                url
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                JSON.stringify(
                    data
                )
            );
        }

        const flow =
            data.simplified_flow
            || [];

        if (
            flow.length === 0
        ) {

            container.innerHTML =
                "<pre>"
                +
                escapeHtml(
                    JSON.stringify(
                        data,
                        null,
                        2
                    )
                )
                +
                "</pre>";

            return;
        }

        const compactClass = flow.length <= 4 ? "flow-list compact-flow" : "flow-list";
        const arrowSymbol = flow.length <= 4 ? "→" : "↓";

        container.innerHTML =
            `
            <div class="flow-summary-bar">
                <span><strong>${flow.length}</strong> execution step${flow.length === 1 ? "" : "s"}</span>
                <span class="muted-text">${escapeHtml(method)} ${escapeHtml(endpoint)}</span>
            </div>
            <div class="${compactClass}">
                ${flow.map((step, index) => {
                    const arrow = index === flow.length - 1 ? "" : `<div class="flow-arrow">${arrowSymbol}</div>`;
                    return `<div class="flow-step">${escapeHtml(step)}</div>${arrow}`;
                }).join("")}
            </div>
            `;

    } catch (error) {

        container.innerHTML =
            renderError(
                error.message
            );
    }
}


async function runInvestigation() {

    const method =
        document.getElementById(
            "investigationMethod"
        ).value;

    const endpoint =
        document.getElementById(
            "investigationEndpoint"
        ).value;

    const container =
        document.getElementById(
            "investigationResult"
        );

    if (!endpoint) {

        container.innerHTML =
            renderError(
                "Please select an endpoint."
            );

        return;
    }

    let input;
    let expected;
    let actual;

    try {

        input =
            JSON.parse(
                document.getElementById(
                    "inputJson"
                ).value
            );

        expected =
            JSON.parse(
                document.getElementById(
                    "expectedJson"
                ).value
            );

        actual =
            JSON.parse(
                document.getElementById(
                    "actualJson"
                ).value
            );

    } catch (error) {

        container.innerHTML =
            renderError(
                "Input, Expected or Actual JSON is invalid."
            );

        return;
    }

    container.innerHTML =
        "Running LangGraph investigation...";

    try {

        const url =
            "/api/investigation/analyse"
            + "?http_method="
            + encodeURIComponent(
                method
            )
            + "&endpoint="
            + encodeURIComponent(
                endpoint
            );

        const response =
            await fetch(
                url,
                {
                    method:
                        "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                input:
                                    input,

                                expected:
                                    expected,

                                actual:
                                    actual
                            }
                        )
                }
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                JSON.stringify(
                    data
                )
            );
        }

        container.innerHTML =
            renderInvestigation(
                data
            );

    } catch (error) {

        container.innerHTML =
            renderError(
                error.message
            );
    }
}


function renderInvestigation(data) {
    const status = data.status || "UNKNOWN";
    const differences = data.differences || [];
    const investigations = data.investigations || [];
    const attributes = data.affected_attributes || [];
    const input = data.input || {};

    window.currentDefectTraces = {};

    let html = `
        <div class="investigation-summary">
            <div class="metric-card">
                <span class="metric-label">Status</span>
                <strong>${escapeHtml(status)}</strong>
            </div>
            <div class="metric-card">
                <span class="metric-label">Differences</span>
                <strong>${differences.length}</strong>
            </div>
            <div class="metric-card">
                <span class="metric-label">Attributes</span>
                <strong>${attributes.length}</strong>
            </div>
        </div>
    `;

    if (differences.length === 0) {
        html += `<div class="success"><strong>No defect found.</strong> Expected and actual responses match.</div>`;
    } else {
        html += `<h3>What is different?</h3><div class="difference-grid">`;
        for (const difference of differences) {
            html += `
                <div class="difference-card">
                    <div class="difference-title">${escapeHtml(difference.path || difference.attribute || "Difference")}</div>
                    <div class="difference-values three-values">
                        <div><span>Input</span><strong>${escapeHtml(formatValue(findInputValue(input, difference.path || difference.attribute)))}</strong></div>
                        <div><span>Expected</span><strong>${escapeHtml(formatValue(difference.expected))}</strong></div>
                        <div><span>Actual</span><strong>${escapeHtml(formatValue(difference.actual))}</strong></div>
                    </div>
                    <span class="tag">${escapeHtml(difference.difference_type || "MISMATCH")}</span>
                </div>
            `;
        }
        html += `</div>`;
    }

    if (investigations.length > 0) {
        html += `<h3>Where did each value go wrong?</h3>`;
        html += `<p class="muted-text">Each affected attribute gets its own compact horizontal code-flow. Highlighted steps are static-analysis locations that directly touch the attribute and are the strongest likely modification points.</p>`;
    }

    for (const investigation of investigations) {
        const locations = investigation.likely_code_locations || [];
        const trace = investigation.trace?.trace || [];
        const conclusion = investigation.conclusion || null;
        const likelyDivergence = investigation.likely_divergence || conclusion?.likely_divergence || null;
        const difference = (investigation.differences || [])[0] || differences.find(d =>
            d.attribute === investigation.attribute ||
            String(d.path || "").endsWith("." + investigation.attribute) ||
            d.path === investigation.attribute
        ) || {};
        const inputValue = findInputValue(input, difference.path || investigation.attribute);
        const expectedValue = difference.expected;
        const actualValue = difference.actual;
        const key = String(investigation.attribute || `attribute-${Object.keys(window.currentDefectTraces).length}`);

        window.currentDefectTraces[key] = {
            attribute: key,
            trace,
            locations,
            inputValue,
            expectedValue,
            actualValue,
            conclusion,
            likelyDivergence
        };

        html += `
            <div class="investigation-attribute-card defect-flow-card">
                <div class="attribute-card-header">
                    <div>
                        <span class="muted-text">Attribute flow</span>
                        <h3>${escapeHtml(key)}</h3>
                    </div>
                    <div class="attribute-actions">
                        <span class="tag">${conclusion ? "1 likely root cause" : `${locations.length} likely modification point${locations.length === 1 ? "" : "s"}`}</span>
                        <button type="button" class="secondary-button" onclick="openDefectTraceModal('${escapeJsString(key)}')">Enlarge</button>
                    </div>
                </div>
                ${renderHorizontalDefectTrace(trace, locations, inputValue, expectedValue, actualValue, false, conclusion, likelyDivergence)}
        `;

        if (locations.length > 0 || conclusion) {
            html += `<details class="technical-details" open><summary>Likely Root Cause &amp; Code Locations</summary><div class="likely-location-list">`;

            if (conclusion) {
                const rootCauseName = conclusion.likely_divergence || likelyDivergence || "";
                const rootCauseLine = conclusion.line_number;
                const rootCauseCode = conclusion.code || "";
                const rootCauseClass = String(rootCauseName).includes(".")
                    ? String(rootCauseName).split(".").slice(0, -1).join(".")
                    : "";
                const rootCauseMethod = String(rootCauseName).includes(".")
                    ? String(rootCauseName).split(".").pop()
                    : String(rootCauseName);

                html += `
                    <div class="likely-location root-cause-location">
                        <div class="root-cause-main">
                            <div class="root-cause-heading">
                                <strong>${escapeHtml(rootCauseName || "Likely root cause")}</strong>
                                <span class="root-cause-badge">ROOT CAUSE</span>
                                <span class="root-cause-confidence">${escapeHtml(conclusion.confidence || "")} CONFIDENCE</span>
                                <span class="root-cause-classification">${escapeHtml(conclusion.classification || "")}</span>
                            </div>
                            ${rootCauseCode ? `<code>${escapeHtml(rootCauseCode)}</code>` : ""}
                            ${conclusion.summary ? `<div class="root-cause-summary">${escapeHtml(conclusion.summary)}</div>` : ""}
                        </div>
                        <span class="root-cause-line">${rootCauseLine ? `line ${rootCauseLine}` : ""}</span>
                    </div>
                `;

                const supportingLocations = locations.filter(location => {
                    const locationName = `${location.class_name || ""}.${location.method_name || ""}`;
                    if (rootCauseName && locationName === rootCauseName) return false;
                    if (rootCauseClass && rootCauseMethod &&
                        location.class_name === rootCauseClass &&
                        location.method_name === rootCauseMethod) return false;
                    return true;
                });

                if (supportingLocations.length > 0) {
                    html += `<div class="supporting-evidence-label">Supporting code locations</div>`;
                    supportingLocations.slice(0, 8).forEach(location => {
                        html += `
                            <div class="likely-location supporting-location">
                                <strong>${escapeHtml(location.class_name || "")}.${escapeHtml(location.method_name || "")}</strong>
                                <span>${location.line_number ? `line ${location.line_number}` : ""}${location.usage_type ? ` · ${escapeHtml(location.usage_type)}` : ""}</span>
                            </div>
                        `;
                    });
                }
            } else {
                locations.slice(0, 8).forEach(location => {
                    html += `
                        <div class="likely-location">
                            <strong>${escapeHtml(location.class_name || "")}.${escapeHtml(location.method_name || "")}</strong>
                            <span>${location.line_number ? `line ${location.line_number}` : ""}${location.usage_type ? ` · ${escapeHtml(location.usage_type)}` : ""}</span>
                        </div>
                    `;
                });
            }

            html += `</div></details>`;
        }

        html += `</div>`;
    }

    html += `
        <details class="technical-details">
            <summary>Technical details / raw investigation JSON</summary>
            <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
        </details>
    `;
    return html;
}

function renderHorizontalDefectTrace(trace, locations, inputValue, expectedValue, actualValue, largeView, conclusion = null, likelyDivergence = null) {
    const normalize = value => String(value || "")
        .replace(/\s+/g, "")
        .replace(/[()]/g, "")
        .toLowerCase();

    const rootCauseName = String(conclusion?.likely_divergence || likelyDivergence || "");
    const normalizedRootCause = normalize(rootCauseName);

    const likely = (locations || []).map(location => ({
        className: normalize(location.class_name),
        methodName: normalize(location.method_name),
        key: normalize(`${location.class_name || ""}.${location.method_name || ""}`)
    }));

    const prepared = (trace || []).map(step => {
        const className = String(step.class_name || "");
        const methodName = String(step.method_name || "");
        const label = String(step.label || `${className}.${methodName}` || "Flow step");
        const normalizedLabel = normalize(label);
        const normalizedClass = normalize(className);
        const normalizedMethod = normalize(methodName);
        const normalizedKey = normalize(`${className}.${methodName}`);

        const locationMatch = likely.some(location => {
            if (location.key && normalizedKey === location.key) return true;
            if (location.className && normalizedClass === location.className) {
                return !location.methodName || !normalizedMethod || normalizedMethod === location.methodName;
            }
            if (location.className && normalizedLabel.includes(location.className)) {
                return !location.methodName || normalizedLabel.includes(location.methodName);
            }
            return false;
        });

        const rootCauseMatch = Boolean(normalizedRootCause) && (
            normalizedKey === normalizedRootCause ||
            normalizedLabel.includes(normalizedRootCause) ||
            (
                normalizedRootCause.includes(normalizedClass) &&
                (!normalizedMethod || normalizedRootCause.includes(normalizedMethod))
            )
        );

        return {
            ...step,
            class_name: className,
            method_name: methodName,
            label,
            suspect: conclusion ? rootCauseMatch : (Boolean(step.direct_attribute_touch) || locationMatch),
            supporting_touch: conclusion ? (Boolean(step.direct_attribute_touch) || locationMatch) && !rootCauseMatch : false
        };
    });

    // Prefer implementation classes over their immediately adjacent interface step.
    const compact = [];
    for (let i = 0; i < prepared.length; i++) {
        const current = prepared[i];
        const next = prepared[i + 1];
        const currentClass = normalize(current.class_name);
        const nextClass = normalize(next?.class_name);

        if (next && nextClass === `${currentClass}impl`) {
            continue;
        }
        compact.push(current);
    }

    // Keep the relevant branch only. This prevents an Employee defect flow
    // from continuing into StudentService (or another unrelated branch).
    const suspectIndexes = compact
        .map((step, index) => step.suspect ? index : -1)
        .filter(index => index >= 0);

    let focused = compact;
    if (suspectIndexes.length > 0) {
        const firstSuspect = suspectIndexes[0];
        const lastSuspect = suspectIndexes[suspectIndexes.length - 1];

        let start = Math.max(0, firstSuspect - 3);
        // Preserve a controller at the beginning when one is available.
        const controllerIndex = compact.findIndex(step => /controller/i.test(step.class_name || step.label || ""));
        if (controllerIndex >= 0 && controllerIndex < firstSuspect) start = controllerIndex;

        let end = Math.min(compact.length - 1, lastSuspect + 2);
        // Stop at the first repository after the suspect; that is normally
        // the end of this business branch.
        for (let i = lastSuspect + 1; i < compact.length; i++) {
            if (/repository/i.test(compact[i].class_name || compact[i].label || "")) {
                end = i;
                break;
            }
        }
        focused = compact.slice(start, end + 1);
    }

    const nodes = [];
    nodes.push(`
        <div class="defect-flow-node value-node input-node">
            <span>Input</span>
            <strong>${escapeHtml(formatValue(inputValue))}</strong>
        </div>
    `);

    focused.forEach(step => {
        const label = step.label || `${step.class_name || ""}.${step.method_name || ""}` || "Flow step";
        nodes.push(`
            <div class="defect-flow-node code-node ${step.suspect ? "suspect-node root-cause-node" : (step.supporting_touch ? "supporting-touch-node" : "")}">
                <span>${step.suspect ? "Likely root cause" : (step.supporting_touch ? "Supporting evidence" : "Code flow")}</span>
                <strong>${escapeHtml(label)}</strong>
                ${step.suspect ? '<b class="suspect-alert" aria-label="Likely issue">!</b>' : ''}
            </div>
        `);
    });

    nodes.push(`
        <div class="defect-flow-node value-node expected-node">
            <span>Expected</span>
            <strong>${escapeHtml(formatValue(expectedValue))}</strong>
        </div>
    `);
    nodes.push(`
        <div class="defect-flow-node value-node actual-node">
            <span>Actual</span>
            <strong>${escapeHtml(formatValue(actualValue))}</strong>
        </div>
    `);

    return `
        <div class="horizontal-defect-flow ${largeView ? "large-defect-flow" : ""}">
            ${nodes.map((node, index) =>
                `${node}${index < nodes.length - 1 ? '<div class="defect-flow-arrow">→</div>' : ''}`
            ).join("")}
        </div>
    `;
}

function findInputValue(input, path) {
    if (input === null || input === undefined) return undefined;
    const rawPath = String(path || "").replace(/^\$\.?/, "");
    if (rawPath) {
        const parts = rawPath.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
        let current = input;
        let found = true;
        for (const part of parts) {
            if (current !== null && current !== undefined && Object.prototype.hasOwnProperty.call(current, part)) {
                current = current[part];
            } else {
                found = false;
                break;
            }
        }
        if (found) return current;
    }

    const terminal = rawPath.split(".").pop();
    if (!terminal) return undefined;
    return findValueByKey(input, terminal);
}

function findValueByKey(value, targetKey) {
    if (!value || typeof value !== "object") return undefined;
    if (!Array.isArray(value) && Object.prototype.hasOwnProperty.call(value, targetKey)) {
        return value[targetKey];
    }
    for (const child of Object.values(value)) {
        const found = findValueByKey(child, targetKey);
        if (found !== undefined) return found;
    }
    return undefined;
}

function escapeJsString(value) {
    return String(value || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function openDefectTraceModal(attribute) {
    const current = window.currentDefectTraces?.[attribute];
    if (!current) return;
    const modal = document.getElementById("defectTraceModal");
    const body = document.getElementById("defectTraceModalBody");
    const title = document.getElementById("defectTraceModalTitle");
    title.textContent = `${current.attribute} · Input → code path → Expected / Actual`;
    body.innerHTML = renderHorizontalDefectTrace(
        current.trace,
        current.locations,
        current.inputValue,
        current.expectedValue,
        current.actualValue,
        true,
        current.conclusion,
        current.likelyDivergence
    );
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
}

function closeDefectTraceModal() {
    const modal = document.getElementById("defectTraceModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
}

function formatValue(value) {
    if (value === null) return "null";
    if (value === undefined) return "undefined";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}


async function analyseRegression() {

    const container =
        document.getElementById(
            "regressionResult"
        );

    container.innerHTML =
        "Checking Git changes...";

    try {

        const response =
            await fetch(
                "/api/regression/impact"
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                JSON.stringify(
                    data
                )
            );
        }

        container.innerHTML =
            renderRegression(
                data
            );

    } catch (error) {

        container.innerHTML =
            renderError(
                error.message
            );
    }
}


function renderRegression(data) {
    if (data.status === "NO_CHANGES") {
        return `<div class="success">No Java source changes detected.</div>`;
    }

    const direct = data.directly_affected || [];
    const possible = data.possibly_affected || [];
    const unaffected = data.unaffected_scenarios || [];
    const methods = data.changed_methods || [];
    const affectedCount = direct.length + possible.length;

    let html = `
        <div class="regression-summary-grid">
            <div class="metric-card"><span class="metric-label">Changed files</span><strong>${data.total_changed_java_files || 0}</strong></div>
            <div class="metric-card danger-metric"><span class="metric-label">Directly affected</span><strong>${direct.length}</strong></div>
            <div class="metric-card warning-metric"><span class="metric-label">Possibly affected</span><strong>${possible.length}</strong></div>
            <div class="metric-card"><span class="metric-label">Affected scenarios</span><strong>${affectedCount}</strong></div>
        </div>
        ${unaffected.length ? `<div class="muted-text regression-hidden-note">${unaffected.length} unaffected / no-baseline scenario${unaffected.length === 1 ? "" : "s"} hidden.</div>` : ""}
    `;

    html += renderImpactGroup("Directly affected", direct, "danger", "No directly affected scenarios found.");
    html += renderImpactGroup("Possibly affected", possible, "warning", "No possibly affected scenarios found.");

    if (methods.length > 0) {
        html += `
            <details class="technical-details changed-methods-details">
                <summary>Changed methods (${methods.length})</summary>
                <div class="changed-method-list">
                    ${methods.map(method => `
                        <div class="code-location">
                            ${escapeHtml(method.class_name)}.${escapeHtml(method.method_name)}
                            ${method.changed_lines?.length ? ` — lines ${method.changed_lines.join(", ")}` : ""}
                        </div>
                    `).join("")}
                </div>
            </details>
        `;
    }

    return html;
}

function renderImpactGroup(title, scenarios, cssClass, emptyText) {
    let html = `<div class="impact-group"><div class="impact-group-header"><h3>${escapeHtml(title)}</h3><span class="tag">${scenarios.length}</span></div>`;
    if (scenarios.length === 0) {
        html += `<div class="muted-box">${escapeHtml(emptyText)}</div></div>`;
        return html;
    }
    html += `<div class="impact-card-grid">`;
    for (const scenario of scenarios) {
        const version = scenario.baseline_version ? `V${scenario.baseline_version}` : "No baseline";
        html += `
            <div class="impact-card ${cssClass}">
                <div class="impact-card-title">${escapeHtml(scenario.scenario_code || "")}</div>
                <div class="muted-text">${escapeHtml(scenario.http_method || "")} ${escapeHtml(scenario.endpoint || "")}</div>
                <div class="impact-card-footer">
                    <span class="tag">${escapeHtml(version)}</span>
                    ${scenario.matched_classes?.length ? `<span class="muted-text">${escapeHtml(scenario.matched_classes.join(", "))}</span>` : ""}
                </div>
            </div>
        `;
    }
    html += `</div></div>`;
    return html;
}


async function loadBaselineOverview() {
    const container = document.getElementById("baselineOverview");
    const select = document.getElementById("baselineScenarioSelect");
    if (!container || !select) return;
    container.innerHTML = "Loading scenario baselines...";

    try {
        const response = await fetch("/api/scenario-baselines/overview");
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));

        baselineOverviewData = Array.isArray(data) ? data : [];
        const captured = data.filter(item => item.baseline_captured).length;
        const missing = data.length - captured;
        let html = `
            <div class="baseline-summary-grid">
                <div class="metric-card"><span class="metric-label">Registered</span><strong>${data.length}</strong></div>
                <div class="metric-card success-metric"><span class="metric-label">With baseline</span><strong>${captured}</strong></div>
                <div class="metric-card warning-metric"><span class="metric-label">Not captured</span><strong>${missing}</strong></div>
            </div>
            <div class="baseline-card-grid">
        `;
        for (const item of data) {
            html += `
                <div class="baseline-card ${item.baseline_captured ? "has-baseline" : "missing-baseline"}">
                    <button class="baseline-card-main" onclick="selectBaselineScenario(${item.scenario_id})">
                        <span class="baseline-code">${escapeHtml(item.scenario_code)}</span>
                        <span class="muted-text">${escapeHtml(item.http_method)} ${escapeHtml(item.endpoint)}</span>
                        <span class="baseline-meta">
                            ${item.baseline_captured ? `<strong>${escapeHtml(item.active_baseline_name || "Release")} · V${item.active_release_version || item.active_baseline_version} ACTIVE</strong>` : `<strong>No baseline</strong>`}
                            <span>${item.history_count || 0} baseline version${item.history_count === 1 ? "" : "s"}</span>
                        </span>
                    </button>
                    <div class="baseline-card-actions">
                        <button class="mini-action" onclick="openMainBaselineModal(${item.scenario_id})">Add Baseline</button>
                        ${item.baseline_captured ? `<button class="mini-action secondary-button" onclick="openTestingBaselineModal(${item.scenario_id})">Add Test Baseline</button>` : ``}
                    </div>
                </div>
            `;
        }
        html += `</div>`;
        container.innerHTML = html;

        const current = select.value;
        select.innerHTML = `<option value="">Select a scenario...</option>` + data.map(item =>
            `<option value="${item.scenario_id}">${escapeHtml(item.http_method)} ${escapeHtml(item.endpoint)} — ${escapeHtml(item.scenario_code)} ${item.baseline_captured ? `— ${escapeHtml(item.active_baseline_name || "Release")} V${item.active_release_version || item.active_baseline_version}` : "— no baseline"}</option>`
        ).join("");
        if (current && data.some(x => String(x.scenario_id) === String(current))) select.value = current;
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

let activeMainBaselineScenarioId = null;
let mainBaselineCreateMode = "release";
let mainBaselineHistoryItems = [];
let activeTestingBaselineHistoryScenarioId = null;

function releaseDisplayVersion(item) {
    return Number(item?.release_version || item?.baseline_version || 1);
}

function groupBaselineReleases(items) {
    const groups = new Map();
    for (const item of (items || [])) {
        const name = String(item.baseline_name || "Legacy").trim() || "Legacy";
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push(item);
    }
    for (const versions of groups.values()) {
        versions.sort((a, b) => releaseDisplayVersion(a) - releaseDisplayVersion(b));
    }
    return groups;
}

async function openMainBaselineModal(scenarioId) {
    const item = baselineOverviewData.find(x => Number(x.scenario_id) === Number(scenarioId));
    const modal = document.getElementById("mainBaselineModal");
    if (!item || !modal) return;

    activeMainBaselineScenarioId = Number(scenarioId);
    mainBaselineCreateMode = "release";
    document.querySelector("#mainBaselineModal h2").textContent = "Add Baseline";
    document.getElementById("mainBaselineOperation").textContent =
        `${item.http_method} ${item.endpoint} · ${item.scenario_code} · Month/Year → Version`;
    const error = document.getElementById("mainBaselineError");
    if (error) error.textContent = "";

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");

    try {
        const response = await fetch(`/api/scenario-baselines/history/${scenarioId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        mainBaselineHistoryItems = Array.isArray(data) ? data : [];
    } catch (_) {
        mainBaselineHistoryItems = [];
    }

    const now = new Date();
    const monthSelect = document.getElementById("mainBaselineMonth");
    const yearSelect = document.getElementById("mainBaselineYear");
    if (monthSelect) {
        monthSelect.value = now.toLocaleString("en-US", {month: "long"});
    }
    if (yearSelect) {
        const currentYear = now.getFullYear();
        yearSelect.innerHTML = Array.from({length: 7}, (_, i) => currentYear - 2 + i)
            .map(year => `<option value="${year}">${year}</option>`).join("");
        yearSelect.value = String(currentYear);
    }
    updateMainBaselineVersionPreview();
}

function selectedMainBaselineReleaseName() {
    const month = (document.getElementById("mainBaselineMonth")?.value || "").trim();
    const year = (document.getElementById("mainBaselineYear")?.value || "").trim();
    return month && year ? `${month} ${year}` : "";
}

function updateMainBaselineVersionPreview() {
    const releaseName = selectedMainBaselineReleaseName();
    const display = document.getElementById("mainBaselineVersionDisplay");
    const groups = groupBaselineReleases(mainBaselineHistoryItems);
    const versions = groups.get(releaseName) || [];
    const next = versions.length ? Math.max(...versions.map(releaseDisplayVersion)) + 1 : 1;
    if (display) display.value = `V${next}`;
    const button = document.getElementById("saveMainBaselineButton");
    if (button) button.textContent = releaseName ? `Create ${releaseName} · V${next}` : `Create Baseline V${next}`;
}

async function openNewMainBaselineModalFromHistory() {
    const scenarioId = activeTestingBaselineHistoryScenarioId;
    if (!scenarioId) return;
    closeTestingBaselineHistoryModal(false);
    await openMainBaselineModal(scenarioId);
}

function closeMainBaselineModal() {
    const modal = document.getElementById("mainBaselineModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    activeMainBaselineScenarioId = null;
    mainBaselineCreateMode = "release";
}

async function saveMainBaseline() {
    const scenarioId = activeMainBaselineScenarioId;
    const error = document.getElementById("mainBaselineError");
    const button = document.getElementById("saveMainBaselineButton");
    if (!scenarioId) return;
    if (error) error.textContent = "";
    try {
        const releaseName = selectedMainBaselineReleaseName();
        if (!releaseName) throw new Error("Month and year are required.");
        const item = baselineOverviewData.find(x => Number(x.scenario_id) === Number(scenarioId));
        if (!item) throw new Error("Scenario overview is not loaded.");

        if (button) { button.disabled = true; button.textContent = "Creating..."; }
        const flowResponse = await fetch(
            "/api/endpoint-flow/analyze?http_method=" + encodeURIComponent(item.http_method)
            + "&endpoint=" + encodeURIComponent(item.endpoint)
        );
        const flowData = await flowResponse.json();
        if (!flowResponse.ok) throw new Error(JSON.stringify(flowData));

        const response = await fetch(`/api/scenario-baselines/release-version/${scenarioId}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({baseline_name: releaseName, successful_response: null, endpoint_flow: flowData})
        });
        const data = await response.json();
        if (!response.ok) {
            const detail = data?.detail;
            throw new Error(typeof detail === "object" ? (detail.message || JSON.stringify(detail)) : (detail || `HTTP ${response.status}`));
        }
        closeMainBaselineModal();
        await loadBaselineOverview();
        const scenarioSelect = document.getElementById("baselineScenarioSelect");
        if (scenarioSelect) scenarioSelect.value = String(scenarioId);
        if (typeof loadBaselineHistory === "function") await loadBaselineHistory();
        // Continue the workflow immediately: Release/Version is now created,
        // so the user can attach the first Test Scenario + JIRA to it.
        await openTestingBaselineModal(scenarioId);
    } catch (e) {
        if (error) error.textContent = e.message || "Could not create baseline version.";
    } finally {
        if (button) button.disabled = false;
    }
}


let activeTestingBaselineScenarioId = null;
let testingBaselineJiraIds = [];
let testingBaselineVersionItems = [];
let editingTestingBaselineId = null;

function ensureTestingBaselineSelectors() {
    const modal = document.getElementById("testingBaselineModal");
    if (!modal) return;

    const scroll = modal.querySelector(".scenario-create-scroll");
    const nameInput = document.getElementById("testingBaselineName");
    if (!scroll || !nameInput) return;

    const nameLabel = nameInput.closest("label");
    if (nameLabel) {
        const textNode = Array.from(nameLabel.childNodes)
            .find(node => node.nodeType === Node.TEXT_NODE && String(node.textContent || "").trim());
        if (textNode) textNode.textContent = "Test Scenario Name\n";
    }
    nameInput.placeholder = "e.g. Student add happy path test";

    if (document.getElementById("testingBaselineReleaseSelect") && document.getElementById("testingBaselineVersionSelect")) {
        return;
    }

    const baselineBlock = document.createElement("div");
    baselineBlock.id = "testingBaselineSelectorBlock";
    baselineBlock.innerHTML = `
        <div class="scenario-create-grid">
            <label>Existing Code Baseline
                <select id="testingBaselineReleaseSelect" onchange="onTestingBaselineReleaseChanged()">
                    <option value="">Loading baselines...</option>
                </select>
            </label>
            <label>Version
                <select id="testingBaselineVersionSelect" onchange="onTestingBaselineVersionChanged()">
                    <option value="">Select version...</option>
                </select>
            </label>
        </div>
        <label>Selected Code Baseline
            <input id="testingMainBaselineDisplay" readonly type="text" value=""/>
        </label>
    `;

    scroll.insertBefore(baselineBlock, nameLabel || scroll.firstChild);
}

async function loadTestingBaselineJiraOptions() {
    const select = document.getElementById("testingBaselineJiraSelect");
    if (!select) return;

    select.innerHTML = '<option value="">Loading saved JIRAs...</option>';
    try {
        const response = await fetch("/api/jira-knowledge");
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));

        const items = Array.isArray(data) ? data : [];
        select.innerHTML = '<option value="">Select a saved JIRA...</option>' +
            items.map(item => {
                const jiraId = String(item.jira_id || "").trim();
                const title = String(item.title || "").trim();
                const label = title ? `${jiraId} — ${title}` : jiraId;
                return `<option value="${escapeHtml(jiraId)}">${escapeHtml(label)}</option>`;
            }).join("");

        if (!items.length) {
            select.innerHTML = '<option value="">No saved JIRAs available</option>';
        }
    } catch (error) {
        select.innerHTML = '<option value="">Unable to load saved JIRAs</option>';
    }
}

function renderTestingBaselineJiras() {
    const container = document.getElementById("testingBaselineJiraList");
    if (!container) return;

    if (!testingBaselineJiraIds.length) {
        container.innerHTML = '<span class="muted-text">No JIRAs added.</span>';
        return;
    }

    container.innerHTML = testingBaselineJiraIds.map((jiraId, index) => `
        <span class="testing-jira-chip">
            ${escapeHtml(jiraId)}
            <button type="button"
                    class="testing-jira-remove"
                    aria-label="Remove ${escapeHtml(jiraId)}"
                    onclick="removeTestingBaselineJira(${index})">×</button>
        </span>
    `).join("");
}

function addTestingBaselineJira() {
    const select = document.getElementById("testingBaselineJiraSelect");
    if (!select) return;

    const jiraId = String(select.value || "").trim().toUpperCase();
    if (!jiraId) return;

    if (!testingBaselineJiraIds.includes(jiraId)) {
        testingBaselineJiraIds.push(jiraId);
        renderTestingBaselineJiras();
    }

    select.value = "";
}

function removeTestingBaselineJira(index) {
    testingBaselineJiraIds.splice(Number(index), 1);
    renderTestingBaselineJiras();
}


async function openTestingBaselineModal(scenarioId) {
    editingTestingBaselineId = null;
    const editTitle = document.querySelector("#testingBaselineModal h2");
    if (editTitle) editTitle.textContent = "Add Testing Baseline";
    const editSaveButton = document.getElementById("saveTestingBaselineButton");
    if (editSaveButton) editSaveButton.textContent = "Save Testing Baseline";
    const editReleaseSelect = document.getElementById("testingBaselineReleaseSelect");
    const editVersionSelect = document.getElementById("testingBaselineVersionSelect");
    if (editReleaseSelect) editReleaseSelect.disabled = false;
    if (editVersionSelect) editVersionSelect.disabled = false;
    const modal = document.getElementById("testingBaselineModal");
    const item = baselineOverviewData.find(x => Number(x.scenario_id) === Number(scenarioId));
    if (!modal || !item) return;
    ensureTestingBaselineSelectors();
    if (!item.baseline_captured) {
        alert("Create a baseline version first.");
        return;
    }
    activeTestingBaselineScenarioId = Number(scenarioId);

    document.getElementById("testingBaselineOperation").textContent =
        `${item.http_method} ${item.endpoint} · ${item.scenario_code}`;

    try {
        const response = await fetch(`/api/scenario-baselines/history/${scenarioId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        testingBaselineVersionItems = Array.isArray(data) ? data : [];
    } catch (_) {
        testingBaselineVersionItems = [];
    }

    if (!testingBaselineVersionItems.length && item.active_baseline_id) {
        testingBaselineVersionItems = [{
            id: item.active_baseline_id,
            baseline_name: item.active_baseline_name || "Current Baseline",
            release_version: item.active_release_version || item.active_baseline_version || 1,
            baseline_version: item.active_baseline_version || item.active_release_version || 1,
            is_active: true
        }];
    }

    const groups = groupBaselineReleases(testingBaselineVersionItems);
    const releaseSelect = document.getElementById("testingBaselineReleaseSelect");
    if (releaseSelect) {
        const releaseNames = [...groups.keys()];
        releaseSelect.innerHTML = releaseNames.length
            ? releaseNames.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("")
            : `<option value="">No baseline versions found</option>`;

        if (item.active_baseline_name && groups.has(item.active_baseline_name)) {
            releaseSelect.value = item.active_baseline_name;
        } else if (releaseNames.length) {
            releaseSelect.value = releaseNames[0];
        }
    }
    onTestingBaselineReleaseChanged();

    ["testingBaselineName", "testingBaselineRequest", "testingBaselineExpected", "testingBaselineActual", "testingBaselineDbEffect"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });

    const error = document.getElementById("testingBaselineError");
    if (error) error.textContent = "";

    testingBaselineJiraIds = [];
    renderTestingBaselineJiras();
    await loadTestingBaselineJiraOptions();

    try {
        const response = await fetch(`/api/scenarios/${scenarioId}`);
        const scenario = await response.json();
        if (response.ok) {
            const request = document.getElementById("testingBaselineRequest");
            const expected = document.getElementById("testingBaselineExpected");
            const dbEffect = document.getElementById("testingBaselineDbEffect");
            if (request && scenario.request_json) request.value = prettyScenarioJson(scenario.request_json);
            if (expected && scenario.expected_response_json) expected.value = prettyScenarioJson(scenario.expected_response_json);
            if (dbEffect && scenario.expected_db_effect) dbEffect.value = scenario.expected_db_effect;
        }
    } catch (_) {}

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    setTimeout(() => document.getElementById("testingBaselineName")?.focus(), 0);
}

function onTestingBaselineReleaseChanged() {
    const releaseSelect = document.getElementById("testingBaselineReleaseSelect");
    const versionSelect = document.getElementById("testingBaselineVersionSelect");
    if (!releaseSelect || !versionSelect) return;
    const release = releaseSelect.value;
    const versions = testingBaselineVersionItems
        .filter(x => String(x.baseline_name || "Legacy") === release)
        .sort((a,b) => releaseDisplayVersion(a) - releaseDisplayVersion(b));
    versionSelect.innerHTML = versions.length
        ? versions.map(x =>
            `<option value="${x.id}">V${releaseDisplayVersion(x)}</option>`
        ).join("")
        : `<option value="">No versions found</option>`;
    const active = versions.find(x => x.is_active) || versions[versions.length - 1];
    if (active) versionSelect.value = String(active.id);
    onTestingBaselineVersionChanged();
}

function onTestingBaselineVersionChanged() {
    const releaseSelect = document.getElementById("testingBaselineReleaseSelect");
    const versionSelect = document.getElementById("testingBaselineVersionSelect");
    const display = document.getElementById("testingMainBaselineDisplay");
    const selected = testingBaselineVersionItems.find(x => Number(x.id) === Number(versionSelect?.value));
    if (display) display.value = selected
        ? `${releaseSelect?.value || selected.baseline_name || "Release"} · V${releaseDisplayVersion(selected)}`
        : "";
}

function closeTestingBaselineModal() {
    const modal = document.getElementById("testingBaselineModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    activeTestingBaselineScenarioId = null;
    testingBaselineJiraIds = [];
    testingBaselineVersionItems = [];
    editingTestingBaselineId = null;
    renderTestingBaselineJiras();
}

function prettyScenarioJson(value) {
    if (value == null || value === "") return "";
    if (typeof value !== "string") return JSON.stringify(value, null, 2);
    try { return JSON.stringify(JSON.parse(value), null, 2); }
    catch (_) { return value; }
}

function parseOptionalJsonField(id, label) {
    const raw = (document.getElementById(id)?.value || "").trim();
    if (!raw) return null;
    try { return JSON.parse(raw); }
    catch (_) { throw new Error(`${label} must contain valid JSON.`); }
}

async function saveTestingBaseline() {
    const scenarioId = activeTestingBaselineScenarioId;
    const error = document.getElementById("testingBaselineError");
    const button = document.getElementById("saveTestingBaselineButton");
    if (!scenarioId) return;
    if (error) error.textContent = "";

    try {
        const name = (document.getElementById("testingBaselineName")?.value || "").trim();
        if (!name) throw new Error("Test Scenario is required.");

        const item = baselineOverviewData.find(x => Number(x.scenario_id) === Number(scenarioId));
        if (!item?.baseline_captured) throw new Error("Create the Main Baseline before adding a Test Baseline.");

        // Safety net: if the user selected a JIRA but did not explicitly press +,
        // include that selection before saving the test baseline.
        const jiraSelect = document.getElementById("testingBaselineJiraSelect");
        const selectedJira = String(jiraSelect?.value || "").trim().toUpperCase();
        if (selectedJira && !testingBaselineJiraIds.includes(selectedJira)) {
            testingBaselineJiraIds.push(selectedJira);
            renderTestingBaselineJiras();
            if (jiraSelect) jiraSelect.value = "";
        }

        const selectedBaselineId = Number(
            document.getElementById("testingBaselineVersionSelect")?.value
            || item.active_baseline_id
            || 0
        ) || null;

        const body = {
            baseline_name: name,
            baseline_id: selectedBaselineId,
            request_json: parseOptionalJsonField("testingBaselineRequest", "Request JSON"),
            expected_response: parseOptionalJsonField("testingBaselineExpected", "Expected Response JSON"),
            actual_response: parseOptionalJsonField("testingBaselineActual", "Actual Response JSON"),
            expected_db_effect: (document.getElementById("testingBaselineDbEffect")?.value || "").trim() || null,
            jira_ids: [...testingBaselineJiraIds]
        };

        if (button) { button.disabled = true; button.textContent = "Saving..."; }
        const editId = editingTestingBaselineId;
        const response = await fetch(
            editId ? `/api/scenario-baselines/testing/${scenarioId}/${editId}` : `/api/scenario-baselines/testing/${scenarioId}`,
            {
                method: editId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body)
            }
        );
        const data = await response.json();
        if (!response.ok) {
            const detail = data?.detail;
            throw new Error(typeof detail === "object" ? (detail.message || JSON.stringify(detail)) : (detail || `HTTP ${response.status}`));
        }

        closeTestingBaselineModal();
        await loadBaselineOverview();
        const select = document.getElementById("baselineScenarioSelect");
        if (select) select.value = String(scenarioId);
        if (typeof loadBaselineHistory === "function") await loadBaselineHistory();
    } catch (e) {
        if (error) error.textContent = e.message || "Could not save Test Baseline.";
    } finally {
        if (button) { button.disabled = false; button.textContent = "Save Test Baseline"; }
    }
}


let testingBaselineHistoryItems = [];

async function openTestingBaselineHistoryModal(scenarioId) {
    activeTestingBaselineHistoryScenarioId = Number(scenarioId);
    const modal = document.getElementById("testingBaselineHistoryModal");
    const select = document.getElementById("testingBaselineHistorySelect");
    const detail = document.getElementById("testingBaselineHistoryDetail");
    const operation = document.getElementById("testingBaselineHistoryOperation");
    const item = baselineOverviewData.find(x => Number(x.scenario_id) === Number(scenarioId));
    if (!modal || !select || !detail || !item) return;

    operation.textContent = `${item.http_method} ${item.endpoint} · ${item.scenario_code} · ${item.active_baseline_name || "Main Baseline"} · V${item.active_baseline_version}`;
    select.innerHTML = `<option value="">Loading saved baselines...</option>`;
    detail.innerHTML = `<div class="muted-box">Loading saved testing baselines...</div>`;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");

    try {
        const response = await fetch(`/api/scenario-baselines/testing/${scenarioId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));

        testingBaselineHistoryItems = Array.isArray(data) ? data : [];
        if (!testingBaselineHistoryItems.length) {
            select.innerHTML = `<option value="">No saved testing baselines</option>`;
            detail.innerHTML = `<div class="muted-box">No testing baselines have been saved for this operation.</div>`;
            return;
        }

        select.innerHTML = testingBaselineHistoryItems.map((baseline, index) => {
            const codeVersion = baseline.code_baseline_version ? ` · Code V${baseline.code_baseline_version}` : "";
            return `<option value="${index}">${escapeHtml(baseline.baseline_name || `Baseline ${index + 1}`)}${escapeHtml(codeVersion)}</option>`;
        }).join("");
        select.value = "0";
        renderSelectedTestingBaseline();
    } catch (error) {
        select.innerHTML = `<option value="">Unable to load baselines</option>`;
        detail.innerHTML = renderError(error.message);
    }
}

function closeTestingBaselineHistoryModal(clearScenario = true) {
    const modal = document.getElementById("testingBaselineHistoryModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    testingBaselineHistoryItems = [];
    if (clearScenario) activeTestingBaselineHistoryScenarioId = null;
}

function renderSelectedTestingBaseline() {
    const select = document.getElementById("testingBaselineHistorySelect");
    const detail = document.getElementById("testingBaselineHistoryDetail");
    if (!select || !detail) return;

    const index = Number(select.value);
    const baseline = testingBaselineHistoryItems[index];
    if (!baseline) {
        detail.innerHTML = `<div class="muted-box">Select a saved testing baseline.</div>`;
        return;
    }

    const jiraIds = Array.isArray(baseline.jira_ids) ? baseline.jira_ids : [];
    const jsonBlock = (label, value) => `
        <div class="testing-history-field">
            <div class="testing-history-label">${escapeHtml(label)}</div>
            <pre>${escapeHtml(prettyScenarioJson(value) || "Nothing")}</pre>
        </div>`;

    detail.innerHTML = `
        <div class="testing-history-summary">
            <div><span class="muted-text">Baseline</span><strong>${escapeHtml(baseline.baseline_name || "Testing baseline")}</strong></div>
            <div><span class="muted-text">Status</span><strong>${escapeHtml(baseline.status || "NOT_RUN")}</strong></div>
            <div><span class="muted-text">Code version</span><strong>${baseline.code_baseline_version ? `V${baseline.code_baseline_version}` : "Not captured"}</strong></div>
            <div><span class="muted-text">Saved</span><strong>${baseline.created_at ? escapeHtml(new Date(baseline.created_at).toLocaleString()) : "-"}</strong></div>
        </div>
        <div class="testing-history-jiras">
            <div class="testing-history-label">Related JIRAs</div>
            <div class="testing-jira-list">${jiraIds.length ? jiraIds.map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("") : `<span class="muted-text">None</span>`}</div>
        </div>
        ${jsonBlock("Request JSON", baseline.request_json)}
        ${jsonBlock("Expected Response JSON", baseline.expected_response_json)}
        ${jsonBlock("Actual Response JSON", baseline.actual_response_json)}
        <div class="testing-history-field">
            <div class="testing-history-label">Expected DB Effect</div>
            <pre>${escapeHtml(baseline.expected_db_effect || "Nothing")}</pre>
        </div>
        <div style="margin-top:12px;">
            <button class="secondary-button" type="button" onclick="editSelectedTestingBaseline()">✏ Edit Test Baseline</button>
        </div>`;
}

async function editSelectedTestingBaseline() {
    const baseline = testingBaselineHistoryItems[Number(document.getElementById("testingBaselineHistorySelect")?.value)];
    const scenarioId = activeTestingBaselineHistoryScenarioId;
    if (!baseline || !scenarioId) return;

    closeTestingBaselineHistoryModal(false);
    await openTestingBaselineModal(scenarioId);
    editingTestingBaselineId = Number(baseline.id);

    const title = document.querySelector("#testingBaselineModal h2");
    if (title) title.textContent = "Edit Testing Baseline";
    const button = document.getElementById("saveTestingBaselineButton");
    if (button) button.textContent = "Save Changes";

    document.getElementById("testingBaselineName").value = baseline.baseline_name || "";
    document.getElementById("testingBaselineRequest").value = prettyScenarioJson(baseline.request_json);
    document.getElementById("testingBaselineExpected").value = prettyScenarioJson(baseline.expected_response_json);
    document.getElementById("testingBaselineActual").value = prettyScenarioJson(baseline.actual_response_json);
    document.getElementById("testingBaselineDbEffect").value = baseline.expected_db_effect || "";
    testingBaselineJiraIds = Array.isArray(baseline.jira_ids) ? [...baseline.jira_ids] : [];
    renderTestingBaselineJiras();
}



async function editTestingBaselineFromRelease(scenarioId, testBaselineId) {
    try {
        const response = await fetch(`/api/scenario-baselines/testing/${scenarioId}`);
        const items = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(items));
        const baseline = (Array.isArray(items) ? items : []).find(x => Number(x.id) === Number(testBaselineId));
        if (!baseline) throw new Error("Test Baseline not found.");

        await openTestingBaselineModal(scenarioId);
        editingTestingBaselineId = Number(baseline.id);

        const title = document.querySelector("#testingBaselineModal h2");
        if (title) title.textContent = "Edit Testing Baseline";
        const button = document.getElementById("saveTestingBaselineButton");
        if (button) button.textContent = "Save Changes";

        document.getElementById("testingBaselineName").value = baseline.baseline_name || "";
        document.getElementById("testingBaselineRequest").value = prettyScenarioJson(baseline.request_json);
        document.getElementById("testingBaselineExpected").value = prettyScenarioJson(baseline.expected_response_json);
        document.getElementById("testingBaselineActual").value = prettyScenarioJson(baseline.actual_response_json);
        document.getElementById("testingBaselineDbEffect").value = baseline.expected_db_effect || "";
        testingBaselineJiraIds = Array.isArray(baseline.jira_ids) ? [...baseline.jira_ids] : [];
        renderTestingBaselineJiras();

        const releaseSelect = document.getElementById("testingBaselineReleaseSelect");
        const versionSelect = document.getElementById("testingBaselineVersionSelect");
        const selectedVersion = testingBaselineVersionItems.find(x =>
            Number(x.id) === Number(baseline.baseline_id) ||
            Number(x.baseline_version) === Number(baseline.code_baseline_version)
        );
        if (selectedVersion) {
            if (releaseSelect) {
                releaseSelect.value = selectedVersion.baseline_name || "Legacy";
                onTestingBaselineReleaseChanged();
            }
            if (versionSelect) {
                versionSelect.value = String(selectedVersion.id);
                onTestingBaselineVersionChanged();
            }
        }
        if (releaseSelect) releaseSelect.disabled = true;
        if (versionSelect) versionSelect.disabled = true;
    } catch (error) {
        alert(error.message || "Could not open Test Baseline for editing.");
    }
}


function selectBaselineScenario(scenarioId) {
    const select = document.getElementById("baselineScenarioSelect");
    select.value = String(scenarioId);
    loadBaselineHistory();
}

async function loadBaselineHistory() {
    const select = document.getElementById("baselineScenarioSelect");
    const scenarioId = select.value;
    const container = document.getElementById("baselineResult");
    if (!scenarioId) {
        container.innerHTML = "Select a scenario first.";
        return;
    }
    container.innerHTML = "Loading baseline history...";
    try {
        const [historyResponse, archiveResponse] = await Promise.all([
            fetch(`/api/scenario-baselines/history/${scenarioId}`),
            fetch(`/api/scenario-baselines/archive-status/${scenarioId}`)
        ]);
        const data = await historyResponse.json();
        const archiveStatus = archiveResponse.ok ? await archiveResponse.json() : {};
        if (!historyResponse.ok) throw new Error(JSON.stringify(data));
        if (!Array.isArray(data) || data.length === 0) {
            container.innerHTML = `<div class="warning">No baseline has been captured for this scenario yet.</div>`;
            return;
        }

        const label = b => `${escapeHtml(b.baseline_name || "Main Baseline")} · V${b.release_version || b.baseline_version || 1}`;
        const archiveAction = b => {
            const archived = archiveStatus[String(b.id)];
            return archived
                ? `<span class="tag">ARCHIVED</span><span class="muted-text">Audit ${escapeHtml(String(archived.snapshot_hash || "").slice(0, 12))}…</span>`
                : `<button class="secondary-button" type="button" onclick="archiveBaselineRelease(${Number(scenarioId)}, ${Number(b.id)})">Archive Release</button>`;
        };

        const active = data.find(item => item.is_active) || data[0];
        const previous = data.filter(item => item.id !== active.id);
        container.innerHTML = `
            <div class="active-baseline-card">
                <div><span class="muted-text">Current baseline</span><h3>${label(active)}</h3></div>
                <span class="tag">ACTIVE</span>
                <div class="baseline-detail">${escapeHtml(active.http_method)} ${escapeHtml(active.endpoint)}</div>
                <div class="baseline-detail">Flow: ${active.endpoint_flow ? "stored" : "not stored"}</div>
                <div class="baseline-detail">${archiveAction(active)}</div>
            </div>
            ${previous.length ? `
                <details class="technical-details">
                    <summary>Previous versions (${previous.length})</summary>
                    ${previous.map(b => `<div class="history-row"><strong>${label(b)}</strong><span>${escapeHtml(b.http_method)} ${escapeHtml(b.endpoint)}</span>${archiveAction(b)}</div>`).join("")}
                </details>` : `<div class="muted-box">No previous versions.</div>`}
            <div style="margin-top:12px;"><button class="secondary-button" type="button" onclick="viewReleaseArchives(${Number(scenarioId)})">View Release Archive</button></div>
            <div id="releaseArchiveResult" style="margin-top:10px;"></div>`;
    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

async function archiveBaselineRelease(scenarioId, baselineId) {
    if (!confirm("Archive this release/version? Archived releases become read-only.")) return;
    try {
        const response = await fetch(`/api/scenario-baselines/archive/${scenarioId}/${baselineId}`, {method: "POST"});
        const data = await response.json();
        if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
        await loadBaselineHistory();
    } catch (error) {
        alert(error.message || "Could not archive release.");
    }
}

async function viewReleaseArchives(scenarioId) {
    const target = document.getElementById("releaseArchiveResult");
    if (!target) return;
    target.innerHTML = "Loading release archive...";
    try {
        const response = await fetch(`/api/scenario-baselines/archives/${scenarioId}`);
        const items = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(items));
        target.innerHTML = Array.isArray(items) && items.length
            ? items.map(item => `<div class="history-row"><strong>${escapeHtml(item.baseline_name)} · V${item.release_version}</strong><span>Code V${item.code_baseline_version}</span><span>${item.archived_at ? escapeHtml(new Date(item.archived_at).toLocaleString()) : ""}</span><span class="muted-text">SHA-256 ${escapeHtml(String(item.snapshot_hash || "").slice(0, 16))}…</span></div>`).join("")
            : `<div class="muted-box">No archived release versions yet.</div>`;
    } catch (error) {
        target.innerHTML = renderError(error.message);
    }
}

function downloadScenarioExcel() {
    window.location.href = "/api/reports/regression/excel";
}

async function loadJiraHistoryBoard() {
    const select = document.getElementById("jiraHistorySelect");
    const result = document.getElementById("jiraHistoryBoardResult");
    if (!select || !result) return;

    result.innerHTML = "Loading saved JIRAs...";
    select.innerHTML = `<option value="">Loading saved JIRAs...</option>`;
    try {
        const response = await fetch("/api/jira-knowledge");
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));

        const items = Array.isArray(data) ? data : [];
        if (!items.length) {
            select.innerHTML = `<option value="">No saved JIRAs available</option>`;
            result.innerHTML = `<div class="muted-box">No saved JIRAs found. Save a JIRA in JIRA Impact Analysis first, then attach it while adding a Testing Baseline.</div>`;
            return;
        }

        select.innerHTML = `<option value="">Select a saved JIRA...</option>` + items.map(item => {
            const jiraId = String(item.jira_id || "").trim().toUpperCase();
            const title = String(item.title || "").trim();
            const label = title ? `${jiraId} — ${title}` : jiraId;
            return `<option value="${escapeHtml(jiraId)}">${escapeHtml(label)}</option>`;
        }).join("");
        result.innerHTML = `<div class="muted-box">Choose a JIRA and click View Coverage.</div>`;
    } catch (error) {
        select.innerHTML = `<option value="">Unable to load JIRAs</option>`;
        result.innerHTML = renderError(error.message);
    }
}

async function loadSelectedJiraCoverage() {
    const select = document.getElementById("jiraHistorySelect");
    const result = document.getElementById("jiraHistoryBoardResult");
    if (!select || !result) return;

    const jiraId = String(select.value || "").trim().toUpperCase();
    if (!jiraId) {
        result.innerHTML = `<div class="warning">Select a JIRA first.</div>`;
        return;
    }

    result.innerHTML = `Loading baseline coverage for ${escapeHtml(jiraId)}...`;
    try {
        const response = await fetch(`/api/scenario-baselines/jira-coverage/${encodeURIComponent(jiraId)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(data));
        result.innerHTML = renderJiraCoverage(data);
    } catch (error) {
        result.innerHTML = renderError(error.message);
    }
}

function renderJiraCoverage(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const jiraId = data.jira_id || "";

    if (!items.length) {
        return `
            <div class="jira-history-text-board">
                <h3>JIRA: ${escapeHtml(jiraId)}</h3>
                <div class="muted-box">No baseline is linked to this JIRA yet. Add it from Scenario Baselines → Add Test Baseline → Related JIRAs.</div>
            </div>
        `;
    }

    const lines = items.map(item => {
        const release = `${item.release_name || "Release"} ${item.release_version ? `V${item.release_version}` : ""}`.trim();
        return `
            <div class="jira-history-line">
                <strong>${escapeHtml(item.scenario_code || "")}</strong>
                <span>→</span>
                <span>${escapeHtml(release)}</span>
                <span>→</span>
                <span>${escapeHtml(item.testing_baseline_name || "Testing baseline")}</span>
                <span>→</span>
                <strong>${escapeHtml(item.status || "NOT_RUN")}</strong>
            </div>
        `;
    }).join("");

    return `
        <div class="jira-history-text-board">
            <h3>JIRA: ${escapeHtml(jiraId)}</h3>
            <div class="muted-text">${Number(data.scenario_count || 0)} scenario(s), ${Number(data.covered_count || items.length)} baseline coverage item(s)</div>
            <h4>Covered Baselines</h4>
            <div class="jira-history-lines">${lines}</div>
        </div>
    `;
}

async function rebuildScenarioRagRegistry() {
    const result = document.getElementById("scenarioRagRegistryResult");
    if (!result) return;

    result.innerHTML = "Rebuilding scenario registry index...";
    try {
        const response = await fetch("/api/scenario-rag-registry/index", {
            method: "POST"
        });
        const data = await readJsonOrText(response);
        if (!response.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));

        result.innerHTML = `
            <div class="success">
                Scenario registry indexed · ${Number(data.documents || 0)} document(s)
            </div>
            <div class="muted-box">
                Indexed scenarios, release versions, test baselines, JIRAs and stored flow/test data for the selected project.
            </div>
        `;
    } catch (error) {
        result.innerHTML = renderError(error.message);
    }
}

async function searchScenarioRagRegistry() {
    const input = document.getElementById("scenarioRagQuery");
    const result = document.getElementById("scenarioRagRegistryResult");
    if (!input || !result) return;

    const query = String(input.value || "").trim();
    if (!query) {
        result.innerHTML = `<div class="warning">Enter an attribute, JIRA, release, version or scenario to search.</div>`;
        return;
    }

    result.innerHTML = "Searching scenario registry...";
    try {
        const response = await fetch(`/api/scenario-rag-registry/search?query=${encodeURIComponent(query)}&top_k=10`);
        const data = await readJsonOrText(response);
        if (!response.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));

        result.innerHTML = renderScenarioRagRegistryResults(data);
    } catch (error) {
        result.innerHTML = renderError(error.message);
    }
}

async function readJsonOrText(response) {
    const text = await response.text();
    if (!text) return {};
    try {
        return JSON.parse(text);
    } catch (_) {
        return text;
    }
}

function renderScenarioRagRegistryResults(data) {
    const results = Array.isArray(data.results) ? data.results : [];
    const query = data.query || "";

    if (!results.length) {
        return `
            <div class="muted-box scenario-rag-empty">
                No captured scenario/release evidence found for <strong>${escapeHtml(query)}</strong>.
            </div>
        `;
    }

    const understanding = data.llm_understanding || {};
    const historical = data.historical_answer || {};
    const historicalPanel = historical.is_historical_query ? `
        <div class="muted-box scenario-rag-historical-answer" style="margin:12px 0;">
            <strong>Historical Answer</strong>
            <div style="margin-top:6px;">${escapeHtml(historical.message || "No captured historical evidence found.")}</div>
            ${(Array.isArray(historical.timeline) && historical.timeline.length) ? `
                <details style="margin-top:8px;">
                    <summary>View historical trace</summary>
                    <div style="margin-top:8px;">
                        ${historical.timeline.map(row => `
                            <div class="jira-history-line" style="margin-bottom:6px;flex-wrap:wrap;">
                                <strong>${escapeHtml(row.release_name || "No release")}${row.release_version ? ` V${escapeHtml(row.release_version)}` : ""}</strong>
                                ${row.test_baseline ? `<span>→</span><span>${escapeHtml(row.test_baseline)}</span>` : ""}
                                ${row.test_status ? `<span>→</span><span>${escapeHtml(row.test_status)}</span>` : ""}
                                ${(Array.isArray(row.jira_ids) && row.jira_ids.length)
                                    ? `<span>→</span>${row.jira_ids.map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("")}`
                                    : ""}
                            </div>
                        `).join("")}
                    </div>
                </details>
            ` : ""}
        </div>
    ` : "";
    const llmPanel = understanding.enabled ? `
        <div class="muted-box scenario-rag-understanding">
            <strong>LLM query understanding:</strong>
            ${understanding.used ? "Used" : "Fallback"}
            ${(understanding.attributes || []).length ? ` · Attributes: ${(understanding.attributes || []).map(escapeHtml).join(", ")}` : ""}
            ${(understanding.concepts || []).length ? ` · Concepts: ${(understanding.concepts || []).map(escapeHtml).join(", ")}` : ""}
            ${understanding.reason ? ` · ${escapeHtml(understanding.reason)}` : ""}
        </div>
    ` : `
        <div class="muted-box scenario-rag-understanding">
            <strong>LLM query understanding:</strong> Not configured. Used local hybrid retrieval.
        </div>
    `;

    const cards = results.map(item => {
        const meta = item.metadata || {};
        const isTest = meta.document_type === "test_baseline";
        const title = isTest
            ? (meta.relevant_test_baseline || meta.scenario_code || item.scenario_code || "")
            : (meta.scenario_code || item.scenario_code || "");
        const release = meta.release_name
            ? `${meta.release_name}${meta.release_version ? ` V${meta.release_version}` : ""}`
            : "No release captured";
        const codeVersion = meta.code_baseline_version
            ? `Code V${meta.code_baseline_version}`
            : "No code version";
        const jiras = Array.isArray(meta.jira_ids) ? meta.jira_ids : [];
        const tests = Array.isArray(meta.test_baselines) ? meta.test_baselines : [];
        const reasons = Array.isArray(item.reasons) ? item.reasons : [];

        return `
            <div class="active-baseline-card scenario-rag-card" style="display:grid;grid-template-columns:minmax(0,1.35fr) minmax(320px,.85fr);gap:18px;align-items:start;">
                <div class="scenario-rag-main" style="min-width:0;">
                    <div class="scenario-rag-title-row">
                        <span class="muted-text">Score ${Number(item.score || 0)}</span>
                        <h3 style="margin:.15rem 0;">${escapeHtml(title)}</h3>
                        ${isTest ? `<div class="muted-text">Parent scenario: ${escapeHtml(meta.scenario_code || item.scenario_code || "")}</div>` : ""}
                    </div>

                    <div class="baseline-detail" style="margin-top:6px;">
                        ${escapeHtml(meta.http_method || "")} ${escapeHtml(meta.endpoint || "")}
                    </div>

                    <div class="scenario-rag-jiras" style="margin-top:10px;">
                        ${(jiras.length ? jiras : ["No JIRA"]).map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("")}
                    </div>
                </div>

                <div class="scenario-rag-side" style="min-width:0;">
                    <div class="scenario-rag-release" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
                        <span class="tag">${escapeHtml(release)}</span>
                        <span class="muted-text">${escapeHtml(codeVersion)}</span>
                    </div>

                    ${tests.length ? `
                        <div class="testing-history-field" style="margin:0;">
                            <div class="testing-history-label">Testing baselines</div>
                            ${tests.map(test => `
                                <div class="jira-history-line">
                                    <strong>${escapeHtml(test.name || "Testing baseline")}</strong>
                                    <span>→</span>
                                    <span>${escapeHtml(test.status || "NOT_RUN")}</span>
                                    ${(Array.isArray(test.jira_ids) && test.jira_ids.length)
                                        ? `<span>→</span>${test.jira_ids.map(id => `<span class="testing-jira-chip">${escapeHtml(id)}</span>`).join("")}`
                                        : ""}
                                </div>
                            `).join("")}
                        </div>
                    ` : `<div class="muted-text">No testing baseline captured.</div>`}
                </div>

                <div class="scenario-rag-footer" style="grid-column:1 / -1;border-top:1px solid rgba(0,0,0,.07);padding-top:10px;">
                    ${reasons.length ? `<div class="muted-text" style="margin-bottom:8px;">${reasons.map(escapeHtml).join(" · ")}</div>` : ""}
                    ${item.summary ? `<details class="muted-box" style="margin:0;"><summary>View matched evidence</summary><div>${escapeHtml(item.summary)}</div></details>` : ""}
                </div>
            </div>
        `;
    }).join("");

    return `
        <div class="jira-history-text-board scenario-rag-results">
            <h3>Scenario Registry Results</h3>
            <div class="muted-text">${Number(data.total_matches || results.length)} match(es) for ${escapeHtml(query)}</div>
            ${llmPanel}
            ${historicalPanel}
            ${cards}
        </div>
    `;
}


async function generateFlowchart() {

    const method =
        document.getElementById(
            "chartMethod"
        ).value;

    const endpoint =
        document.getElementById(
            "chartEndpoint"
        ).value;

    const container =
        document.getElementById(
            "flowchartResult"
        );

    if (!endpoint) {
        container.innerHTML =
            renderError(
                "Please select an endpoint."
            );
        return;
    }

    container.innerHTML =
        "Generating flowchart...";

    try {
        const url =
            "/api/reports/flowchart"
            + "?http_method="
            + encodeURIComponent(method)
            + "&endpoint="
            + encodeURIComponent(endpoint);

        const response = await fetch(url);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(JSON.stringify(data));
        }

        if (!data.mermaid) {
            container.innerHTML =
                renderError(
                    "Flowchart was not generated."
                );
            return;
        }

        const diagramId =
            "mermaid-" + Date.now();

        container.innerHTML = `
            <div class="flowchart-toolbar">
                <div class="flowchart-caption">
                    ${escapeHtml(method)} ${escapeHtml(endpoint)}
                </div>
                <div class="flowchart-actions">
                    <button type="button" onclick="openFlowchartModal()">
                        Open Large View
                    </button>
                    <button type="button" onclick="downloadCurrentFlowchartSvg()">
                        Download SVG
                    </button>
                </div>
            </div>
            <div id="${diagramId}" class="flowchart-preview ${data.total_nodes <= 4 ? "flowchart-preview-compact" : ""}"></div>
        `;

        const result = await window.mermaid.render(
            diagramId + "-svg",
            data.mermaid
        );

        const target =
            document.getElementById(diagramId);

        target.innerHTML = result.svg;

        window.currentFlowchart = {
            method,
            endpoint,
            mermaid: data.mermaid,
            svg: result.svg
        };

        const source =
            document.getElementById(
                "flowchartMermaidSource"
            );
        if (source) {
            source.textContent = data.mermaid;
        }

    } catch (error) {
        container.innerHTML =
            renderError(error.message);
    }
}


function openFlowchartModal() {
    const current = window.currentFlowchart;
    if (!current) {
        return;
    }

    const modal =
        document.getElementById("flowchartModal");
    const body =
        document.getElementById("flowchartModalBody");
    const title =
        document.getElementById("flowchartModalTitle");

    body.innerHTML = current.svg;
    title.textContent =
        current.method + " " + current.endpoint;

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
}


function closeFlowchartModal() {
    const modal =
        document.getElementById("flowchartModal");
    if (!modal) {
        return;
    }

    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
}


function downloadCurrentFlowchartSvg() {
    const current = window.currentFlowchart;
    if (!current || !current.svg) {
        return;
    }

    const blob = new Blob(
        [current.svg],
        { type: "image/svg+xml;charset=utf-8" }
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const safeEndpoint = current.endpoint
        .replace(/[^a-zA-Z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .toLowerCase();

    link.href = url;
    link.download =
        "flowchart-"
        + current.method.toLowerCase()
        + "-"
        + (safeEndpoint || "endpoint")
        + ".svg";

    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}


document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
        closeFlowchartModal();
    }
});


function renderError(
    message
) {

    return `
        <div class="danger">
            ${escapeHtml(
                message
            )}
        </div>
    `;
}


function escapeHtml(
    value
) {

    if (
        value === null
        ||
        value === undefined
    ) {

        return "";
    }

    return String(
        value
    )
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            '"',
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );
}

function populateVersionCompare(history) {
    const panel = document.getElementById("versionComparePanel");
    const fromSelect = document.getElementById("compareFromVersion");
    const toSelect = document.getElementById("compareToVersion");
    if (!panel || !fromSelect || !toSelect) return;

    if (!Array.isArray(history) || history.length < 2) {
        panel.classList.add("hidden");
        return;
    }
    const versions = [...history].sort((a,b) => a.baseline_version - b.baseline_version);
    const options = versions.map(v => `<option value="${v.baseline_version}">V${v.baseline_version}</option>`).join("");
    fromSelect.innerHTML = options;
    toSelect.innerHTML = options;
    fromSelect.value = String(versions[0].baseline_version);
    toSelect.value = String(versions[versions.length - 1].baseline_version);
    panel.classList.remove("hidden");
    document.getElementById("versionCompareResult").innerHTML = "";
}

async function compareBaselineVersions() {
    const scenarioId = document.getElementById("baselineScenarioSelect")?.value;
    const fromVersion = document.getElementById("compareFromVersion")?.value;
    const toVersion = document.getElementById("compareToVersion")?.value;
    const result = document.getElementById("versionCompareResult");
    if (!scenarioId || !fromVersion || !toVersion) return;
    if (fromVersion === toVersion) {
        result.innerHTML = `<div class="warning">Choose two different versions.</div>`;
        return;
    }
    result.innerHTML = "Comparing versions...";
    try {
        const response = await fetch(`/api/scenario-baselines/compare/${scenarioId}?from_version=${fromVersion}&to_version=${toVersion}`);
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        result.innerHTML = renderVersionComparison(data);
    } catch (error) {
        result.innerHTML = `<div class="error">Version comparison failed: ${escapeHtml(error.message)}</div>`;
    }
}

function renderVersionComparison(data) {
    const methodSection = (title, items, cls) => `
        <div class="compare-section">
            <h4>${title} <span class="count-pill">${items.length}</span></h4>
            ${items.length ? items.map(x => `<div class="compare-item ${cls}">${escapeHtml(x)}</div>`).join("") : `<div class="muted-text">None</div>`}
        </div>`;

    const response = data.response_changes || {};
    const attributeRows = [
        ...(response.added_attributes || []).map(x => `<div class="compare-item added">+ ${escapeHtml(x.path)} <span>${escapeHtml(String(x.value))}</span></div>`),
        ...(response.removed_attributes || []).map(x => `<div class="compare-item removed">− ${escapeHtml(x.path)} <span>${escapeHtml(String(x.value))}</span></div>`),
        ...(response.changed_attributes || []).map(x => `<div class="compare-item changed">~ ${escapeHtml(x.path)} <span>${escapeHtml(String(x.from))} → ${escapeHtml(String(x.to))}</span></div>`)
    ];

    return `
        <div class="version-compare-summary">
            <div><strong>${escapeHtml(data.scenario_code)}</strong></div>
            <div class="version-badges"><span>V${data.from_version}</span><b>→</b><span>V${data.to_version}</span></div>
            <div class="muted-text">${escapeHtml(data.from_endpoint)}${data.endpoint_changed ? ` → ${escapeHtml(data.to_endpoint)}` : ""}</div>
        </div>
        ${(data.source_comparison && data.source_comparison.project_mismatch) ? `
            <div class="warning">These versions were captured from different Java project paths. Flow/dependency differences are shown, but they are not source file additions/deletions.</div>
        ` : ``}
        <div class="compare-section">
            <h4>Response / Attribute Changes <span class="count-pill">${attributeRows.length}</span></h4>
            ${attributeRows.length ? attributeRows.join("") : `<div class="muted-text">No stored response attribute differences.</div>`}
        </div>
        ${((data.added_methods || []).length || (data.removed_methods || []).length || (data.added_classes || []).length || (data.removed_classes || []).length) ? `
            <details class="technical-details">
                <summary>Technical baseline differences</summary>
                <div class="compare-note muted-text">These are stored execution-flow/dependency differences, not proof that Java source files were created or deleted.</div>
                <div class="compare-grid">
                    ${methodSection("Methods entered execution flow", data.added_methods || [], "added")}
                    ${methodSection("Methods left execution flow", data.removed_methods || [], "removed")}
                    ${methodSection("Classes entered scenario dependency set", data.added_classes || [], "added")}
                    ${methodSection("Classes left scenario dependency set", data.removed_classes || [], "removed")}
                </div>
            </details>
        ` : ``}
    `;
}


// PHASE2_ACCORDION_BUILTIN_V2
// Built into app.js so accordions keep working even if the optional accordion-ui files are stale/missed.
(function () {
    function setAccordionState(card, collapsed) {
        const header = card.querySelector('.accordion-header');
        const body = card.querySelector('.accordion-body');
        const toggle = header ? header.querySelector('.accordion-toggle') : null;
        card.classList.toggle('is-collapsed', collapsed);
        if (body) body.style.display = collapsed ? 'none' : '';
        if (header) header.setAttribute('aria-expanded', String(!collapsed));
        if (toggle) toggle.textContent = collapsed ? '›' : '⌄';
    }

    function initCodeIntelligenceAccordions() {
        const cards = Array.from(document.querySelectorAll('.accordion-card'));
        cards.forEach((card, index) => {
            const header = card.querySelector('.accordion-header');
            if (!header || header.dataset.accordionBound === 'true') return;
            header.dataset.accordionBound = 'true';

            // JIRA is open initially. Other major cards start collapsed.
            const shouldCollapse = card.id !== 'jiraImpactCard';
            setAccordionState(card, shouldCollapse);

            const toggle = header.querySelector('.accordion-toggle');
            const doToggle = (event) => {
                if (event) {
                    const interactive = event.target.closest('button:not(.accordion-toggle), a, input, select, textarea, label');
                    if (interactive) return;
                }
                setAccordionState(card, !card.classList.contains('is-collapsed'));
            };

            header.addEventListener('click', doToggle);
            header.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    doToggle(event);
                }
            });
            if (toggle) {
                toggle.addEventListener('click', (event) => {
                    event.stopPropagation();
                    setAccordionState(card, !card.classList.contains('is-collapsed'));
                });
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCodeIntelligenceAccordions);
    } else {
        initCodeIntelligenceAccordions();
    }
})();


async function runScenarioRagEvaluation() {
    const result = document.getElementById("scenarioRagRegistryResult");
    if (!result) {
        alert("Scenario RAG result panel was not found.");
        return;
    }
    result.innerHTML = `<div class="muted-box">Running automated Scenario RAG evaluation...</div>`;
    try {
        const response = await fetch("/api/scenario-rag-registry/evaluate", {method: "POST"});
        const raw = await response.text();
        let data = {};
        try {
            data = raw ? JSON.parse(raw) : {};
        } catch (_) {
            if (!response.ok) {
                throw new Error(raw || `HTTP ${response.status}`);
            }
            throw new Error("Evaluation endpoint returned an invalid response.");
        }
        if (!response.ok) {
            const detail = data?.detail;
            throw new Error(typeof detail === "object" ? JSON.stringify(detail) : (detail || `HTTP ${response.status}`));
        }

        const cases = Array.isArray(data.cases) ? data.cases : [];
        result.innerHTML = `
            <div class="scenario-rag-card">
                <div class="scenario-rag-card-header">
                    <div>
                        <strong>Automated RAG Evaluation</strong>
                        <div class="muted-text">Positive and negative regression checks using captured Scenario Registry evidence.</div>
                    </div>
                    <span class="tag">${escapeHtml(data.status || "")}</span>
                </div>
                <div style="display:flex;gap:18px;flex-wrap:wrap;margin:12px 0;">
                    <div><strong>${Number(data.accuracy_percent || 0).toFixed(2)}%</strong><div class="muted-text">Accuracy</div></div>
                    <div><strong>${data.passed || 0}</strong><div class="muted-text">Passed</div></div>
                    <div><strong>${data.failed || 0}</strong><div class="muted-text">Failed</div></div>
                    <div><strong>${data.total || 0}</strong><div class="muted-text">Total</div></div>
                    <div><strong>${data.positive?.total || 0}</strong><div class="muted-text">Positive Tests</div></div>
                    <div><strong>${data.negative?.total || 0}</strong><div class="muted-text">Negative Tests</div></div>
                </div>
                <div style="display:flex;gap:18px;flex-wrap:wrap;margin:12px 0;padding:10px 0;border-top:1px solid #d9e2ef;border-bottom:1px solid #d9e2ef;">
                    <div><strong>${(Number(data.precision_at_k || 0) * 100).toFixed(2)}%</strong><div class="muted-text">Precision@${data.top_k || 8}</div></div>
                    <div><strong>${(Number(data.recall_at_k || 0) * 100).toFixed(2)}%</strong><div class="muted-text">Recall@${data.top_k || 8}</div></div>
                    <div><strong>${Number(data.mrr || 0).toFixed(4)}</strong><div class="muted-text">MRR</div></div>
                    <div><strong>${Number(data.hit_at_k_percent || 0).toFixed(2)}%</strong><div class="muted-text">Hit@${data.top_k || 8}</div></div>
                    <div><strong>${data.irrelevant_result_count || 0}</strong><div class="muted-text">Irrelevant Results</div></div>
                    <div><strong>${Number(data.average_latency_ms || 0).toFixed(2)} ms</strong><div class="muted-text">Avg Latency</div></div>
                </div>
                ${cases.map(item => `
                    <div class="attribute-impact-row">
                        <div style="display:flex;justify-content:space-between;gap:12px;">
                            <strong>${escapeHtml(item.query || "")}</strong>
                            <span class="tag">${item.passed ? "PASS" : "FAIL"}</span>
                        </div>
                        <div class="muted-text">
                            ${escapeHtml(item.case_type || "")}
                            ${item.expected_jira ? ` · Expected JIRA ${escapeHtml(item.expected_jira)}` : " · Expected NO EVIDENCE"}
                            ${item.expected_test_baseline ? ` · ${escapeHtml(item.expected_test_baseline)}` : ""}
                        </div>
                        <div class="muted-text" style="margin-top:4px;">
                            ${item.case_type === "POSITIVE" ? `P@${data.top_k || 8}: ${(Number(item.precision_at_k || 0) * 100).toFixed(1)}% · R@${data.top_k || 8}: ${(Number(item.recall_at_k || 0) * 100).toFixed(1)}% · RR: ${Number(item.reciprocal_rank || 0).toFixed(3)} · ` : ""}
                            Irrelevant: ${item.irrelevant_result_count || 0} · Latency: ${Number(item.latency_ms || 0).toFixed(2)} ms
                        </div>
                        ${!item.passed && item.actual_evidence?.length ? `
                            <details style="margin-top:6px;">
                                <summary>Actual evidence</summary>
                                <pre>${escapeHtml(JSON.stringify(item.actual_evidence, null, 2))}</pre>
                            </details>` : ""}
                    </div>
                `).join("")}
            </div>`;
    } catch (error) {
        result.innerHTML = renderError(error.message);
    }
}

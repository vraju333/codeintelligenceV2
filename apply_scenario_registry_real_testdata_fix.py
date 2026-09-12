from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
JS = ROOT / "static" / "project-ui.js"
HTML = ROOT / "templates" / "index.html"

for path in (JS, HTML):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run this patch from the CodeIntelligence project root.")

s = JS.read_text(encoding="utf-8")

if "let scenarioRegistryScenarioMap = new Map();" not in s:
    marker = "async function loadScenarios(page = scenarioPage) {"
    if marker not in s:
        raise SystemExit("Could not find loadScenarios() in static/project-ui.js")
    s = s.replace(marker, "let scenarioRegistryScenarioMap = new Map();\n\n" + marker, 1)

load_marker = "const scenarios = data.items || [];"
idx = s.find(load_marker)
if idx >= 0 and "scenarioRegistryScenarioMap = new Map(" not in s[idx:idx+700]:
    replacement = (
        "const scenarios = data.items || [];\n"
        "        scenarioRegistryScenarioMap = new Map(\n"
        "            scenarios.map(item => [String(item.scenario_code || \"\"), item])\n"
        "        );"
    )
    s = s.replace(load_marker, replacement, 1)

start = s.find("function toggleScenarioTestData(")
end = s.find("function renderScenarioPager", start)
if start < 0 or end < 0:
    raise SystemExit("Could not find toggleScenarioTestData()/renderScenarioPager() in project-ui.js")

new_toggle = r'''function parseStoredScenarioJson(rawValue) {
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

    let input = parseStoredScenarioJson(scenario.request_json);
    let expected = parseStoredScenarioJson(scenario.expected_response_json);

    let generated = null;
    if (input === null || expected === null) {
        generated = await loadGeneratedScenarioTestData(scenario);
        if (input === null && generated?.request_json !== undefined) {
            input = generated.request_json;
        }
        if (expected === null && generated?.expected_response_json !== undefined) {
            expected = generated.expected_response_json;
        }
    }

    const actual = parseStoredScenarioJson(
        scenario.actual_response_json ?? scenario.actual_json ?? scenario.output_json ?? null
    );

    const usedStoredData = !!(
        String(scenario.request_json || "").trim() ||
        String(scenario.expected_response_json || "").trim()
    );
    const sourceText = usedStoredData
        ? "Saved scenario data"
        : (generated ? "Generated from the selected endpoint model" : "No test data available");

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

'''

s = s[:start] + new_toggle + s[end:]
JS.write_text(s, encoding="utf-8")

h = HTML.read_text(encoding="utf-8")
h = re.sub(r'/static/project-ui\.js\?v=[^"\']+', '/static/project-ui.js?v=scenario-real-testdata-1', h)
HTML.write_text(h, encoding="utf-8")

style = ROOT / "static" / "style.css"
if style.exists():
    css = style.read_text(encoding="utf-8")
    if ".scenario-test-data-empty" not in css:
        css += """

/* Scenario Registry: real test-data empty state */
.scenario-test-data-empty {
    min-height: 96px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 18px;
    border: 1px dashed #cbd5e1;
    border-radius: 10px;
    background: #f8fafc;
    color: #64748b;
    text-align: center;
}
"""
        style.write_text(css, encoding="utf-8")

print("Scenario Registry real test-data fix applied successfully.")
print("Restart the service and hard refresh the browser with Ctrl+F5.")

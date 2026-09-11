
/*
 * Scenario Flowchart readable renderer.
 * This file is loaded AFTER app.js and deliberately overrides only
 * generateFlowchart/openFlowchartModal. It leaves all other UI fixes intact.
 */

function ciFlowLabel(node) {
    return String(node?.label || node?.name || "Unknown")
        .replace(/\\n/g, " · ")
        .replace(/\s+/g, " ")
        .trim();
}

function ciFlowKind(label) {
    const value = String(label || "").toLowerCase();
    if (value.includes("controller")) return "controller";
    if (value.includes("repository")) return "repository";
    if (value.includes("mapper")) return "mapper";
    if (value.includes("service")) return "service";
    return "code";
}


function ciFlowInputText(node) {
    const params = Array.isArray(node?.input_parameters) ? node.input_parameters : [];
    if (!params.length) return "Nothing";
    return params.map(param => {
        if (typeof param === "string") return param;
        return param?.display || [param?.type, param?.name].filter(Boolean).join(" ") || "parameter";
    }).join(", ");
}

function ciFlowOutputText(node) {
    const raw = String(node?.return_type || "").trim();
    if (!raw) return "Nothing";

    // Keep the hover focused on the actual Java type, not declaration
    // modifiers such as public/private/protected/static/final.
    const cleaned = raw
        .replace(/^(?:(?:public|protected|private|abstract|default|static|final|synchronized|native|strictfp)\s+)+/i, "")
        .trim();

    if (!cleaned || cleaned.toLowerCase() === "void") return "Nothing";
    return cleaned;
}

function ciFlowFileText(node) {
    const value = String(node?.file_path || "");
    if (!value) return "";
    return value.replace(/\\/g, "/").split("/").pop();
}

function ciBuildGraph(nodes, edges) {
    const byId = new Map((nodes || []).map(node => [String(node.id), node]));
    const children = new Map();
    const incoming = new Map();

    for (const edge of (edges || [])) {
        const from = String(edge.from);
        const to = String(edge.to);
        if (!children.has(from)) children.set(from, []);
        children.get(from).push(to);
        incoming.set(to, (incoming.get(to) || 0) + 1);
    }

    const roots = (nodes || [])
        .map(node => String(node.id))
        .filter(id => !incoming.has(id));

    return {
        byId,
        children,
        roots: roots.length
            ? roots
            : ((nodes || [])[0] ? [String(nodes[0].id)] : [])
    };
}

function ciRenderFlowNode(id, graph, ancestry = new Set()) {
    if (ancestry.has(id)) return "";

    const node = graph.byId.get(id);
    if (!node) return "";

    const label = ciFlowLabel(node);
    const kind = ciFlowKind(label);
    const nextAncestry = new Set(ancestry);
    nextAncestry.add(id);

    const childIds = [...new Set(graph.children.get(id) || [])];

    return `
        <div class="ci-flow-tree-node">
            <div class="ci-flow-card ci-flow-${kind}" tabindex="0">
                <span>${escapeHtml(kind)}</span>
                <strong>${escapeHtml(label)}</strong>
                <div class="ci-flow-tooltip" role="tooltip">
                    <div class="ci-flow-tooltip-title">${escapeHtml(node?.class_name || "")}\.${escapeHtml(node?.method_name || "")}</div>
                    <div class="ci-flow-tooltip-row"><b>Input</b><code>${escapeHtml(ciFlowInputText(node))}</code></div>
                    <div class="ci-flow-tooltip-row"><b>Output</b><code>${escapeHtml(ciFlowOutputText(node))}</code></div>
                    ${ciFlowFileText(node) ? `<div class="ci-flow-tooltip-row"><b>File</b><code>${escapeHtml(ciFlowFileText(node))}</code></div>` : ""}
                </div>
            </div>
            ${childIds.length ? `
                <div class="ci-flow-arrow">↓</div>
                <div class="ci-flow-children ${childIds.length > 1 ? "ci-flow-branches" : ""}">
                    ${childIds.map(childId =>
                        `<div class="ci-flow-branch">${ciRenderFlowNode(childId, graph, nextAncestry)}</div>`
                    ).join("")}
                </div>
            ` : ""}
        </div>
    `;
}

function ciRenderReadableFlow(nodes, edges, large = false) {
    if (!Array.isArray(nodes) || !nodes.length) {
        return `<div class="muted-box">No execution steps found.</div>`;
    }

    const graph = ciBuildGraph(nodes, edges);

    return `
        <div class="ci-readable-flow ${large ? "ci-readable-flow-large" : ""}">
            ${graph.roots.map(rootId =>
                `<div class="ci-flow-root">${ciRenderFlowNode(rootId, graph)}</div>`
            ).join("")}
        </div>
    `;
}

async function generateFlowchart() {
    const method = document.getElementById("chartMethod").value;
    const endpoint = document.getElementById("chartEndpoint").value;
    const container = document.getElementById("flowchartResult");

    if (!endpoint) {
        container.innerHTML = renderError("Please select an endpoint.");
        return;
    }

    container.innerHTML = "Generating flowchart...";

    try {
        const response = await fetch(
            "/api/reports/flowchart"
            + "?http_method=" + encodeURIComponent(method)
            + "&endpoint=" + encodeURIComponent(endpoint)
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || JSON.stringify(data));
        }

        const nodes = Array.isArray(data.nodes) ? data.nodes : [];
        const edges = Array.isArray(data.edges) ? data.edges : [];

        // Keep Mermaid only for Download SVG/debug. Never use it for visible UI.
        let svg = "";
        if (data.mermaid && window.mermaid) {
            try {
                const rendered = await window.mermaid.render(
                    "mermaid-download-" + Date.now(),
                    data.mermaid
                );
                svg = rendered.svg;
            } catch (_) {}
        }

        window.currentFlowchart = {
            method,
            endpoint,
            nodes,
            edges,
            mermaid: data.mermaid || "",
            svg
        };

        container.innerHTML = `
            <div class="flowchart-toolbar">
                <div>
                    <div class="flowchart-caption">
                        ${escapeHtml(method)} ${escapeHtml(endpoint)}
                    </div>
                    <div class="flowchart-meta">
                        ${nodes.length} steps · ${edges.length} calls
                    </div>
                </div>
                <div class="flowchart-actions">
                    <button type="button" onclick="openFlowchartModal()">Open Large View</button>
                    <button type="button"
                            onclick="downloadCurrentFlowchartSvg()"
                            ${svg ? "" : "disabled"}>
                        Download SVG
                    </button>
                </div>
            </div>

            <div class="ci-flow-window">
                ${ciRenderReadableFlow(nodes, edges, false)}
            </div>
        `;

        const source = document.getElementById("flowchartMermaidSource");
        if (source) source.textContent = data.mermaid || "";

    } catch (error) {
        container.innerHTML = renderError(error.message);
    }
}

function openFlowchartModal() {
    const current = window.currentFlowchart;
    if (!current) return;

    const modal = document.getElementById("flowchartModal");
    const body = document.getElementById("flowchartModalBody");
    const title = document.getElementById("flowchartModalTitle");

    title.textContent = current.method + " " + current.endpoint;
    body.innerHTML = `
        <div class="ci-flow-window ci-flow-window-large">
            ${ciRenderReadableFlow(current.nodes, current.edges, true)}
        </div>
    `;

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
}

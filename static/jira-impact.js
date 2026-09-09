async function analyseJiraImpact() {
    const jiraId = document.getElementById('jiraImpactId')?.value?.trim() || null;
    const requirement = document.getElementById('jiraImpactRequirement')?.value?.trim() || '';
    const result = document.getElementById('jiraImpactResult');

    if (!requirement) {
        result.innerHTML = '<div class="jira-impact-error">Enter a requirement / Jira description first.</div>';
        return;
    }

    result.innerHTML = '<div class="jira-impact-loading">Analysing selected Java project...</div>';

    try {
        const response = await fetch('/api/jira-impact/analyze', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({jira_id: jiraId, requirement})
        });
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || 'JIRA impact analysis failed');
        }
        renderJiraImpact(payload);
    } catch (error) {
        result.innerHTML = `<div class="jira-impact-error">${escapeJiraImpact(String(error.message || error))}</div>`;
    }
}

function renderJiraImpact(data) {
    const result = document.getElementById('jiraImpactResult');
    const understanding = data.requirement_understanding || {};
    const files = data.likely_files || [];
    const endpoints = data.affected_endpoints || [];
    const scenarios = data.affected_scenarios || [];
    const paths = data.dependency_paths || [];
    const confidence = data.confidence || {};
    const analysisBasis = data.analysis_basis || {};
    const projectMatch = data.project_match || {};
    const warnings = data.warnings || [];

    const chips = (understanding.concepts || []).map(item => `<span class="jira-chip">${escapeJiraImpact(item)}</span>`).join('');
    const intents = (understanding.intents || []).map(item => `<span class="jira-chip jira-chip-muted">${escapeJiraImpact(item)}</span>`).join('');
    const roles = [
        ['Action', understanding.action],
        ['Attribute', understanding.attribute],
        ['Parent', understanding.parent],
        ['Entity', understanding.entity],
        ['Condition', understanding.condition],
        ['Outcome', understanding.desired_behavior],
    ].filter(([, value]) => value).map(([label, value]) =>
        `<span class="jira-chip"><strong>${escapeJiraImpact(label)}:</strong>&nbsp;${escapeJiraImpact(value)}</span>`
    ).join('');

    const filesHtml = files.length ? files.map(file => `
        <div class="jira-impact-item">
            <div class="jira-impact-item-title">${escapeJiraImpact(file.relative_path || file.file_name)}</div>
            <div class="jira-impact-item-meta">${escapeJiraImpact(file.class_name || '')} · evidence score ${file.score}</div>
            ${(file.reasons || []).map(reason => `<div class="jira-impact-reason">• ${escapeJiraImpact(reason)}</div>`).join('')}
        </div>`).join('') : '<div class="jira-impact-empty">No strong file candidates found.</div>';

    const endpointsHtml = endpoints.length ? endpoints.map(item => `
        <div class="jira-impact-item">
            <div class="jira-impact-item-title"><span class="jira-method">${escapeJiraImpact(item.http_method)}</span> ${escapeJiraImpact(item.endpoint)}</div>
            <div class="jira-impact-item-meta">${escapeJiraImpact(item.controller || '')}.${escapeJiraImpact(item.method_name || '')}</div>
            ${(item.reasons || []).map(reason => `<div class="jira-impact-reason">• ${escapeJiraImpact(reason)}</div>`).join('')}
        </div>`).join('') : '<div class="jira-impact-empty">No endpoint impact found from current evidence.</div>';

    const scenariosHtml = scenarios.length ? scenarios.map(item => `
        <div class="jira-impact-item">
            <div class="jira-impact-item-title">${escapeJiraImpact(item.scenario_code)}</div>
            <div class="jira-impact-item-meta">${escapeJiraImpact(item.http_method)} ${escapeJiraImpact(item.endpoint)}</div>
            ${(item.reasons || []).map(reason => `<div class="jira-impact-reason">• ${escapeJiraImpact(reason)}</div>`).join('')}
        </div>`).join('') : '<div class="jira-impact-empty">No registered scenario matched the current evidence.</div>';


    const warningHtml = warnings.length ? `
        <div class="jira-impact-error">
            ${warnings.map(item => `<div>⚠ ${escapeJiraImpact(item)}</div>`).join('')}
        </div>` : '';

    const llmNote = analysisBasis.llm_used
        ? 'Hybrid analysis · LLM used only for Jira requirement understanding · Java source analysis remains local'
        : (analysisBasis.llm_error
            ? `Local fallback parser used · LLM unavailable: ${escapeJiraImpact(analysisBasis.llm_error)}`
            : 'Local requirement parser used · Java source analysis remains local');

    const pathsHtml = paths.length ? paths.map(item => `
        <div class="jira-path-row">
            ${(item.path || []).map((node, index) => `${index ? '<span class="jira-path-arrow">→</span>' : ''}<span class="jira-path-node">${escapeJiraImpact(node)}</span>`).join('')}
        </div>`).join('') : '<div class="jira-impact-empty">Dependency path will appear when endpoint flow intersects the impacted code.</div>';

    result.innerHTML = `
        <div class="jira-impact-summary">
            <div>
                <div class="jira-impact-kicker">${escapeJiraImpact(data.jira_id || 'Manual requirement')}</div>
                <div class="jira-impact-project">${escapeJiraImpact(data.project_path || '')}</div>
            </div>
            <div class="jira-confidence jira-confidence-${String(confidence.level || 'LOW').toLowerCase()}">
                ${escapeJiraImpact(confidence.level || 'LOW')} · ${Number(confidence.score || 0)}%
            </div>
        </div>

        ${warningHtml}

        <div class="jira-impact-block">
            <h3>Requirement understanding</h3>
            <div class="jira-chip-row">${roles || '<span class="jira-impact-empty">No structured requirement roles detected.</span>'}</div>
            <div class="jira-chip-row">${chips || '<span class="jira-impact-empty">No explicit code-style concepts detected.</span>'}</div>
            <div class="jira-chip-row">${intents}</div>
            <div class="jira-impact-note">${llmNote}</div>
        </div>

        <div class="jira-impact-grid">
            <div class="jira-impact-block"><h3>Likely files to inspect / modify</h3>${filesHtml}</div>
            <div class="jira-impact-block"><h3>Affected endpoints</h3>${endpointsHtml}</div>
        </div>

        <div class="jira-impact-grid">
            <div class="jira-impact-block"><h3>Affected scenarios</h3>${scenariosHtml}</div>
            <div class="jira-impact-block"><h3>Why affected?</h3>${pathsHtml}</div>
        </div>
    `;
}

function escapeJiraImpact(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

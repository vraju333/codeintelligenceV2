const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');
const context = vm.createContext({escapeHtml: value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/version-source-history.js'), 'utf8'), context);
const source = {snapshot_status: 'PARTIAL', message: 'Partial evidence', unverified_files: ['B.java'],
    changed_files: [{file_path: 'src/A.java', status: 'MODIFIED', diff: '@@ -1 +1 @@\n-old\n+<script>unsafe()</script>'}]};
const html = context.renderReleaseVersionComparison({source_comparison: source});
assert(html.includes('src/A.java'));
assert(html.includes('baseline-file-diff'));
assert(html.includes('diff-added'));
assert(html.includes('diff-removed'));
assert(html.includes('&lt;script&gt;'));
assert(!html.includes('<script>'));
assert(html.includes('INCOMPLETE EVIDENCE'));
assert(html.includes('B.java'));
assert(html.includes('Partial evidence'));
const risk = context.renderBaselineRisk({risk_level: 'UNKNOWN', score: 20, candidate_files: [],
    test_counts: {PASS: 0, FAIL: 0}, reasons: ['Missing evidence'], recommendations: ['Review']});
assert(risk.includes('UNKNOWN'));
assert(risk.includes('Download report JSON'));
console.log('JavaScript rendering, source escaping, diff classes, unknown evidence, and report control checks passed.');

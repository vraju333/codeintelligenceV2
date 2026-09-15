const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {chromium} = require(require.resolve('playwright', {
    paths: [process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || process.cwd()]
}));

(async () => {
    const root = path.resolve(__dirname, '..');
    const browser = await chromium.launch({headless: true});
    try {
        for (const width of [1280, 390]) {
            const page = await browser.newPage({viewport: {width, height: 900}});
            await page.setContent('<html><head></head><body><main id="report"></main></body></html>');
            await page.addStyleTag({content: 'body{font-family:Arial;margin:16px}main{max-width:100%;min-width:0}*{box-sizing:border-box}'});
            await page.addStyleTag({content: fs.readFileSync(path.join(root, 'static/version-source-history.css'), 'utf8')});
            await page.evaluate(() => {
                window.escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            });
            await page.addScriptTag({content: fs.readFileSync(path.join(root, 'static/version-source-history.js'), 'utf8')});
            await page.evaluate(() => {
                const file = {status: 'MODIFIED', file_path: 'src/main/java/com/example/studentemployee/entity/Student.java',
                    diff: '--- a/Student.java\n+++ b/Student.java\n@@ -1 +1 @@\n-private int age;\n+private int birthYear;\n+// <script>alert(1)</script>'};
                const data = {scenario_id: 1, from_version: 2, to_version: 3, scenario_code: 'STUDENT_ADD',
                    source_comparison: {snapshot_status: 'AVAILABLE', changed_files: [file]},
                    scenario_impact: {changed: true}, change_summary: {changed_source_files: 1},
                    risk_report: {risk_level: 'HIGH', score: 60, scope: 'Selected scenario',
                        candidate_files: [file], reasons: ['Changed stored dependency'],
                        recommendations: ['Review test evidence'], test_counts: {PASS: 0, FAIL: 1}, limitations: 'Heuristic review indicator.'}};
                latestBaselineReport = data;
                document.getElementById('report').innerHTML = renderReleaseVersionComparison(data);
            });
            await page.locator('.baseline-file-diff summary').click();
            assert(await page.locator('.baseline-diff-code').isVisible());
            assert.strictEqual(await page.locator('#report script').count(), 0);
            assert(await page.locator('.diff-added').count() > 0);
            assert(await page.locator('.diff-removed').count() > 0);
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
            assert.strictEqual(overflow, false, `horizontal overflow at ${width}px`);
            const downloadPromise = page.waitForEvent('download');
            await page.getByRole('button', {name: 'Download report JSON'}).click();
            const download = await downloadPromise;
            assert.strictEqual(download.suggestedFilename(), 'baseline-1-2-to-3.json');
            await page.screenshot({path: path.join(root, `baseline-review-${width}.png`), fullPage: true});
            await page.close();
        }
        console.log('Desktop/mobile rendering, line diff expansion, escaping, and JSON download passed.');
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});

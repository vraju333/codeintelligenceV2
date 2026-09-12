from pathlib import Path
import re

root = Path(__file__).resolve().parent
html = root / 'templates' / 'index.html'
js = root / 'static' / 'project-ui.js'

if not html.exists() or not js.exists():
    raise SystemExit('Run/copy this patch from the CodeIntelligence project root so templates/index.html and static/project-ui.js exist.')

# 1) Defect JSON areas should start blank. Data must come from selected scenario/manual input.
text = html.read_text(encoding='utf-8')
for field_id in ('inputJson', 'expectedJson', 'actualJson'):
    pattern = rf'(<textarea\s+id=["\']{field_id}["\'][^>]*>)(.*?)(</textarea>)'
    text, count = re.subn(pattern, rf'\1\3', text, count=1, flags=re.S)
    if count == 0:
        print(f'Warning: textarea {field_id} not found in templates/index.html')
html.write_text(text, encoding='utf-8')

# 2) If scenario selection is cleared / there are no scenarios, clear any stale values.
text = js.read_text(encoding='utf-8')
needle = '''    if (!select?.value) {
        const context = document.getElementById("defectScenarioContext");'''
replacement = '''    if (!select?.value) {
        ["inputJson", "expectedJson", "actualJson"].forEach(id => {
            const field = document.getElementById(id);
            if (field) field.value = "";
        });
        const context = document.getElementById("defectScenarioContext");'''
if replacement not in text:
    if needle in text:
        text = text.replace(needle, replacement, 1)
    else:
        print('Warning: applyDefectScenario empty-selection block not found; template defaults were still removed.')

# Also clear immediately after loading zero scenarios, so project switches never keep old data.
needle2 = '''        if (context) {
            context.textContent = activeProjectScenarios.length
                ? `${activeProjectScenarios.length} scenario(s) available for the selected project.`
                : "No registered scenarios are available for the selected project.";
        }'''
replacement2 = '''        if (context) {
            context.textContent = activeProjectScenarios.length
                ? `${activeProjectScenarios.length} scenario(s) available for the selected project.`
                : "No registered scenarios are available for the selected project.";
        }
        if (!activeProjectScenarios.length) {
            ["inputJson", "expectedJson", "actualJson"].forEach(id => {
                const field = document.getElementById(id);
                if (field) field.value = "";
            });
        }'''
if replacement2 not in text and needle2 in text:
    text = text.replace(needle2, replacement2, 1)

js.write_text(text, encoding='utf-8')
print('Applied: defect JSON fields are blank until a project-scoped scenario is selected or the user enters manual data.')

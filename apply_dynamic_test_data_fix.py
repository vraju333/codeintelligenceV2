from pathlib import Path
ROOT=Path(__file__).resolve().parent
router=ROOT/'routers'/'endpoint_flow_router.py'
js=ROOT/'static'/'project-ui.js'
html=ROOT/'templates'/'index.html'
for p in (router,js,html):
    if not p.exists(): raise SystemExit(f'Missing {p}. Extract/run from CodeIntelligence V2 root.')
# router
s=router.read_text(encoding='utf-8')
imp='from services.scenario.java_sample_data_service import JavaSampleDataService\n'
if imp not in s:
    marker='router = APIRouter('
    pos=s.find(marker)
    if pos<0: raise SystemExit('Could not patch endpoint_flow_router.py')
    s=s[:pos]+imp+'\n'+s[pos:]
if '@router.get("/sample-data")' not in s:
    s += '''\n\n@router.get("/sample-data")\ndef get_sample_data(http_method: str = Query(...), endpoint: str = Query(...)):\n    try:\n        return JavaSampleDataService().generate(http_method=http_method, endpoint=endpoint)\n    except RuntimeError as e:\n        raise HTTPException(status_code=404, detail=str(e))\n    except Exception as e:\n        raise HTTPException(status_code=500, detail=str(e))\n'''
router.write_text(s,encoding='utf-8')
# JS - put implementation in project-ui.js so it is guaranteed to run with modal flow
s=js.read_text(encoding='utf-8')
if 'async function populateScenarioTestData()' not in s:
    anchor='async function checkExistingOperationScenario() {'
    fn=r'''async function populateScenarioTestData() {
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

'''
    if anchor not in s: raise SystemExit('Could not find checkExistingOperationScenario in project-ui.js')
    s=s.replace(anchor,fn+anchor,1)
# Call it directly from actual endpoint-selection flow
needle='''        suggestScenarioIdentity(method, endpoint);\n        button.disabled = false;'''
if needle in s and 'await populateScenarioTestData();' not in s[s.find(needle):s.find(needle)+200]:
    s=s.replace(needle,needle+'\n        await populateScenarioTestData();',1)
# method changes rebuild endpoints: clear old test data
needle2='''function refreshNewScenarioEndpoints() {'''
if needle2 in s and '/* dynamic-test-data-clear */' not in s:
    s=s.replace(needle2,needle2+'''\n    /* dynamic-test-data-clear */\n    ["newScenarioRequestJson", "newScenarioExpectedResponse", "newScenarioExpectedDbEffect"].forEach(id => {\n        const el = document.getElementById(id); if (el) el.value = "";\n    });''',1)
js.write_text(s,encoding='utf-8')
# Remove misleading Java-specific placeholder from test-data fields
s=html.read_text(encoding='utf-8')
s=s.replace("placeholder='{\"primaryContact\":\"9876543210\",\"secondaryContact\":\"9123456780\"}'", "placeholder='Select an endpoint to generate request data'")
# bump browser cache for project-ui
import re
s=re.sub(r'/static/project-ui\.js\?v=[^"\']+', '/static/project-ui.js?v=dynamic-testdata-2', s)
html.write_text(s,encoding='utf-8')
print('V2 dynamic scenario test-data FIX applied. Restart V2 and hard-refresh browser (Ctrl+F5).')

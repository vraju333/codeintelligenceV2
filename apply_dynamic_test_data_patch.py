from pathlib import Path

ROOT = Path(__file__).resolve().parent
router = ROOT / "routers" / "endpoint_flow_router.py"
template = ROOT / "templates" / "index.html"

if not router.exists() or not template.exists():
    raise SystemExit("Run/extract this patch in the codeintelligenceV2 project root.")

text = router.read_text(encoding="utf-8")
import_line = "from services.scenario.java_sample_data_service import JavaSampleDataService\n"
if import_line not in text:
    anchor = "from services.flow.endpoint_flow_service import (\n    EndpointFlowService\n)\n"
    if anchor not in text:
        raise SystemExit("Could not find EndpointFlowService import in routers/endpoint_flow_router.py")
    text = text.replace(anchor, anchor + import_line, 1)

if '@router.get("/sample-data")' not in text:
    text += '''\n\n@router.get("/sample-data")\ndef get_sample_data(\n    http_method: str = Query(...),\n    endpoint: str = Query(...)\n):\n    try:\n        return JavaSampleDataService().generate(\n            http_method=http_method,\n            endpoint=endpoint\n        )\n    except RuntimeError as exception:\n        raise HTTPException(status_code=404, detail=str(exception))\n    except Exception as exception:\n        raise HTTPException(status_code=500, detail=str(exception))\n'''
router.write_text(text, encoding="utf-8")

html = template.read_text(encoding="utf-8")
script = '<script src="/static/scenario-test-data.js?v=1"></script>'
if script not in html:
    marker = "</body>"
    if marker not in html:
        raise SystemExit("Could not find </body> in templates/index.html")
    html = html.replace(marker, script + "\n" + marker, 1)
template.write_text(html, encoding="utf-8")

print("Java V2 dynamic scenario test-data patch applied.")
print("Restart V2 and select an endpoint in Register New Scenario.")

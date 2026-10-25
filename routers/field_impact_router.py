"""Read-only field-impact analyzer with AG-UI compatible SSE event envelope."""
import json
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from services.field_impact.analyzer import analyze_project, enrich_field_impact
from services.field_impact.ui_schema_builder import build_field_impact_view
from services.field_impact.structural_analysis import structural_evidence
from services.field_impact.advanced_analysis import advanced_evidence
from services.field_impact.what_if import simulate_requirement

router=APIRouter(prefix='/api',tags=['Field Impact'])
class AnalyzeRequest(BaseModel):
    project_path:str=Field(min_length=1)
    field:str=Field(min_length=1,max_length=128)

def _run(req):
    try:return advanced_evidence(structural_evidence(enrich_field_impact(analyze_project(req.project_path,req.field))))
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.post('/field-impact')
def field_impact(req:AnalyzeRequest):
    return _run(req)

def _event(event):
    return f'data: {json.dumps(event,ensure_ascii=False)}\n\n'

@router.post('/field-impact/stream')
def field_impact_stream(req:AnalyzeRequest):
    # StreamingResponse uses AG-UI standard event types. Tool progress here is
    # coarse-grained because analysis is synchronous; no fabricated live steps.
    run_id=str(uuid.uuid4());thread_id=str(uuid.uuid4())
    def events():
        yield _event({'type':'RUN_STARTED','threadId':thread_id,'runId':run_id})
        yield _event({'type':'TOOL_CALL_START','toolCallId':run_id+'-scan','toolCallName':'field_impact_scan','parentMessageId':run_id})
        try:
            result=_run(req)
            yield _event({'type':'TOOL_CALL_ARGS','toolCallId':run_id+'-scan','delta':json.dumps({'field':req.field})})
            yield _event({'type':'TOOL_CALL_END','toolCallId':run_id+'-scan'})
            yield _event({'type':'STATE_SNAPSHOT','snapshot':{'field_impact':result,'field_impact_view':build_field_impact_view(result)}})
            yield _event({'type':'RUN_FINISHED','threadId':thread_id,'runId':run_id})
        except Exception as exc:
            yield _event({'type':'RUN_ERROR','message':str(exc)})
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

class GraphSyncRequest(AnalyzeRequest):
    project_id: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=1, max_length=128)

@router.post('/field-impact/graph-sync')
def field_impact_graph_sync(req: GraphSyncRequest):
    """Explicit opt-in sync. Never writes graph data during normal scans."""
    from services.field_impact.graph_store import persist_field_evidence
    try:
        result=_run(req)
        if result.get('warnings'):
            raise HTTPException(status_code=422,detail={'message':'Parser warnings prevent graph sync','warnings':result['warnings']})
        return persist_field_evidence(result,req.project_id,req.revision)
    except (ValueError,RuntimeError) as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.post('/field-impact/view')
def field_impact_view(req: AnalyzeRequest):
    return build_field_impact_view(_run(req))

@router.get('/field-impact/sync-status')
def field_impact_sync_status():
    from services.field_impact.git_sync import status
    return status()

class WhatIfRequest(BaseModel):
    project_path: str = Field(min_length=1)
    requirement: str = Field(min_length=5, max_length=5000)
    fields: list[str] = Field(default_factory=lambda: ['firstName','lastName','fullName'],max_length=20)

@router.post('/field-impact/what-if')
def field_impact_what_if(req:WhatIfRequest):
    results=[_run(AnalyzeRequest(project_path=req.project_path,field=field)) for field in dict.fromkeys(req.fields)]
    return simulate_requirement(req.requirement,results)

@router.post('/field-impact/what-if/stream')
def field_impact_what_if_stream(req:WhatIfRequest):
    run_id=str(uuid.uuid4());thread_id=str(uuid.uuid4())
    def events():
        yield _event({'type':'RUN_STARTED','threadId':thread_id,'runId':run_id})
        yield _event({'type':'TOOL_CALL_START','toolCallId':run_id+'-what-if','toolCallName':'what_if_analysis','parentMessageId':run_id})
        try:
            result=field_impact_what_if(req)
            yield _event({'type':'TOOL_CALL_ARGS','toolCallId':run_id+'-what-if','delta':json.dumps({'fields':req.fields})})
            yield _event({'type':'TOOL_CALL_END','toolCallId':run_id+'-what-if'})
            yield _event({'type':'STATE_SNAPSHOT','snapshot':{'what_if':result}})
            yield _event({'type':'RUN_FINISHED','threadId':thread_id,'runId':run_id})
        except Exception as exc:yield _event({'type':'RUN_ERROR','message':str(exc)})
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

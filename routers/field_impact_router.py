"""Read-only field-impact analyzer with AG-UI compatible SSE event envelope."""
import json
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from services.field_impact.analyzer import analyze_project

router=APIRouter(prefix='/api',tags=['Field Impact'])
class AnalyzeRequest(BaseModel):
    project_path:str=Field(min_length=1)
    field:str=Field(min_length=1,max_length=128)

def _run(req):
    try:return analyze_project(req.project_path,req.field)
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
            yield _event({'type':'STATE_SNAPSHOT','snapshot':{'field_impact':result}})
            yield _event({'type':'RUN_FINISHED','threadId':thread_id,'runId':run_id})
        except Exception as exc:
            yield _event({'type':'RUN_ERROR','message':str(exc)})
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

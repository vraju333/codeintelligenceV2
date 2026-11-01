"""Single investigation contract and streaming event source for both clients."""
import json
import uuid
import logging

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from database import SessionLocal
from services.agent.investigation_contract import build_investigation

router = APIRouter(prefix='/api/investigations', tags=['Investigation Experience'])

class InvestigationRequest(BaseModel):
    query: str = Field(min_length=1)
    project_path: str = Field(min_length=1)


def _run(payload: InvestigationRequest) -> tuple[dict, dict]:
    from routers.knowledge_agent_router import KnowledgeAgentRequest, ask_knowledge_agent
    with SessionLocal() as db:
        answer = ask_knowledge_agent(KnowledgeAgentRequest(query=payload.query, project_path=payload.project_path), db)
    if not isinstance(answer, dict):
        raise ValueError('Knowledge agent did not return an evidence object')
    return answer, build_investigation(payload.query, payload.project_path, answer)

@router.post('/analyse')
def analyse(payload: InvestigationRequest):
    answer, investigation = _run(payload)
    return {'result': answer, 'investigation': investigation}

@router.post('/events')
def events(payload: InvestigationRequest):
    run_id = str(uuid.uuid4())
    def emit(name, data):
        return f'event: {name}\ndata: {json.dumps({"run_id": run_id, **data}, default=str, ensure_ascii=False)}\n\n'
    def generate():
        yield emit('RUN_STARTED', {'message': 'Investigation started', 'project_path': payload.project_path})
        yield emit('STEP_STARTED', {'step': 'Running project-scoped knowledge agent'})
        try:
            answer, investigation = _run(payload)
            yield emit('STEP_FINISHED', {'step': 'Running project-scoped knowledge agent'})
            yield emit('STEP_STARTED', {'step': 'Preparing evidence panels'})
            for panel in investigation['evidence']:
                yield emit('TOOL_EVIDENCE', {'panel': panel})
            yield emit('STEP_FINISHED', {'step': 'Preparing evidence panels'})
            yield emit('STATE_SNAPSHOT', {'result': answer, 'investigation': investigation})
            yield emit('RUN_FINISHED', {'status': 'completed'})
        except Exception as exc:
            logger.exception("Investigation stream failed (run_id=%s)", run_id)
            yield emit('RUN_ERROR', {'message': 'Investigation failed. See backend logs for details.', 'error_type': type(exc).__name__})
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

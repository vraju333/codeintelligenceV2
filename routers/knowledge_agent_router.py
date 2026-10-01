from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.agent.knowledge_agent_service import KnowledgeAgentService

router = APIRouter(prefix="/api/knowledge-agent", tags=["Knowledge Agent"])
service = KnowledgeAgentService()


class KnowledgeAgentRequest(BaseModel):
    query: str = Field(..., min_length=1)


@router.post("/ask")
def ask_knowledge_agent(payload: KnowledgeAgentRequest, db: Session = Depends(get_db)):
    try:
        return service.run(db, payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

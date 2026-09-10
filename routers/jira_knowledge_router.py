from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.jira.jira_knowledge_service import jira_knowledge_service


router = APIRouter(prefix="/api/jira-knowledge", tags=["JIRA Knowledge"])


class JiraKnowledgeRequest(BaseModel):
    jira_id: str = Field(min_length=2)
    title: str | None = None
    requirement: str = Field(min_length=3)


@router.post("/save-index")
def save_and_index(request: JiraKnowledgeRequest, db: Session = Depends(get_db)):
    try:
        return jira_knowledge_service.save_and_index(
            db=db,
            jira_id=request.jira_id,
            title=request.title,
            requirement=request.requirement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def list_jiras(db: Session = Depends(get_db)):
    return jira_knowledge_service.list_all(db)


@router.get("/search")
def search_jiras(query: str, top_k: int = 5, db: Session = Depends(get_db)):
    return {
        "query": query,
        "results": jira_knowledge_service.search(db, query, top_k=top_k),
    }

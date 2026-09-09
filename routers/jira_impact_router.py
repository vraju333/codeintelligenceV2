from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.jira.jira_impact_service import JiraImpactService
from routers.rag_router import rag_service


router = APIRouter(
    prefix="/api/jira-impact",
    tags=["JIRA Impact Analysis"],
)


class JiraImpactRequest(BaseModel):
    jira_id: str | None = None
    requirement: str = Field(min_length=3)


@router.post("/analyze")
def analyze_jira_impact(request: JiraImpactRequest, db: Session = Depends(get_db)):
    try:
        # Build per request so project switching always uses the active Java project.
        service = JiraImpactService(rag_service=rag_service)
        return service.analyze(request.jira_id, request.requirement, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

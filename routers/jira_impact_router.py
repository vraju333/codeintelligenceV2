from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.jira.jira_impact_service import JiraImpactService
from db_models import JiraKnowledge
from pathlib import Path
from services.scanner.java_scanner_service import JavaScannerService
from routers.rag_router import rag_service


router = APIRouter(
    prefix="/api/jira-impact",
    tags=["JIRA Impact Analysis"],
)


class JiraImpactRequest(BaseModel):
    jira_id: str | None = None
    requirement: str | None = Field(default=None, min_length=3)


@router.post("/analyze")
def analyze_jira_impact(request: JiraImpactRequest, db: Session = Depends(get_db)):
    try:
        # The selected workspace is authoritative; never resolve a JIRA from another project.
        active_project = JavaScannerService().scan().project_path
        requirement = (request.requirement or "").strip()
        jira_id = (request.jira_id or "").strip().upper()
        source = "REQUEST_OVERRIDE" if requirement else "STORED_JIRA"
        if not requirement:
            if not jira_id:
                raise HTTPException(status_code=400, detail="jira_id or requirement is required")
            record = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira_id).first()
            if record is None:
                raise HTTPException(status_code=404, detail=f"JIRA {jira_id} is not imported")
            def normalize(path: str | None) -> str:
                return str(Path(path or "")).replace("\\", "/").rstrip("/").casefold()
            if not record.project_path or normalize(record.project_path) != normalize(active_project):
                raise HTTPException(status_code=409, detail=f"JIRA {jira_id} belongs to a different workspace project")
            requirement = (record.requirement or "").strip()
            if len(requirement) < 3:
                raise HTTPException(status_code=422, detail=f"JIRA {jira_id} has no usable stored requirement")
        service = JiraImpactService(rag_service=rag_service)
        result = service.analyze(jira_id or None, requirement, db)
        result["requirement_source"] = source
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

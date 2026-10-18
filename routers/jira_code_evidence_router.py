"""Additive read-only API; existing main.py router registration stays unchanged."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from services.jira.jira_code_evidence_resolver import resolve_jira_code_candidates

router = APIRouter(prefix="/api/jira-code-evidence", tags=["JIRA Code Evidence"])

class JiraCodeEvidenceRequest(BaseModel):
    project_path: str = Field(..., min_length=1)
    base_ref: str | None = None  # omit for current Java source (no commits required)
    target_ref: str = "HEAD"
    jira_ids: list[str] = Field(default_factory=list)

@router.post("/resolve")
def resolve(payload: JiraCodeEvidenceRequest, db: Session = Depends(get_db)):
    try:
        return resolve_jira_code_candidates(db, payload.project_path, payload.base_ref, payload.target_ref, payload.jira_ids)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

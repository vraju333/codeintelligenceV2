from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

router=APIRouter(prefix="/api/knowledge-sync",tags=["Enterprise Knowledge Sync"])
service=EnterpriseKnowledgeSyncService()

class SourceRequest(BaseModel):
    name: str
    source_type: str
    location: str | None = None
    config: dict[str,Any] = Field(default_factory=dict)
    enabled: bool = True
    project_path: str | None = None

@router.get("/status")
def status(db:Session=Depends(get_db)):
    return service.list_sources(db)

@router.post("/sources")
def register(payload:SourceRequest,db:Session=Depends(get_db)):
    try: return service.register(db,name=payload.name,source_type=payload.source_type,location=payload.location,config=payload.config,enabled=payload.enabled,project_path=payload.project_path)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.post("/sources/{source_id}/sync")
def sync_source(source_id:int,db:Session=Depends(get_db)):
    try: return service.sync_source(db,source_id)
    except ValueError as exc: raise HTTPException(status_code=404,detail=str(exc)) from exc

@router.post("/sync-all")
def sync_all(db:Session=Depends(get_db)):
    return service.sync_all(db)

@router.get("/runs")
def runs(limit:int=25,db:Session=Depends(get_db)):
    return service.recent_runs(db,limit)

@router.get("/jira/projects")
def jira_projects():
    """Discover Jira projects using backend-only .env credentials."""
    try:
        result = service._jira_request("/rest/api/3/project/search?maxResults=100")
        return {"projects": [{"key": p.get("key"), "name": p.get("name")}
                            for p in result.get("values", []) if p.get("key")]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@router.patch("/sources/{source_id}")
def update_source(source_id: int, payload: SourceRequest, db: Session = Depends(get_db)):
    from db_models import KnowledgeSyncSource
    row = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == source_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    # Register preserves the existing source ID through its unique name.
    if row.name != payload.name:
        raise HTTPException(status_code=400, detail="Renaming is not supported; keep the source name")
    try:
        return service.register(db, name=payload.name, source_type=payload.source_type,
                                location=payload.location, config=payload.config,
                                enabled=payload.enabled, project_path=payload.project_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ConnectSourceRequest(SourceRequest):
    """The IntelliJ Knowledge Hub uses this instead of manual register + sync."""

@router.post("/connect")
def connect_source(payload: ConnectSourceRequest, db: Session = Depends(get_db)):
    from services.knowledge.knowledge_hub_onboarding_service import KnowledgeHubOnboardingService
    try:
        return KnowledgeHubOnboardingService().connect(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sources/{source_id}/verify")
def verify_source(source_id: int, db: Session = Depends(get_db)):
    from services.knowledge.knowledge_hub_verification_service import verify_source as verify
    try:
        return verify(db, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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

@router.get("/status")
def status(db:Session=Depends(get_db)):
    return service.list_sources(db)

@router.post("/sources")
def register(payload:SourceRequest,db:Session=Depends(get_db)):
    try: return service.register(db,name=payload.name,source_type=payload.source_type,location=payload.location,config=payload.config,enabled=payload.enabled)
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

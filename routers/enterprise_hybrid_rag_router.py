from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.retrieval.enterprise_hybrid_rag_service import EnterpriseHybridRagService

router = APIRouter(prefix="/api/enterprise-rag", tags=["Phase 8 Enterprise Hybrid RAG"])
service = EnterpriseHybridRagService()


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    source_types: list[str] | None = None
    project_path: str | None = None


@router.post("/rebuild")
def rebuild(db: Session = Depends(get_db)) -> dict[str, Any]:
    return service.rebuild(db)


@router.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    return service.search(request.query, request.top_k, request.source_types, request.project_path)


@router.get("/status")
def status() -> dict[str, Any]:
    return service.status()

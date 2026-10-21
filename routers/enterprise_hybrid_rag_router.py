from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.retrieval.enterprise_hybrid_rag_service import EnterpriseHybridRagService
from services.retrieval.retrieval_accuracy import evaluate_cases

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


class EvaluationCase(BaseModel):
    query: str = Field(min_length=1)
    project_path: str = Field(min_length=1)
    relevant_document_ids: list[str] = Field(min_length=1)


class EvaluationRequest(BaseModel):
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)
    k: int = Field(default=10, ge=1, le=50)


@router.post("/evaluate")
def evaluate(request: EvaluationRequest) -> dict[str, Any]:
    """Evaluate labeled project-scoped retrieval; no fabricated accuracy percentages."""
    try:
        return evaluate_cases([c.model_dump() for c in request.cases], service.search, request.k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

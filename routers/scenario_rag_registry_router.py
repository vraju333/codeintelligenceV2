from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from services.scenario_rag.scenario_rag_registry_service import (
    scenario_rag_registry_service,
)


router = APIRouter(
    prefix="/api/scenario-rag-registry",
    tags=["Scenario RAG Registry"],
)


@router.post("/index")
def rebuild_scenario_rag_registry(db: Session = Depends(get_db)):
    return scenario_rag_registry_service.rebuild_index(db)


@router.get("/search")
def search_scenario_rag_registry(
    query: str = Query(min_length=1),
    top_k: int = 10,
    db: Session = Depends(get_db),
):
    return scenario_rag_registry_service.search(db, query=query, top_k=top_k)

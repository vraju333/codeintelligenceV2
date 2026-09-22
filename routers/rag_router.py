from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db

from services.rag.rag_service import RagService
from services.scenario.scenario_service import ScenarioService


router = APIRouter(
    prefix="/api/rag",
    tags=["RAG"]
)

rag_service = RagService()
scenario_service = ScenarioService()


@router.post("/index")
def index_project(db: Session = Depends(get_db)):
    result = rag_service.index_project()
    result["scenario_sync"] = scenario_service.sync_discovered_operations(db)
    return result


@router.get("/search")
def search_code(
    query: str = Query(...),
    top_k: int = Query(5)
):
    return {
        "query": query,
        "results": rag_service.search(
            query=query,
            top_k=top_k
        )
    }
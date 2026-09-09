from fastapi import APIRouter, Query

from services.rag.rag_service import RagService


router = APIRouter(
    prefix="/api/rag",
    tags=["RAG"]
)

rag_service = RagService()


@router.post("/index")
def index_project():
    return rag_service.index_project()


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
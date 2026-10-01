from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from services.knowledge.unified_knowledge_search_service import UnifiedKnowledgeSearchService

router = APIRouter(prefix="/api/knowledge-search", tags=["Unified Knowledge Search"])
service = UnifiedKnowledgeSearchService()


@router.get("/search")
def unified_knowledge_search(
    query: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    try:
        return service.search(db, query, top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

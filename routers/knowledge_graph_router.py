from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from routers.cross_project_router import service as cross_project_service
from services.cross_project.knowledge_graph_service import KnowledgeGraphService

router = APIRouter(prefix="/api/knowledge", tags=["Knowledge Graph"])
service = KnowledgeGraphService(cross_project_service)


@router.get("/attribute")
def attribute_knowledge(
    attribute: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    try:
        return service.analyze_attribute(attribute, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.cross_project.engineering_knowledge_graph_service import EngineeringKnowledgeGraphService

router = APIRouter(prefix="/api/engineering-graph", tags=["Phase 5 - Engineering Knowledge Graph"])
service = EngineeringKnowledgeGraphService()


class GraphSyncRequest(BaseModel):
    projects: list[str] | None = None


@router.get("/status")
def status():
    return service.status()


@router.post("/sync")
def sync(request: GraphSyncRequest, db: Session = Depends(get_db)):
    try:
        return service.sync(db, request.projects)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/search")
def search(
    entity: str = Query(..., min_length=1),
    max_hops: int = Query(8, ge=1, le=12),
    limit: int = Query(250, ge=1, le=1000),
):
    try:
        return service.search(entity, max_hops=max_hops, limit=limit)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/impact")
def impact(
    entity: str = Query(..., min_length=1),
    project: str | None = Query(None),
):
    try:
        return service.impact(entity, project=project)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/schema")
def schema():
    return {
        "nodes": ["Project", "Class", "Method", "Attribute", "Endpoint", "Scenario", "Jira", "Release", "TestBaseline"],
        "relationships": ["CONTAINS", "DECLARES", "READS", "WRITES", "CALLS", "EXPOSES", "INVOKES", "COVERED_BY", "CHANGED_BY", "RELEASED_IN", "TESTED_IN", "BELONGS_TO", "VERIFIES_CHANGE"],
        "system_of_record": "PostgreSQL/SQLite",
        "relationship_intelligence": "Neo4j",
    }

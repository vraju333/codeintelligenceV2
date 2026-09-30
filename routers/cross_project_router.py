from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from services.cross_project.cross_project_graph_service import CrossProjectGraphService

router = APIRouter(prefix="/api/cross-project", tags=["Phase E - Cross Project"])
service = CrossProjectGraphService()

class CrossProjectSyncRequest(BaseModel):
    projects: list[str]

@router.get("/projects")
def available_projects():
    return service.available_projects()

@router.post("/sync")
def sync_graph(request: CrossProjectSyncRequest):
    try:
        return service.sync(request.projects)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/graph")
def graph():
    return service.graph()

@router.get("/impact")
def impact(q: str = Query(..., min_length=1)):
    return service.impact(q)

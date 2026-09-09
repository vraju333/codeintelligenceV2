from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers.rag_router import rag_service
from routers.scanner_router import service as scanner_service
from services.project.project_registry_service import ProjectRegistryService


router = APIRouter(prefix="/api/projects", tags=["Projects"])
service = ProjectRegistryService()


class ProjectPathRequest(BaseModel):
    project_path: str


@router.get("")
def get_projects():
    return service.list_projects()


@router.post("/register")
def register_project(request: ProjectPathRequest):
    try:
        selected = service.select(request.project_path)
        scan = scanner_service.scan()
        rag = rag_service.index_project()
        project = {
            "name": __import__("pathlib").Path(selected).name,
            "path": selected
        }
        return {
            "status": "READY",
            "project": project,
            "total_java_files": scan.total_java_files,
            "total_classes": len(scan.classes),
            "rag": rag,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/select")
def select_project(request: ProjectPathRequest):
    try:
        selected = service.select(request.project_path)
        scan = scanner_service.scan()
        rag = rag_service.index_project()
        return {
            "status": "READY",
            "project_path": selected,
            "project_name": __import__("pathlib").Path(selected).name,
            "total_java_files": scan.total_java_files,
            "total_classes": len(scan.classes),
            "rag": rag,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

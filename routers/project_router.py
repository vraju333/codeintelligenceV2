from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db

from routers.rag_router import rag_service
from routers.scanner_router import service as scanner_service
from services.project.project_registry_service import ProjectRegistryService
from services.scenario.scenario_service import ScenarioService


router = APIRouter(prefix="/api/projects", tags=["Projects"])
service = ProjectRegistryService()
scenario_service = ScenarioService()


class ProjectPathRequest(BaseModel):
    project_path: str


@router.get("")
def get_projects():
    return service.list_projects()




@router.post("/browse")
def browse_project_folder():
    """
    Local-UAT helper: opens the operating system folder chooser on the same
    desktop where the FastAPI process is running. No project is registered
    until /register is called.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                title="Select Java Project Folder",
                mustexist=True,
            )
        finally:
            root.destroy()

        if not selected:
            return {"status": "CANCELLED", "project_path": None}

        path = __import__("pathlib").Path(selected)
        java_files = list(path.rglob("*.java"))
        if not java_files:
            raise HTTPException(
                status_code=400,
                detail="The selected folder does not contain any Java source files."
            )

        return {
            "status": "SELECTED",
            "project_path": str(path.resolve()),
            "project_name": path.name,
            "java_files": len(java_files),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Folder picker could not be opened. "
                "Use Enter Path Manually instead. Details: " + str(exc)
            ),
        )


@router.post("/register")
def register_project(request: ProjectPathRequest, db: Session = Depends(get_db)):
    try:
        selected = service.select(request.project_path)
        scan = scanner_service.scan()
        rag = rag_service.index_project()
        scenario_sync = scenario_service.sync_discovered_operations(db)
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
            "scenario_sync": scenario_sync,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/select")
def select_project(request: ProjectPathRequest, db: Session = Depends(get_db)):
    try:
        selected = service.select(request.project_path)
        scan = scanner_service.scan()
        rag = rag_service.index_project()
        scenario_sync = scenario_service.sync_discovered_operations(db)
        return {
            "status": "READY",
            "project_path": selected,
            "project_name": __import__("pathlib").Path(selected).name,
            "total_java_files": scan.total_java_files,
            "total_classes": len(scan.classes),
            "rag": rag,
            "scenario_sync": scenario_sync,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

from fastapi import APIRouter, HTTPException, Depends
from pathlib import Path
from urllib.parse import urlsplit
import subprocess
import re
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
        # Registering an already-known project must not trigger a second scan.
        requested = Path(request.project_path).expanduser().resolve()
        known = service.list_projects().get("projects", [])
        if any(Path(item.get("path", "")).expanduser().resolve() == requested for item in known):
            return {"status": "EXISTS", "project": {"name": requested.name, "path": str(requested)},
                    "already_registered": True, "indexing_skipped": True}
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


class GitImportRequest(BaseModel):
    repository_url: str


@router.post("/import-git")
def import_git_project(request: GitImportRequest, db: Session = Depends(get_db)):
    """Clone a public GitHub repository to the backend machine and index it.

    This deliberately restricts the remote host and repository syntax. It does
    not execute arbitrary git transports or overwrite existing directories.
    """
    parsed = urlsplit(request.repository_url.strip())
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment):
        raise HTTPException(400, "Provide a public https://github.com/owner/repository URL")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parsed.path)
    if not match or match.group(1) in (".", "..") or match.group(2) in (".", ".."):
        raise HTTPException(400, "Invalid GitHub repository path")
    owner, repo = match.groups()
    base = Path(__file__).resolve().parents[1] / "imported_projects"
    base.mkdir(parents=True, exist_ok=True)
    destination = base / f"{owner}__{repo}"
    if destination.exists():
        if not (destination / ".git").is_dir():
            raise HTTPException(409, "Import directory already exists and is not a Git checkout")
        # Reuse the existing clone; do not silently pull or overwrite changes.
    else:
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--", f"https://github.com/{owner}/{repo}.git", str(destination)],
                           check=True, timeout=300, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Git clone timed out after 5 minutes")
        except FileNotFoundError:
            raise HTTPException(500, "Git executable is not available on the backend machine")
        except subprocess.CalledProcessError as exc:
            raise HTTPException(502, "Git clone failed: " + (exc.stderr or "unknown error")[-350:])
    return register_project(ProjectPathRequest(project_path=str(destination)), db)


@router.post("/select")
def select_project(request: ProjectPathRequest):
    """Select an existing project without repeating expensive indexing.

    Use /register for initial indexing. An explicit indexing endpoint should
    handle subsequent source updates.
    """
    try:
        selected = service.select(request.project_path)
        return {"status": "SELECTED", "project_path": selected,
                "project_name": Path(selected).name, "indexing_skipped": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

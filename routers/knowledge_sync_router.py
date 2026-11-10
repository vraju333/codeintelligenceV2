from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

router=APIRouter(prefix="/api/knowledge-sync",tags=["Enterprise Knowledge Sync"])
service=EnterpriseKnowledgeSyncService()

class SourceRequest(BaseModel):
    name: str
    source_type: str
    location: str | None = None
    config: dict[str,Any] = Field(default_factory=dict)
    enabled: bool = True
    project_path: str | None = None

@router.get("/status")
def status(db:Session=Depends(get_db)):
    return service.list_sources(db)

@router.post("/sources")
def register(payload:SourceRequest,db:Session=Depends(get_db)):
    try: return service.register(db,name=payload.name,source_type=payload.source_type,location=payload.location,config=payload.config,enabled=payload.enabled,project_path=payload.project_path)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.post("/sources/{source_id}/sync")
def sync_source(source_id:int,db:Session=Depends(get_db)):
    try: return service.sync_source(db,source_id)
    except ValueError as exc: raise HTTPException(status_code=404,detail=str(exc)) from exc

@router.post("/sync-all")
def sync_all(db:Session=Depends(get_db)):
    return service.sync_all(db)

@router.get("/runs")
def runs(limit:int=25,db:Session=Depends(get_db)):
    return service.recent_runs(db,limit)

@router.get("/jira/projects")
def jira_projects():
    """Discover Jira projects using backend-only .env credentials."""
    try:
        result = service._jira_request("/rest/api/3/project/search?maxResults=100")
        return {"projects": [{"key": p.get("key"), "name": p.get("name")}
                            for p in result.get("values", []) if p.get("key")]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/jira/issues")
def jira_issues(project_key: str, query: str = ""):
    """Browse Jira issues in a selected project, with optional text filtering."""
    import re
    from urllib.parse import quote
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project_key):
        raise HTTPException(400, "Invalid Jira project key")
    jql = f'project = "{project_key.upper()}" ORDER BY updated DESC'
    if query.strip():
        safe = query.strip().replace('\\', '\\\\').replace('"', '\\"')
        jql = f'project = "{project_key.upper()}" AND text ~ "{safe}" ORDER BY updated DESC'
    try:
        result = service._jira_request("/rest/api/3/search/jql?maxResults=100&fields=summary,attachment&jql=" + quote(jql, safe=""))
        return {"issues": [{"key": x.get("key"), "summary": (x.get("fields") or {}).get("summary", ""),
                            "attachment_count": len((x.get("fields") or {}).get("attachment") or [])}
                           for x in result.get("issues", [])]}
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

@router.get("/jira/issues/{issue_key}/attachments")
def jira_issue_attachments(issue_key: str):
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", issue_key):
        raise HTTPException(400, "Invalid Jira issue key")
    try:
        result = service._jira_request(f"/rest/api/3/issue/{issue_key.upper()}?fields=summary,attachment")
        fields = result.get("fields") or {}
        from services.knowledge.test_suite_service import SUPPORTED
        return {"issue": issue_key.upper(), "summary": fields.get("summary", ""),
                "attachments": [{"id": str(a.get("id")), "filename": a.get("filename"),
                                 "size": a.get("size"), "supported": str(a.get("filename") or "").lower().endswith(tuple(SUPPORTED))}
                                for a in fields.get("attachment") or []]}
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

@router.patch("/sources/{source_id}")
def update_source(source_id: int, payload: SourceRequest, db: Session = Depends(get_db)):
    from db_models import KnowledgeSyncSource
    row = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == source_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    # Register preserves the existing source ID through its unique name.
    if row.name != payload.name:
        raise HTTPException(status_code=400, detail="Renaming is not supported; keep the source name")
    try:
        return service.register(db, name=payload.name, source_type=payload.source_type,
                                location=payload.location, config=payload.config,
                                enabled=payload.enabled, project_path=payload.project_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ConnectSourceRequest(SourceRequest):
    """The IntelliJ Knowledge Hub uses this instead of manual register + sync."""

@router.post("/connect")
def connect_source(payload: ConnectSourceRequest, db: Session = Depends(get_db)):
    from services.knowledge.knowledge_hub_onboarding_service import KnowledgeHubOnboardingService
    try:
        return KnowledgeHubOnboardingService().connect(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sources/{source_id}/verify")
def verify_source(source_id: int, db: Session = Depends(get_db)):
    from services.knowledge.knowledge_hub_verification_service import verify_source as verify
    try:
        return verify(db, source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sources/{source_id}/mapping-inventory")
def mapping_inventory(source_id: int, project_path: str, db: Session = Depends(get_db)):
    """Read-only reconciliation of Excel files and mapping imports for a workspace."""
    from pathlib import Path
    from db_models import KnowledgeSyncSource, MappingDocument

    def norm(value):
        return str(value or "").strip().replace("\\", "/").rstrip("/").casefold()

    source = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == source_id).first()
    if source is None or source.source_type != "MAPPING_FOLDER":
        raise HTTPException(status_code=404, detail="Mapping source not found")
    if not project_path or norm(project_path) != norm(source.project_path):
        raise HTTPException(status_code=400, detail="Project does not match registered mapping source")
    folder = Path(str(source.location or "")).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Mapping source folder is unavailable")
    files = sorted((p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("~$")
                    and p.suffix.lower() in {".xlsx", ".xlsm"}), key=lambda p: str(p).casefold())
    documents = [doc for doc in db.query(MappingDocument).filter(MappingDocument.status == "ACTIVE").all()
                 if norm(doc.project_path) == norm(source.project_path)]
    by_source = {}
    for doc in documents:
        by_source.setdefault(norm(doc.source_ref), []).append(doc)
    entries = []
    for file in files:
        full_path = str(file.resolve())
        matches = by_source.get(norm(full_path), [])
        entries.append({"filename": file.name, "path": full_path,
                        "imported_record_count": len(matches),
                        "document_ids": [doc.id for doc in matches],
                        "mapping_row_counts": [doc.row_count for doc in matches],
                        "status": "IMPORTED" if matches else "NO_MATCHING_IMPORT"})
    return {"source_id": source.id, "project_path": source.project_path,
            "discovered": len(entries), "files": entries,
            "imported_records_for_project": len(documents),
            "files_without_matching_import": sum(x["status"] != "IMPORTED" for x in entries)}

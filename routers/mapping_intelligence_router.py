from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from threading import Lock
from time import monotonic
import os

from database import get_db
from services.lineage.mapping_intelligence_service import MappingIntelligenceService

router = APIRouter(prefix="/api/mapping-intelligence", tags=["Phase 6 - Mapping Intelligence"])
service = MappingIntelligenceService()

# Avoid repeating expensive folder imports for each Knowledge Hub read.
# A forced sync (?sync=true) always bypasses this throttle.
_AUTO_SYNC_INTERVAL = max(0, int(os.getenv("MAPPING_DOCUMENT_AUTO_SYNC_SECONDS", "60")))
_last_auto_sync: dict[str, float] = {}
_auto_sync_lock = Lock()


@router.post("/upload")
async def upload_mapping(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    document_version: str | None = Form(None),
    mapping_family: str | None = Form(None),
    source_ref: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        return service.import_excel(db, filename=file.filename or "mapping.xlsx", content=await file.read(),
                                    title=title, document_version=document_version, mapping_family=mapping_family, source_ref=source_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/documents")
def documents(project_path: str | None = Query(None), sync: bool | None = Query(None),
              include_history: bool = Query(False), db: Session = Depends(get_db)):
    """Project-scoped mapping documents.

    sync=true: force folder sync and reconciliation (manual Sync now).
    sync=false: read-only, no import or database writes.
    omitted sync: automatically synchronize at most once per configured interval
    per project and worker; other reads return the stored documents immediately.
    """
    if not project_path:
        raise HTTPException(status_code=400, detail="project_path is required")

    scope = service.normalize_project_path(project_path)
    reports = []
    changes = {"status": "NOT_RUN", "reason": "Read-only request"}
    sync_status = "READ_ONLY"
    locked = False
    should_sync = False
    forced = sync is True

    if sync is False:
        sync_status = "READ_ONLY"
    elif forced:
        # Serialize forced sync with automatic sync in this process.
        _auto_sync_lock.acquire()
        locked = True
        should_sync = True
        sync_status = "FORCED"
    else:
        # Nonblocking: concurrent reads should not queue behind a slow import.
        locked = _auto_sync_lock.acquire(blocking=False)
        if not locked:
            sync_status = "IN_PROGRESS"
        elif monotonic() - _last_auto_sync.get(scope, float("-inf")) < _AUTO_SYNC_INTERVAL:
            sync_status = "THROTTLED"
        else:
            should_sync = True
            sync_status = "AUTOMATIC"

    try:
        if should_sync:
            from db_models import KnowledgeSyncSource
            from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

            engine = EnterpriseKnowledgeSyncService()
            sources = [s for s in db.query(KnowledgeSyncSource).filter(
                KnowledgeSyncSource.source_type == "MAPPING_FOLDER").all()
                if service.normalize_project_path(s.project_path) == scope and
                str(s.enabled or "true").lower() == "true"]
            for source in sources:
                result = engine.sync_source(db, source.id, trigger="DOCUMENTS_READ", rebuild_indexes=False)
                reports.append(result)
            if not any(r.get("status") == "FAILED" for r in reports):
                changes = service.reconcile_project_records(db, project_path)
                db.commit()
                _last_auto_sync[scope] = monotonic()
            else:
                changes = {"status": "NOT_RUN", "reason": "A source failed to sync"}
                sync_status = "FAILED"
    finally:
        if locked:
            _auto_sync_lock.release()

    response = service.list_project_documents(db, project_path, include_history=include_history)
    response["sync"] = {"sources": reports, "reconciliation": changes, "mode": sync_status,
                        "auto_sync_interval_seconds": _AUTO_SYNC_INTERVAL}
    response["record_count"] = len(response["documents"])
    response["distinct_file_count"] = len({str(d.get("filename") or "").casefold() for d in response["documents"]})
    return response


@router.get("/documents/{document_id}")
def document(document_id: int, db: Session = Depends(get_db)):
    try:
        return service.document_rows(db, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=250), db: Session = Depends(get_db)):
    return service.search(db, q, limit)


@router.get("/lineage")
def lineage(attribute: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    return service.lineage(db, attribute)


@router.get("/graph-lineage")
def graph_lineage(
    attribute: str = Query(..., min_length=1),
    project: str | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        return service.graph_lineage(attribute, project, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/families")
def families(db: Session = Depends(get_db)):
    return service.list_families(db)


@router.get("/history")
def history(
    attribute: str = Query(..., min_length=1),
    mapping_family: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return service.mapping_history(db, attribute, mapping_family)


@router.get("/compare")
def compare(
    mapping_family: str = Query(..., min_length=1),
    from_version: str = Query(..., min_length=1),
    to_version: str = Query(..., min_length=1),
    attribute: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return service.compare_versions(db, mapping_family, from_version, to_version, attribute)


@router.get("/validate")
def validate(document_id: int | None = Query(None), db: Session = Depends(get_db)):
    return service.validate(db, document_id)


@router.post("/documents/{document_id}/sync")
def sync(document_id: int, db: Session = Depends(get_db)):
    try:
        return service.sync_document_to_neo4j(db, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

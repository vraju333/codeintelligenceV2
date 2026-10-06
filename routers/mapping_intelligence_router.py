from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from services.lineage.mapping_intelligence_service import MappingIntelligenceService

router = APIRouter(prefix="/api/mapping-intelligence", tags=["Phase 6 - Mapping Intelligence"])
service = MappingIntelligenceService()


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
def documents(db: Session = Depends(get_db)):
    return service.list_documents(db)


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
):
    try:
        return service.graph_lineage(attribute, project)
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

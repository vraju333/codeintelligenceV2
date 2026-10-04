from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.knowledge.file_text_extractor import FileTextExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService

router = APIRouter(prefix="/api/knowledge-base", tags=["Knowledge Base Ingestion"])
service = KnowledgeIngestionService()
file_extractor = FileTextExtractor()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class KnowledgeDocumentRequest(BaseModel):
    source_type: str = Field(..., examples=["ARCHITECTURE"])
    title: str
    content: str
    source_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/documents")
def ingest_document(payload: KnowledgeDocumentRequest, db: Session = Depends(get_db)):
    try:
        return service.ingest(
            db,
            source_type=payload.source_type,
            title=payload.title,
            content=payload.content,
            source_ref=payload.source_ref,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/files")
@router.post("/documents/upload")
async def ingest_file(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    title: str | None = Form(None),
    source_ref: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Upload a TXT, MD, JSON, DOCX or text-based PDF into the existing KB pipeline."""
    try:
        filename = Path(file.filename or "knowledge-document").name
        content_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content_bytes) > MAX_UPLOAD_BYTES:
            raise ValueError("File is too large. Maximum upload size is 20 MB")

        text = file_extractor.extract(filename, content_bytes)
        document_title = (title or "").strip() or Path(filename).stem
        document_source_ref = (source_ref or "").strip() or filename

        result = service.ingest(
            db,
            source_type=source_type,
            title=document_title,
            content=text,
            source_ref=document_source_ref,
            metadata={
                "upload": {
                    "file_name": filename,
                    "content_type": file.content_type or "",
                    "size_bytes": len(content_bytes),
                }
            },
        )
        return {
            "status": result.get("status"),
            "file_name": filename,
            "document": result.get("document"),
            "extracted": result.get("extracted"),
            "rag": result.get("rag"),
            "neo4j": result.get("neo4j"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await file.close()


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    return service.list_documents(db)


@router.post("/rebuild")
def rebuild_knowledge_rag(db: Session = Depends(get_db)):
    return service.rebuild_rag(db)


@router.get("/search")
def search_knowledge(
    query: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    try:
        return service.search(db, query, top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# -----------------------------------------------------------------------------
# Structured enterprise artifact ingestion (pre-live-JIRA milestone)
# -----------------------------------------------------------------------------
from services.knowledge.structured_knowledge_ingestion_service import StructuredKnowledgeIngestionService

structured_service = StructuredKnowledgeIngestionService()


async def _read_structured_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = Path(file.filename or "engineering-artifact").name
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File is too large. Maximum upload size is 20 MB")
    if not data:
        raise ValueError("Uploaded file is empty")
    return filename, data


@router.post("/test-reports/upload")
async def ingest_test_report(file: UploadFile = File(...), title: str | None = Form(None),
                             source_ref: str | None = Form(None), db: Session = Depends(get_db)):
    try:
        filename, data = await _read_structured_upload(file)
        return structured_service.ingest_test_report(db, filename, data, title, source_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await file.close()


@router.post("/openapi/upload")
async def ingest_openapi(file: UploadFile = File(...), title: str | None = Form(None),
                         source_ref: str | None = Form(None), db: Session = Depends(get_db)):
    try:
        filename, data = await _read_structured_upload(file)
        return structured_service.ingest_openapi(db, filename, data, title, source_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await file.close()


async def _ingest_typed_document(source_type: str, file: UploadFile, title: str | None,
                                 source_ref: str | None, db: Session):
    try:
        filename, data = await _read_structured_upload(file)
        return structured_service.ingest_document(db, source_type, filename, data, title, source_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await file.close()


@router.post("/releases/upload")
async def ingest_release_document(file: UploadFile = File(...), title: str | None = Form(None),
                                  source_ref: str | None = Form(None), db: Session = Depends(get_db)):
    return await _ingest_typed_document("RELEASE", file, title, source_ref, db)


@router.post("/architecture/upload")
async def ingest_architecture_document(file: UploadFile = File(...), title: str | None = Form(None),
                                       source_ref: str | None = Form(None), db: Session = Depends(get_db)):
    return await _ingest_typed_document("ARCHITECTURE", file, title, source_ref, db)

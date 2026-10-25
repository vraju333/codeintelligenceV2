from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from services.release_intelligence.regression_release_intelligence_service import RegressionReleaseIntelligenceService

router = APIRouter(prefix="/api/release-intelligence", tags=["Regression & Release Intelligence"])
service = RegressionReleaseIntelligenceService()


@router.get("/status")
def release_intelligence_status():
    return service.status()


@router.get("/analyse")
def release_intelligence_analyse(mode: str = "working", base_ref: str | None = None, target_ref: str | None = None, db: Session = Depends(get_db)):
    try:
        return service.analyse(db, mode=mode, base_ref=base_ref, target_ref=target_ref)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

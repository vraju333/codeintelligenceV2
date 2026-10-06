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
def release_intelligence_analyse(db: Session = Depends(get_db)):
    try:
        return service.analyse(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

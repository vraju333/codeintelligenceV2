from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.scenario.operation_baseline_service import OperationBaselineService


router = APIRouter(prefix="/api/operation-baselines", tags=["Operation Baselines"])
service = OperationBaselineService()


class CaptureOperationBaselineRequest(BaseModel):
    http_method: str
    endpoint: str


@router.get("/overview")
def overview(http_method: str | None = Query(default=None), db: Session = Depends(get_db)):
    return service.get_overview(db, http_method=http_method)


@router.post("/capture")
def capture(request: CaptureOperationBaselineRequest, db: Session = Depends(get_db)):
    return service.capture(db, request.http_method, request.endpoint)


@router.get("/history")
def history(http_method: str, endpoint: str, db: Session = Depends(get_db)):
    return service.history(db, http_method, endpoint)


@router.get("/compare")
def compare(
    http_method: str,
    endpoint: str,
    from_version: int,
    to_version: int,
    db: Session = Depends(get_db),
):
    return service.compare(db, http_method, endpoint, from_version, to_version)

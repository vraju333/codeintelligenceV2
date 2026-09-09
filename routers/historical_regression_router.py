from typing import Any

from fastapi import (
    APIRouter,
    Depends
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.regression.historical_regression_service import (
    HistoricalRegressionService
)


router = APIRouter(
    prefix="/api/regression-history",
    tags=["Historical Regression"]
)

service = HistoricalRegressionService()


class HistoricalRegressionRequest(
    BaseModel
):

    current_flow: Any


@router.post("/analyse/{scenario_id}")
def analyse_historical_regression(
    scenario_id: int,
    request: HistoricalRegressionRequest,
    db: Session = Depends(get_db)
):

    return service.analyse(
        db=db,
        scenario_id=scenario_id,
        current_flow=request.current_flow
    )
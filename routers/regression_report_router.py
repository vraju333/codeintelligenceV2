from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy.orm import Session

from database import get_db
from services.report.regression_report_service import (
    RegressionReportService
)


router = APIRouter(
    prefix="/api/reports",
    tags=["Reports"]
)

service = (
    RegressionReportService()
)


@router.get("/regression")
def generate_regression_report(
    db: Session = Depends(get_db)
):

    try:

        return service.generate(
            db
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )
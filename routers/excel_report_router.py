from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from services.report.excel_export_service import (
    ExcelExportService
)
from services.report.regression_report_service import (
    RegressionReportService
)


router = APIRouter(
    prefix="/api/reports",
    tags=["Reports"]
)

regression_report_service = (
    RegressionReportService()
)

excel_export_service = (
    ExcelExportService()
)


@router.get("/regression/excel")
def export_regression_excel(
    db: Session = Depends(get_db)
):

    try:

        report = (
            regression_report_service
            .generate(db)
        )

        excel_file = (
            excel_export_service
            .generate_regression_report(
                report,
                db
            )
        )

        headers = {
            "Content-Disposition":
                "attachment; "
                "filename="
                "codeintelligence_scenario_regression_report.xlsx"
        }

        return StreamingResponse(
            excel_file,
            media_type=(
                "application/"
                "vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers=headers
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )
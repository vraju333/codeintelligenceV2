from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from pydantic import BaseModel

from services.defect.defect_comparison_service import (
    DefectComparisonService
)


router = APIRouter(
    prefix="/api/defect-analysis",
    tags=["Defect Analysis"]
)


class DefectComparisonRequest(
    BaseModel
):

    expected: Any
    actual: Any


@router.post("/compare")
def compare_expected_actual(
    request: DefectComparisonRequest,

    http_method: str = Query(
        ...
    ),

    endpoint: str = Query(
        ...
    )
):

    try:

        service = (
            DefectComparisonService()
        )

        return service.compare(
            http_method=http_method,
            endpoint=endpoint,
            expected=request.expected,
            actual=request.actual
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )
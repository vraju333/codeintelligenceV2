from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from services.flow.scenario_attribute_trace_service import (
    ScenarioAttributeTraceService
)


router = APIRouter(
    prefix="/api/attribute-lineage",
    tags=["Attribute Lineage"]
)


@router.get("/trace")
def trace_attribute(
    http_method: str = Query(...),
    endpoint: str = Query(...),
    attribute: str = Query(...)
):

    try:

        service = (
            ScenarioAttributeTraceService()
        )

        return service.trace(
            http_method=http_method,
            endpoint=endpoint,
            attribute_name=attribute
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
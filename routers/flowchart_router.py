from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from services.flow.endpoint_flow_service import (
    EndpointFlowService
)
from services.report.flowchart_service import (
    FlowchartService
)


router = APIRouter(
    prefix="/api/reports",
    tags=["Reports"]
)

flowchart_service = (
    FlowchartService()
)


@router.get("/flowchart")
def generate_flowchart(
    http_method: str = Query(...),
    endpoint: str = Query(...)
):

    try:

        endpoint_flow_service = EndpointFlowService()

        flow_data = (
            endpoint_flow_service
            .analyze_endpoint(
                http_method=http_method,
                endpoint=endpoint
            )
        )

        result = (
            flowchart_service
            .generate(
                flow_data
            )
        )

        return {
            "http_method":
                http_method.upper(),

            "endpoint":
                endpoint,

            **result
        }

    except Exception as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )
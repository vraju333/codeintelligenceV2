from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from services.flow.endpoint_flow_service import (
    EndpointFlowService
)


router = APIRouter(
    prefix="/api/endpoint-flow",
    tags=["Endpoint Flow"]
)


@router.get("/endpoints")
def get_endpoints():

    try:
        service = EndpointFlowService()

        endpoints = (
            service.discover_endpoints()
        )

        return {
            "total": len(endpoints),
            "endpoints": endpoints
        }

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )


@router.get("/analyze")
def analyze_endpoint(
    http_method: str = Query(...),
    endpoint: str = Query(...)
):

    try:
        service = EndpointFlowService()

        return service.analyze_endpoint(
            http_method=http_method,
            endpoint=endpoint
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=404,
            detail=str(exception)
        )

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )
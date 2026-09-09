from fastapi import APIRouter, Query

from services.flow.code_flow_service import (
    CodeFlowService
)


router = APIRouter(
    prefix="/api/code-flow",
    tags=["Code Flow"]
)


@router.get("/analyze")
def analyze_flow(
    class_name: str = Query(...),
    method_name: str = Query(...)
):

    service = CodeFlowService()

    return service.analyze(
        class_name=class_name,
        method_name=method_name
    )
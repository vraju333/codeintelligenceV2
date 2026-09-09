from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from graph.investigation_graph import InvestigationGraph


router = APIRouter(
    prefix="/api/investigation",
    tags=["Investigation"]
)


class InvestigationRequest(BaseModel):
    input: Any = None
    expected: Any
    actual: Any


@router.post("/analyse")
def analyse_defect(
    request: InvestigationRequest,
    http_method: str = Query(...),
    endpoint: str = Query(...)
):

    investigation_graph = InvestigationGraph()

    return investigation_graph.investigate(
        http_method=http_method,
        endpoint=endpoint,
        input=request.input,
        expected=request.expected,
        actual=request.actual
    )
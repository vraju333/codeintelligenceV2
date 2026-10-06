from fastapi import APIRouter, HTTPException, Query
from services.flow.deep_code_intelligence_service import DeepCodeIntelligenceService

router = APIRouter(prefix="/api/deep-code-intelligence", tags=["Phase 7 - Deep Code Intelligence"])
service = DeepCodeIntelligenceService()

@router.get("/analyze")
def analyze(attribute: str = Query(..., min_length=1), project_path: str | None = None):
    try: return service.analyze(attribute, project_path)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc))

@router.get("/control-flow")
def control_flow(attribute: str = Query(..., min_length=1), project_path: str | None = None):
    try: return service.control_flow(attribute, project_path)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc))

@router.get("/data-flow")
def data_flow(attribute: str = Query(..., min_length=1), project_path: str | None = None):
    try: return service.data_flow(attribute, project_path)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc))

@router.get("/shared-impact")
def shared_impact(attribute: str = Query(..., min_length=1), project_path: str | None = None):
    try: return service.shared_impact(attribute, project_path)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc))

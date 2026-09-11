from typing import Any

from fastapi import (
    APIRouter,
    Depends
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from services.scenario.scenario_baseline_service import (
    ScenarioBaselineService
)


router = APIRouter(
    prefix="/api/scenario-baselines",
    tags=["Scenario Baselines"]
)

service = ScenarioBaselineService()


class CaptureBaselineRequest(BaseModel):

    successful_response: Any = None
    endpoint_flow: dict | None = None


class CreateTestingBaselineRequest(BaseModel):
    baseline_name: str
    request_json: Any = None
    expected_response: Any = None
    actual_response: Any = None
    expected_db_effect: str | None = None
    jira_ids: list[str] = []


@router.get("/overview")
def get_baseline_overview(
    db: Session = Depends(get_db)
):
    return service.get_overview(db)


@router.post("/capture/{scenario_id}")
def capture_baseline(
    scenario_id: int,
    request: CaptureBaselineRequest,
    db: Session = Depends(get_db)
):

    return service.capture(
        db=db,
        scenario_id=scenario_id,
        successful_response=request.successful_response,
        endpoint_flow=request.endpoint_flow
    )


@router.post("/testing/{scenario_id}")
def create_testing_baseline(
    scenario_id: int,
    request: CreateTestingBaselineRequest,
    db: Session = Depends(get_db)
):
    return service.create_test_baseline(
        db=db,
        scenario_id=scenario_id,
        baseline_name=request.baseline_name,
        request_json=request.request_json,
        expected_response=request.expected_response,
        actual_response=request.actual_response,
        expected_db_effect=request.expected_db_effect,
        jira_ids=request.jira_ids
    )


@router.get("/testing/{scenario_id}")
def get_testing_baselines(
    scenario_id: int,
    db: Session = Depends(get_db)
):
    return service.get_test_baselines(db, scenario_id)


@router.get("/latest/{scenario_id}")
def get_latest_baseline(
    scenario_id: int,
    db: Session = Depends(get_db)
):

    return service.get_latest(
        db=db,
        scenario_id=scenario_id
    )


@router.get("/history/{scenario_id}")
def get_baseline_history(
    scenario_id: int,
    db: Session = Depends(get_db)
):

    return service.get_history(
        db=db,
        scenario_id=scenario_id
    )

@router.get("/compare/{scenario_id}")
def compare_baseline_versions(
    scenario_id: int,
    from_version: int,
    to_version: int,
    db: Session = Depends(get_db)
):
    return service.compare_versions(
        db=db,
        scenario_id=scenario_id,
        from_version=from_version,
        to_version=to_version
    )

from typing import List

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from database import get_db
from schemas import ScenarioRequest, ScenarioResponse
from services.scenario.scenario_service import ScenarioService


router = APIRouter(
    prefix="/api/scenarios",
    tags=["Scenario Registry"]
)

service = ScenarioService()


@router.get("", response_model=List[ScenarioResponse])
def get_scenarios(db: Session = Depends(get_db)):
    return service.get_all(db)


@router.get("/page")
def get_scenario_page(
    page: int = 1,
    page_size: int = 5,
    db: Session = Depends(get_db)
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 50)
    return service.get_page_for_active_project(db, page, page_size)


@router.get("/active-project", response_model=List[ScenarioResponse])
def get_active_project_scenarios(db: Session = Depends(get_db)):
    """Return only scenarios that belong to endpoints discovered in the active Java project."""
    return service.get_all_for_active_project(db)


@router.get("/{scenario_id}", response_model=ScenarioResponse)
def get_scenario(scenario_id: int, db: Session = Depends(get_db)):
    return service.get_by_id(db, scenario_id)


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
def create_scenario(request: ScenarioRequest, db: Session = Depends(get_db)):
    return service.create(db, request)


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: int, db: Session = Depends(get_db)):
    service.delete(db, scenario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

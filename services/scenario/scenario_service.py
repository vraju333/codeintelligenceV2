from fastapi import HTTPException
from sqlalchemy.orm import Session

from repositories.scenario_repository import ScenarioRepository
from schemas import ScenarioRequest, ScenarioUpdateRequest
from services.flow.endpoint_flow_service import EndpointFlowService
from config import settings


class ScenarioService:

    def __init__(self):
        self.repository = ScenarioRepository()

    def get_all(self, db: Session):
        return self.repository.find_all(db)

    def get_page_for_active_project(self, db: Session, page: int, page_size: int):
        endpoints = EndpointFlowService().discover_endpoints()
        items, total = self.repository.find_page_for_endpoints(db, endpoints, page, page_size)
        total_pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "project_path": settings.JAVA_PROJECT_PATH,
        }

    def get_all_for_active_project(self, db: Session):
        endpoints = EndpointFlowService().discover_endpoints()
        return self.repository.find_all_for_endpoints(db, endpoints)

    def get_by_id(self, db: Session, scenario_id: int):
        scenario = self.repository.find_by_id(db, scenario_id)
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return scenario

    def find_existing_for_operation(self, db: Session, http_method: str, endpoint: str):
        method = str(http_method or "").upper().strip()
        path = str(endpoint or "").strip()
        if not method or not path:
            return []
        return self.repository.find_by_operation(
            db,
            method,
            path,
            settings.JAVA_PROJECT_PATH
        )

    def create(self, db: Session, request: ScenarioRequest):
        existing = self.repository.find_by_code(db, request.scenario_code)
        if existing:
            raise HTTPException(status_code=409, detail="Scenario code already exists")

        operation_matches = self.find_existing_for_operation(
            db, request.http_method, request.endpoint
        )
        if operation_matches:
            first = operation_matches[0]
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A scenario already exists for this operation. Open the existing scenario instead of creating a duplicate.",
                    "scenario_id": first.id,
                    "scenario_code": first.scenario_code,
                    "http_method": first.http_method,
                    "endpoint": first.endpoint,
                    "existing_count": len(operation_matches),
                }
            )

        return self.repository.create(db, request, settings.JAVA_PROJECT_PATH)

    def update(self, db: Session, scenario_id: int, request: ScenarioUpdateRequest):
        scenario = self.get_by_id(db, scenario_id)
        name = request.scenario_name if request.scenario_name is not None else scenario.scenario_name
        if not str(name or "").strip():
            raise HTTPException(status_code=400, detail="Scenario name is required")
        return self.repository.update(db, scenario, request)

    def delete(self, db: Session, scenario_id: int):
        scenario = self.get_by_id(db, scenario_id)
        self.repository.delete(db, scenario)

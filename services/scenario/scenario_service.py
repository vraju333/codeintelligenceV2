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

    @staticmethod
    def _operation_scenario_base(http_method: str) -> tuple[str, str]:
        method = str(http_method or "").upper().strip()
        mapping = {
            "GET": ("GET_DATA", "Get Data"),
            "POST": ("CREATE_DATA", "Create Data"),
            "PUT": ("UPDATE_DATA", "Update Data"),
            "PATCH": ("PATCH_DATA", "Patch Data"),
            "DELETE": ("DELETE_DATA", "Delete Data"),
        }
        return mapping.get(method, (f"{method or 'OPERATION'}_DATA", f"{method.title() or 'Operation'} Data"))

    @staticmethod
    def _endpoint_suffix(endpoint: str) -> str:
        import re
        value = re.sub(r"[^A-Za-z0-9]+", "_", str(endpoint or "")).strip("_").upper()
        return value[:60] or "ENDPOINT"

    def sync_discovered_operations(self, db: Session):
        """Create one high-level scenario for each discovered HTTP operation.

        Test variations (STUDENT_UPDATE, EMPLOYEE_UPDATE, etc.) belong under
        the release baseline as Test Baselines; they are not top-level scenarios.
        Existing operations are never duplicated or overwritten.
        """
        endpoints = EndpointFlowService().discover_endpoints()
        created = []
        existing = []

        for item in endpoints:
            method = str(item.get("http_method") or "").upper().strip()
            endpoint = str(item.get("endpoint") or "").strip()
            if not method or not endpoint:
                continue

            operation_matches = self.find_existing_for_operation(db, method, endpoint)
            if operation_matches:
                existing.append(operation_matches[0].scenario_code)
                continue

            base_code, base_name = self._operation_scenario_base(method)
            code = base_code
            if self.repository.find_by_code(db, code):
                code = f"{base_code}_{self._endpoint_suffix(endpoint)}"
                counter = 2
                candidate = code
                while self.repository.find_by_code(db, candidate):
                    candidate = f"{code}_{counter}"
                    counter += 1
                code = candidate

            request = ScenarioRequest(
                scenario_code=code,
                scenario_name=base_name,
                http_method=method,
                endpoint=endpoint,
                description=f"Auto-discovered operation scenario for {method} {endpoint}.",
                status="ACTIVE",
            )
            row = self.repository.create(db, request, settings.JAVA_PROJECT_PATH)
            created.append({
                "scenario_id": row.id,
                "scenario_code": row.scenario_code,
                "http_method": row.http_method,
                "endpoint": row.endpoint,
            })

        return {
            "created_count": len(created),
            "existing_count": len(existing),
            "created": created,
        }

    def create(self, db: Session, request: ScenarioRequest):
        existing = self.repository.find_by_code(db, request.scenario_code)
        if existing:
            raise HTTPException(status_code=409, detail="Scenario code already exists")

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

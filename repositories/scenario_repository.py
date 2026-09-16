from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from db_models import Scenario
from schemas import ScenarioRequest, ScenarioUpdateRequest


class ScenarioRepository:

    def find_all(self, db: Session):
        return db.query(Scenario).order_by(Scenario.id).all()


    def find_page_for_endpoints(self, db: Session, endpoints: list[dict], page: int, page_size: int):
        if not endpoints:
            return [], 0

        filters = []
        for item in endpoints:
            method = str(item.get("http_method", "")).upper()
            endpoint = str(item.get("endpoint", ""))
            if method and endpoint:
                filters.append(
                    and_(
                        Scenario.http_method == method,
                        Scenario.endpoint == endpoint
                    )
                )

        if not filters:
            return [], 0

        query = db.query(Scenario).filter(or_(*filters))
        total = query.count()
        items = (
            query.order_by(Scenario.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def find_all_for_endpoints(self, db: Session, endpoints: list[dict]):
        if not endpoints:
            return []
        filters = []
        for item in endpoints:
            method = str(item.get("http_method", "")).upper()
            endpoint = str(item.get("endpoint", ""))
            if method and endpoint:
                filters.append(and_(Scenario.http_method == method, Scenario.endpoint == endpoint))
        if not filters:
            return []
        return db.query(Scenario).filter(or_(*filters)).order_by(Scenario.id).all()

    def find_by_id(self, db: Session, scenario_id: int):
        return db.query(Scenario).filter(Scenario.id == scenario_id).first()

    def find_by_code(self, db: Session, scenario_code: str):
        return db.query(Scenario).filter(Scenario.scenario_code == scenario_code).first()

    def find_by_operation(self, db: Session, http_method: str, endpoint: str, project_path: str | None = None):
        query = db.query(Scenario).filter(
            Scenario.http_method == str(http_method).upper(),
            Scenario.endpoint == endpoint
        )
        if project_path:
            # New scenarios are project-bound. Legacy rows may have no project_path,
            # so include them to avoid accidentally creating another duplicate.
            query = query.filter(
                or_(Scenario.project_path == project_path, Scenario.project_path.is_(None))
            )
        return query.order_by(Scenario.id).all()

    def create(self, db: Session, request: ScenarioRequest, project_path: str | None = None):
        data = request.model_dump()
        if project_path:
            data["project_path"] = project_path
        scenario = Scenario(**data)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return scenario

    def update(self, db: Session, scenario: Scenario, request: ScenarioUpdateRequest):
        data = request.model_dump(exclude_unset=True)
        for field, value in data.items():
            if hasattr(scenario, field):
                setattr(scenario, field, value)
        db.commit()
        db.refresh(scenario)
        return scenario

    def delete(self, db: Session, scenario: Scenario):
        db.delete(scenario)
        db.commit()

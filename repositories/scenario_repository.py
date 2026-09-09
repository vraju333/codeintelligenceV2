from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from db_models import Scenario
from schemas import ScenarioRequest


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

    def create(self, db: Session, request: ScenarioRequest):
        scenario = Scenario(**request.model_dump())
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return scenario

    def delete(self, db: Session, scenario: Scenario):
        db.delete(scenario)
        db.commit()

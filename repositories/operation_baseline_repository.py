from sqlalchemy import desc
from sqlalchemy.orm import Session

from baseline_models import OperationBaseline, OperationBaselineSourceSnapshot


class OperationBaselineRepository:
    def find_latest(self, db: Session, project_path: str, http_method: str, endpoint: str):
        return (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.http_method == http_method,
                OperationBaseline.endpoint == endpoint,
            )
            .order_by(desc(OperationBaseline.baseline_version))
            .first()
        )

    def find_active(self, db: Session, project_path: str, http_method: str, endpoint: str):
        return (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.http_method == http_method,
                OperationBaseline.endpoint == endpoint,
                OperationBaseline.is_active.is_(True),
            )
            .first()
        )

    def find_history(self, db: Session, project_path: str, http_method: str, endpoint: str):
        return (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.http_method == http_method,
                OperationBaseline.endpoint == endpoint,
            )
            .order_by(desc(OperationBaseline.baseline_version))
            .all()
        )

    def find_by_version(self, db: Session, project_path: str, http_method: str, endpoint: str, version: int):
        return (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.http_method == http_method,
                OperationBaseline.endpoint == endpoint,
                OperationBaseline.baseline_version == version,
            )
            .first()
        )

    def deactivate_existing(self, db: Session, project_path: str, http_method: str, endpoint: str):
        rows = (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.http_method == http_method,
                OperationBaseline.endpoint == endpoint,
                OperationBaseline.is_active.is_(True),
            )
            .all()
        )
        for row in rows:
            row.is_active = False
        db.flush()

    def create(self, db: Session, row: OperationBaseline):
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def create_snapshot(self, db: Session, row: OperationBaselineSourceSnapshot):
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def find_snapshot(self, db: Session, baseline_id: int):
        return (
            db.query(OperationBaselineSourceSnapshot)
            .filter(OperationBaselineSourceSnapshot.baseline_id == baseline_id)
            .first()
        )

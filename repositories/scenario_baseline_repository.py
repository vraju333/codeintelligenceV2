from sqlalchemy import desc
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline, ScenarioBaselineSourceSnapshot, ScenarioTestBaseline


class ScenarioBaselineRepository:

    def find_all_for_scenario(self, db: Session, scenario_id: int):
        return (
            db.query(ScenarioBaseline)
            .filter(ScenarioBaseline.scenario_id == scenario_id)
            .order_by(desc(ScenarioBaseline.baseline_version))
            .all()
        )

    def find_all(self, db: Session):
        return (
            db.query(ScenarioBaseline)
            .order_by(ScenarioBaseline.scenario_id, desc(ScenarioBaseline.baseline_version))
            .all()
        )

    def find_all_active(self, db: Session):
        return (
            db.query(ScenarioBaseline)
            .filter(ScenarioBaseline.is_active.is_(True))
            .order_by(ScenarioBaseline.scenario_id)
            .all()
        )


    def find_by_version(self, db: Session, scenario_id: int, version: int):
        return (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id == scenario_id,
                ScenarioBaseline.baseline_version == version
            )
            .first()
        )


    def find_by_id(self, db: Session, scenario_id: int, baseline_id: int):
        return (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id == scenario_id,
                ScenarioBaseline.id == baseline_id
            )
            .first()
        )

    def find_release_versions(self, db: Session, scenario_id: int, release_name: str):
        return (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id == scenario_id,
                ScenarioBaseline.baseline_name == release_name
            )
            .order_by(ScenarioBaseline.release_version, ScenarioBaseline.baseline_version)
            .all()
        )

    def find_latest(self, db: Session, scenario_id: int):
        return (
            db.query(ScenarioBaseline)
            .filter(ScenarioBaseline.scenario_id == scenario_id)
            .order_by(desc(ScenarioBaseline.baseline_version))
            .first()
        )

    def find_active(self, db: Session, scenario_id: int):
        return (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id == scenario_id,
                ScenarioBaseline.is_active.is_(True)
            )
            .first()
        )

    def deactivate_existing(self, db: Session, scenario_id: int):
        baselines = (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id == scenario_id,
                ScenarioBaseline.is_active.is_(True)
            )
            .all()
        )
        for baseline in baselines:
            baseline.is_active = False
        db.flush()

    def create(self, db: Session, baseline: ScenarioBaseline):
        db.add(baseline)
        db.commit()
        db.refresh(baseline)
        return baseline



    def create_source_snapshot(
        self,
        db: Session,
        snapshot: ScenarioBaselineSourceSnapshot
    ):
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return snapshot

    def find_source_snapshot(
        self,
        db: Session,
        baseline_id: int
    ):
        return (
            db.query(ScenarioBaselineSourceSnapshot)
            .filter(
                ScenarioBaselineSourceSnapshot.baseline_id == baseline_id
            )
            .first()
        )


    def find_test_baselines(self, db: Session, scenario_id: int):
        return (
            db.query(ScenarioTestBaseline)
            .filter(ScenarioTestBaseline.scenario_id == scenario_id)
            .order_by(desc(ScenarioTestBaseline.created_at), desc(ScenarioTestBaseline.id))
            .all()
        )

    def find_test_baseline_by_name(self, db: Session, scenario_id: int, baseline_name: str, code_version: int | None = None):
        query = db.query(ScenarioTestBaseline).filter(
            ScenarioTestBaseline.scenario_id == scenario_id,
            ScenarioTestBaseline.baseline_name == baseline_name
        )
        if code_version is not None:
            query = query.filter(ScenarioTestBaseline.code_baseline_version == code_version)
        return query.first()


    def find_test_baselines_for_code_version(self, db: Session, scenario_id: int, version: int):
        return (
            db.query(ScenarioTestBaseline)
            .filter(
                ScenarioTestBaseline.scenario_id == scenario_id,
                ScenarioTestBaseline.code_baseline_version == version
            )
            .order_by(ScenarioTestBaseline.created_at, ScenarioTestBaseline.id)
            .all()
        )

    def find_test_baseline_for_code_version(self, db: Session, scenario_id: int, version: int):
        return (
            db.query(ScenarioTestBaseline)
            .filter(
                ScenarioTestBaseline.scenario_id == scenario_id,
                ScenarioTestBaseline.code_baseline_version == version
            )
            .order_by(desc(ScenarioTestBaseline.created_at), desc(ScenarioTestBaseline.id))
            .first()
        )

    def create_test_baseline(self, db: Session, baseline: ScenarioTestBaseline):
        db.add(baseline)
        db.commit()
        db.refresh(baseline)
        return baseline

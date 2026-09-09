from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ScenarioBaseline(Base):

    __tablename__ = "scenario_baselines"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    scenario_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scenarios.id"),
        nullable=False,
        index=True
    )

    baseline_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    scenario_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True
    )

    http_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )

    endpoint: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    expected_response_json: Mapped[dict | list | None] = mapped_column(
        JSON,
        nullable=True
    )

    successful_response_json: Mapped[dict | list | None] = mapped_column(
        JSON,
        nullable=True
    )

    expected_db_effect: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    involved_classes: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True
    )

    endpoint_flow: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )


class ScenarioBaselineSourceSnapshot(Base):

    __tablename__ = "scenario_baseline_source_snapshots"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    baseline_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scenario_baselines.id"),
        nullable=False,
        unique=True,
        index=True
    )

    project_path: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True
    )

    source_snapshot: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True
    )

    git_diff: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    source_changes: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

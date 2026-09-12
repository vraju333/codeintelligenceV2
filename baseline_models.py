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

    baseline_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    # Display version inside one release/month. Internal baseline_version remains
    # the globally unique sequence used by comparison/source snapshot APIs.
    release_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
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

class OperationBaseline(Base):
    """Approved baseline version for one project + HTTP operation.

    Multiple business scenarios/test cases can belong to the same operation.
    Re-running those scenarios never creates another version; a new version is
    created only when the relevant operation implementation changes.
    """

    __tablename__ = "operation_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_path: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    operation_key: Mapped[str] = mapped_column(String(1200), nullable=False, index=True)
    http_method: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scenario_contracts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    endpoint_flow: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    code_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    flow_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    combined_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class OperationBaselineSourceSnapshot(Base):
    __tablename__ = "operation_baseline_source_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    baseline_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("operation_baselines.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    source_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    git_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ScenarioTestBaseline(Base):
    """Periodic/repeatable test data captured for one operation scenario.

    ScenarioBaseline/OperationBaseline represent code/flow versions.
    ScenarioTestBaseline represents a test snapshot such as September 2026 or
    October 2026 and can continue to reference the same code baseline version.
    """

    __tablename__ = "scenario_test_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scenario_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scenarios.id"),
        nullable=False,
        index=True,
    )
    baseline_name: Mapped[str] = mapped_column(String(150), nullable=False)
    http_method: Mapped[str] = mapped_column(String(20), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    request_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    expected_response_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    actual_response_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    expected_db_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_RUN")
    code_baseline_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("scenario_baselines.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

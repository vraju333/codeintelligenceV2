"""Additive, project-scoped SDLC execution and release evidence tables."""
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from database import Base


def now():
    return datetime.now(timezone.utc)


class SdlcTestExecution(Base):
    __tablename__ = 'sdlc_test_executions'
    id = Column(Integer, primary_key=True)
    project_path = Column(String(1024), nullable=False, index=True)
    jira_key = Column(String(64), nullable=False, index=True)
    test_id = Column(String(200), nullable=False)
    status = Column(String(30), nullable=False)
    git_revision = Column(String(64), nullable=False)
    evidence_ref = Column(Text, nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=now, nullable=False)
    recorded_by = Column(String(200), nullable=False)
    notes = Column(Text, nullable=True)


class SdlcRelease(Base):
    __tablename__ = 'sdlc_releases'
    id = Column(Integer, primary_key=True)
    project_path = Column(String(1024), nullable=False, index=True)
    release_key = Column(String(150), nullable=False)
    jira_keys_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint('project_path', 'release_key', name='uq_sdlc_release_scope'),)


class SdlcReleaseSnapshot(Base):
    __tablename__ = 'sdlc_release_snapshots'
    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey('sdlc_releases.id'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    captured_at = Column(DateTime(timezone=True), default=now, nullable=False)
    captured_by = Column(String(200), nullable=False)
    __table_args__ = (UniqueConstraint('release_id', 'version', name='uq_sdlc_release_version'),)

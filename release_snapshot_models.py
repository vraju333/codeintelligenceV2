"""Immutable finalized release manifests (separate from legacy testing baselines)."""
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from database import Base

class ReleaseManifest(Base):
    __tablename__ = 'release_manifests'
    id = Column(Integer, primary_key=True)
    release_id = Column(Integer, ForeignKey('sdlc_releases.id'), nullable=False, index=True)
    manifest_sha256 = Column(String(64), nullable=False)
    manifest_json = Column(Text, nullable=False)
    finalized_by = Column(String(200), nullable=False)
    finalized_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    __table_args__ = (UniqueConstraint('release_id', name='uq_one_final_manifest_per_release'),)

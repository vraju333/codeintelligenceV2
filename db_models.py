from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from database import Base


class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True, index=True)
    scenario_code = Column(String(100), unique=True, nullable=False, index=True)
    scenario_name = Column(String(200), nullable=False)
    http_method = Column(String(20), nullable=False)
    endpoint = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    request_json = Column(Text, nullable=True)
    expected_response_json = Column(Text, nullable=True)
    expected_db_effect = Column(Text, nullable=True)
    involved_classes = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

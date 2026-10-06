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
    jira_id = Column(String(100), nullable=True, index=True)
    request_json = Column(Text, nullable=True)
    expected_response_json = Column(Text, nullable=True)
    expected_db_effect = Column(Text, nullable=True)
    involved_classes = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="ACTIVE")
    project_path = Column(Text, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class JiraKnowledge(Base):
    """Locally saved JIRA/requirement document used by the demo JIRA RAG index."""
    __tablename__ = "jira_knowledge"

    id = Column(Integer, primary_key=True, index=True)
    jira_id = Column(String(100), unique=True, nullable=False, index=True)
    title = Column(String(300), nullable=True)
    requirement = Column(Text, nullable=False)
    project_path = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class KnowledgeDocument(Base):
    """Engineering knowledge ingested from architecture/API/release/test sources."""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(50), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    content = Column(Text, nullable=False)
    project_path = Column(Text, nullable=False, index=True)
    source_ref = Column(String(500), nullable=True)
    metadata_json = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MappingDocument(Base):
    """Phase 6 authoritative mapping workbook with immutable provenance metadata."""
    __tablename__ = "mapping_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_path = Column(Text, nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    title = Column(String(500), nullable=False)
    mapping_family = Column(String(500), nullable=True, index=True)
    document_version = Column(String(100), nullable=True)
    source_ref = Column(String(1000), nullable=True)
    checksum_sha256 = Column(String(64), nullable=False, index=True)
    sheet_count = Column(Integer, nullable=False, default=0)
    row_count = Column(Integer, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MappingDefinition(Base):
    """One normalized mapping row. Sheet + row are preserved as evidence."""
    __tablename__ = "mapping_definitions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    project_path = Column(Text, nullable=False, index=True)
    sheet_name = Column(String(300), nullable=False)
    row_number = Column(Integer, nullable=False)
    source_type = Column(String(50), nullable=False, default="UNKNOWN", index=True)
    source_system = Column(String(300), nullable=True)
    source_path = Column(Text, nullable=False, index=True)
    mapping_rule = Column(Text, nullable=True)
    target_class = Column(String(500), nullable=True, index=True)
    target_attribute = Column(String(500), nullable=False, index=True)
    target_expression = Column(Text, nullable=False)
    null_rule = Column(Text, nullable=True)
    validation_rule = Column(Text, nullable=True)
    comments = Column(Text, nullable=True)
    raw_row_json = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

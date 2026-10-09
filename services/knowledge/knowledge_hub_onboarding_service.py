"""Project-scoped, evidence-checked Knowledge Hub connection workflow."""
import json
from pathlib import Path
from sqlalchemy.orm import Session
from db_models import KnowledgeSyncSource, JiraKnowledge, KnowledgeDocument, KnowledgeSyncRun
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService


def normalized(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").casefold()


class KnowledgeHubOnboardingService:
    def __init__(self):
        self.sync = EnterpriseKnowledgeSyncService()

    def connect(self, db: Session, payload: dict) -> dict:
        project = str(payload.get("project_path") or "").strip()
        if not project or project.startswith(("jira://", "enterprise://")):
            raise ValueError("Connect a knowledge source to a real indexed IntelliJ project path")
        source_type = str(payload.get("source_type") or "").upper()
        config = payload.get("config") or {}
        location = str(payload.get("location") or "").strip()
        if source_type == "JIRA" and not str(config.get("project_key") or "").strip():
            raise ValueError("Select a Jira project key")
        if source_type in {"DOCUMENT_FOLDER", "MAPPING_FOLDER"} and not Path(location).is_dir():
            raise ValueError("Selected source folder does not exist on the backend host")
        def identity(row):
            if row.source_type != source_type or normalized(row.project_path) != normalized(project):
                return False
            if source_type == "JIRA":
                try:
                    old = json.loads(row.config_json or "{}")
                except (TypeError, ValueError):
                    old = {}
                return str(old.get("project_key") or "").upper() == str(config.get("project_key") or "").upper()
            return normalized(row.location) == normalized(location)
        existing = next((r for r in db.query(KnowledgeSyncSource).all() if identity(r)), None)
        # Keep the original name/ID on reconnect. Names are globally unique in the legacy registry.
        name = existing.name if existing else str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Source name is required")
        conflict = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.name == name).first()
        if conflict is not None and (existing is None or conflict.id != existing.id):
            raise ValueError("Source name is already in use by another association")
        registration = self.sync.register(db, name=name, source_type=source_type,
            location=location, config=config, enabled=True, project_path=project)
        source_id = registration["source"]["id"]
        sync_result = self.sync.sync_source(db, source_id, trigger="KNOWLEDGE_HUB_CONNECT")
        stages = {"source": {"status": "SUCCESS", "id": source_id}, "sync": sync_result}
        source = db.query(KnowledgeSyncSource).filter_by(id=source_id).first()
        runs = db.query(KnowledgeSyncRun).filter_by(source_id=source_id).order_by(KnowledgeSyncRun.id.desc()).first()
        stages["postgresql"] = {
            "status": "VERIFIED" if source and normalized(source.project_path) == normalized(project)
                       and runs and runs.status == "SUCCESS" else "FAILED",
            "source_id": source_id, "run_id": runs.id if runs else None}
        if source_type == "JIRA":
            key = str(config["project_key"]).upper()
            count = db.query(JiraKnowledge).filter(
                JiraKnowledge.jira_id.like(key + "-%"),
                JiraKnowledge.project_path == project).count()
            stages["jira_records"] = {"status": "VERIFIED" if count else "NO_RECORDS", "count": count}
        elif source_type == "DOCUMENT_FOLDER":
            count = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.project_path == project,
                KnowledgeDocument.status == "ACTIVE").count()
            stages["documents"] = {"status": "VERIFIED" if count else "NO_RECORDS", "count": count}
        # Existing graph sync API is reused; do not infer Jira->Method edges from project ownership.
        try:
            from services.cross_project.engineering_knowledge_graph_service import EngineeringKnowledgeGraphService
            if source_type == "JIRA":
                stages["neo4j"] = sync_result.get("graph_ownership") or EngineeringKnowledgeGraphService().ensure_jira_project_links(db, project)
            else:
                stages["neo4j"] = {"status": "UNCHANGED", "detail": "No global graph rebuild required"}
        except Exception as exc:
            stages["neo4j"] = {"status": "FAILED", "error": str(exc)}
        index = sync_result.get("indexes", {}).get("enterprise_rag", {})
        stages["pgvector"] = {"status": index.get("status", "NOT_REBUILT"),
                              "details": index}
        failed = sync_result.get("status") != "SUCCESS" or stages["postgresql"]["status"] != "VERIFIED"
        failed = failed or stages["neo4j"].get("status") in {"FAILED", "UNAVAILABLE"}
        failed = failed or index.get("status") == "UNAVAILABLE"
        from services.knowledge.knowledge_hub_verification_service import verify_source
        stages["verification"] = verify_source(db, source_id)
        failed = failed or stages["verification"]["status"] != "VERIFIED"
        return {"status": "PARTIAL_SUCCESS" if failed else "CONNECTED",
                "project_path": project, "source_id": source_id, "stages": stages}

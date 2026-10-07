from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from config import settings
from db_models import (JiraKnowledge, KnowledgeDocument, KnowledgeSyncItem,
                       KnowledgeSyncRun, KnowledgeSyncSource)
from services.jira.jira_knowledge_service import JiraKnowledgeService
from services.knowledge.file_text_extractor import FileTextExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService
from services.lineage.mapping_intelligence_service import MappingIntelligenceService


class EnterpriseKnowledgeSyncService:
    SOURCE_TYPES = {"JIRA", "DOCUMENT_FOLDER", "MAPPING_FOLDER"}

    def __init__(self) -> None:
        self.extractor = FileTextExtractor()
        self.knowledge = KnowledgeIngestionService()
        self.mapping = MappingIntelligenceService()
        self.jira_knowledge = JiraKnowledgeService()

    @staticmethod
    def _project_path() -> str:
        value = getattr(settings, "PYTHON_PROJECT_PATH", None) or getattr(settings, "JAVA_PROJECT_PATH", None) or "ACTIVE_PROJECT"
        try:
            return str(Path(str(value)).expanduser().resolve())
        except Exception:
            return str(value)

    @staticmethod
    def _jira_project_key(jql: str | None) -> str | None:
        match = re.search(r"\bproject\s*=\s*[\"']?([A-Za-z][A-Za-z0-9_-]*)", str(jql or ""), flags=re.I)
        return match.group(1).upper() if match else None

    @classmethod
    def _source_project_scope(cls, source_type: str, config: dict[str, Any] | None) -> str:
        if str(source_type).upper() == "JIRA":
            key = cls._jira_project_key((config or {}).get("jql") or getattr(settings, "JIRA_SYNC_JQL", ""))
            if key:
                return f"jira://{key}"
        return cls._project_path()

    @staticmethod
    def _enabled(row: KnowledgeSyncSource) -> bool:
        return str(row.enabled or "true").lower() == "true"

    @staticmethod
    def _source_dict(row: KnowledgeSyncSource) -> dict[str, Any]:
        try:
            config = json.loads(row.config_json or "{}")
        except Exception:
            config = {}
        # Never return secrets. Connector credentials live in environment/configuration.
        return {"id": row.id, "name": row.name, "source_type": row.source_type,
                "location": row.location, "project_path": row.project_path,
                "config": config, "enabled": EnterpriseKnowledgeSyncService._enabled(row),
                "last_cursor": row.last_cursor, "last_sync_at": row.last_sync_at,
                "last_status": row.last_status, "last_error": row.last_error}

    def register(self, db: Session, *, name: str, source_type: str, location: str | None = None,
                 config: dict[str, Any] | None = None, enabled: bool = True) -> dict[str, Any]:
        source_type = str(source_type or "").strip().upper()
        if source_type not in self.SOURCE_TYPES:
            raise ValueError("source_type must be JIRA, DOCUMENT_FOLDER or MAPPING_FOLDER")
        name = str(name or "").strip()
        if not name:
            raise ValueError("name is required")
        row = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.name == name).first()
        if row is None:
            row = KnowledgeSyncSource(name=name, source_type=source_type)
            db.add(row)
        previous_scope = row.project_path
        new_scope = self._source_project_scope(source_type, config)
        row.source_type = source_type
        row.location = str(location or "").strip() or None
        row.project_path = new_scope
        row.config_json = json.dumps(config or {}, ensure_ascii=False)
        if previous_scope and previous_scope != new_scope:
            # Re-run this source once when its logical project binding changes.
            # This repairs rows created by older builds that bound Jira to JAVA_PROJECT_PATH.
            row.last_cursor = None
            db.query(KnowledgeSyncItem).filter(KnowledgeSyncItem.source_id == row.id).delete(synchronize_session=False)
        row.enabled = "true" if enabled else "false"
        db.commit(); db.refresh(row)
        return {"status": "REGISTERED", "source": self._source_dict(row)}

    def ensure_default_sources(self, db: Session) -> list[dict[str, Any]]:
        configured: list[dict[str, Any]] = []
        if settings.JIRA_LIVE_ENABLED and settings.JIRA_BASE_URL:
            configured.append(self.register(db, name="JIRA", source_type="JIRA", location=settings.JIRA_BASE_URL,
                                            config={"jql": settings.JIRA_SYNC_JQL})["source"])
        if settings.KNOWLEDGE_DOCUMENT_FOLDER:
            configured.append(self.register(db, name="Documents", source_type="DOCUMENT_FOLDER",
                                            location=settings.KNOWLEDGE_DOCUMENT_FOLDER)["source"])
        if settings.KNOWLEDGE_MAPPING_FOLDER:
            configured.append(self.register(db, name="Mappings", source_type="MAPPING_FOLDER",
                                            location=settings.KNOWLEDGE_MAPPING_FOLDER)["source"])
        return configured

    def list_sources(self, db: Session) -> dict[str, Any]:
        rows = db.query(KnowledgeSyncSource).order_by(KnowledgeSyncSource.id).all()
        return {"auto_sync_enabled": settings.KNOWLEDGE_AUTO_SYNC_ENABLED,
                "interval_seconds": settings.KNOWLEDGE_SYNC_INTERVAL_SECONDS,
                "sources": [self._source_dict(x) for x in rows]}

    def sync_all(self, db: Session, trigger: str = "MANUAL") -> dict[str, Any]:
        sources = db.query(KnowledgeSyncSource).order_by(KnowledgeSyncSource.id).all()
        results = []
        changed = False
        for source in sources:
            if not self._enabled(source):
                continue
            result = self.sync_source(db, source.id, trigger=trigger, rebuild_indexes=False)
            results.append(result)
            changed = changed or (result.get("created", 0) + result.get("updated", 0) > 0)
        indexes = self._refresh_indexes(db) if changed else {"status": "UNCHANGED"}
        return {"status": "SYNCED", "trigger": trigger, "sources": results, "indexes": indexes}

    def sync_source(self, db: Session, source_id: int, trigger: str = "MANUAL", rebuild_indexes: bool = True) -> dict[str, Any]:
        source = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == source_id).first()
        if source is None:
            raise ValueError(f"Knowledge sync source {source_id} was not found")
        run = KnowledgeSyncRun(source_id=source.id, trigger=trigger, status="RUNNING")
        db.add(run); db.commit(); db.refresh(run)
        try:
            if source.source_type == "JIRA": result = self._sync_jira(db, source)
            elif source.source_type == "DOCUMENT_FOLDER": result = self._sync_document_folder(db, source)
            elif source.source_type == "MAPPING_FOLDER": result = self._sync_mapping_folder(db, source)
            else: raise ValueError(f"Unsupported source type {source.source_type}")
            for key in ("discovered", "created", "updated", "skipped", "failed"):
                setattr(run, "created_count" if key == "created" else "updated_count" if key == "updated" else "skipped_count" if key == "skipped" else "failed_count" if key == "failed" else "discovered", int(result.get(key, 0)))
            run.status = "SUCCESS"; run.details_json = json.dumps(result, default=str, ensure_ascii=False); run.completed_at = datetime.utcnow()
            source.last_sync_at = datetime.utcnow(); source.last_status = "SUCCESS"; source.last_error = None
            db.commit()
            if rebuild_indexes and result.get("created", 0) + result.get("updated", 0) > 0:
                result["indexes"] = self._refresh_indexes(db)
            return {"source_id": source.id, "source": source.name, "source_type": source.source_type, **result}
        except Exception as exc:
            db.rollback()
            run = db.query(KnowledgeSyncRun).filter(KnowledgeSyncRun.id == run.id).first()
            source = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == source_id).first()
            if run:
                run.status = "FAILED"; run.failed_count = 1; run.details_json = json.dumps({"error": str(exc)}); run.completed_at = datetime.utcnow()
            if source:
                source.last_sync_at = datetime.utcnow(); source.last_status = "FAILED"; source.last_error = str(exc)
            db.commit()
            return {"source_id": source_id, "source": getattr(source, "name", None), "status": "FAILED", "error": str(exc), "created": 0, "updated": 0, "skipped": 0, "failed": 1}

    def _checkpoint(self, db: Session, source_id: int, external_id: str) -> KnowledgeSyncItem | None:
        return db.query(KnowledgeSyncItem).filter(KnowledgeSyncItem.source_id == source_id,
                                                  KnowledgeSyncItem.external_id == external_id).first()

    def _save_checkpoint(self, db: Session, source_id: int, external_id: str, token: str | None,
                         content_hash: str | None, local_ref: str | None) -> None:
        row = self._checkpoint(db, source_id, external_id)
        if row is None:
            row = KnowledgeSyncItem(source_id=source_id, external_id=external_id); db.add(row)
        row.version_token = token; row.content_hash = content_hash; row.local_ref = local_ref
        row.status = "ACTIVE"; row.last_seen_at = datetime.utcnow()

    @staticmethod
    def _adf_text(value: Any) -> str:
        if value is None: return ""
        if isinstance(value, str): return value
        if isinstance(value, list): return "\n".join(EnterpriseKnowledgeSyncService._adf_text(x) for x in value)
        if isinstance(value, dict):
            text = str(value.get("text") or "")
            children = EnterpriseKnowledgeSyncService._adf_text(value.get("content") or [])
            return " ".join(x for x in (text, children) if x).strip()
        return str(value)

    def _jira_request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        base = settings.JIRA_BASE_URL.rstrip("/")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "CodeIntelligence/KnowledgeSync"}
        if settings.JIRA_EMAIL or settings.JIRA_API_TOKEN:
            if not settings.JIRA_EMAIL or not settings.JIRA_API_TOKEN:
                raise ValueError("Set both JIRA_EMAIL and JIRA_API_TOKEN")
            raw = f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, headers=headers, data=data, method="POST" if data is not None else "GET")
        try:
            with urlopen(req, timeout=30) as response: return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Jira sync failed ({exc.code}): {body[:700]}") from exc
        except URLError as exc:
            raise RuntimeError(f"Unable to connect to Jira: {exc.reason}") from exc

    def _sync_jira(self, db: Session, source: KnowledgeSyncSource) -> dict[str, Any]:
        if not settings.JIRA_LIVE_ENABLED: raise ValueError("JIRA_LIVE_ENABLED=true is required")
        config = json.loads(source.config_json or "{}")
        base_jql = str(config.get("jql") or settings.JIRA_SYNC_JQL or "ORDER BY updated ASC").strip()
        # Cursor is an updated timestamp. Keep the user JQL and add an incremental updated constraint.
        cursor = source.last_cursor
        jql = base_jql
        if cursor:
            constraint = f'updated >= "{cursor}"'
            if re.search(r"\border\s+by\b", jql, flags=re.I):
                parts = re.split(r"\border\s+by\b", jql, maxsplit=1, flags=re.I)
                jql = f"({parts[0].strip()}) AND {constraint} ORDER BY {parts[1].strip()}"
            else: jql = f"({jql}) AND {constraint}"
        issues: list[dict[str, Any]] = []; next_token = None
        while True:
            payload: dict[str, Any] = {"jql": jql, "maxResults": 100,
                "fields": ["summary","description","status","issuetype","project","components","labels","priority","fixVersions","updated"]}
            if next_token: payload["nextPageToken"] = next_token
            page = self._jira_request("/rest/api/3/search/jql", payload)
            issues.extend(page.get("issues") or [])
            next_token = page.get("nextPageToken")
            if not next_token: break
        created=updated=skipped=0; newest=cursor
        for issue in issues:
            key=str(issue.get("key") or "").upper(); fields=issue.get("fields") or {}; changed=str(fields.get("updated") or "")
            if not key: continue
            cp=self._checkpoint(db,source.id,key)
            if cp and cp.version_token == changed: skipped += 1; continue
            title=str(fields.get("summary") or "")
            description=self._adf_text(fields.get("description"))
            project = fields.get("project") or {}
            project_key = str(project.get("key") or self._jira_project_key(base_jql) or "").upper()
            project_name = str(project.get("name") or "").strip()
            extras={"project_key": project_key, "project_name": project_name,
                    "status": (fields.get("status") or {}).get("name"), "issue_type": (fields.get("issuetype") or {}).get("name"),
                    "labels": fields.get("labels") or [], "components": [x.get("name") for x in fields.get("components") or []],
                    "fix_versions": [x.get("name") for x in fields.get("fixVersions") or []]}
            requirement=(description + "\n\nJira metadata: " + json.dumps(extras, ensure_ascii=False)).strip()
            jira_scope = f"jira://{project_key}" if project_key else str(source.project_path or self._source_project_scope("JIRA", config))
            row=db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == key).first()
            if row is None:
                row=JiraKnowledge(jira_id=key,title=title,requirement=requirement,project_path=jira_scope); db.add(row); created += 1
            else:
                row.title=title; row.requirement=requirement; row.project_path=jira_scope; updated += 1
            self._save_checkpoint(db,source.id,key,changed,hashlib.sha256(requirement.encode()).hexdigest(),key)
            if changed and (not newest or changed > newest): newest=changed
        source.last_cursor=newest; db.commit()
        if created+updated: self.jira_knowledge.rebuild_index(db)
        return {"status":"SUCCESS","discovered":len(issues),"created":created,"updated":updated,"skipped":skipped,"failed":0,"cursor":newest}

    def _sync_document_folder(self, db: Session, source: KnowledgeSyncSource) -> dict[str, Any]:
        folder=Path(str(source.location or "")).expanduser()
        if not folder.exists() or not folder.is_dir(): raise ValueError(f"Document folder does not exist: {folder}")
        files=[p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in self.extractor.SUPPORTED_EXTENSIONS]
        created=updated=skipped=failed=0
        for file in files:
            try:
                data=file.read_bytes(); digest=hashlib.sha256(data).hexdigest(); external=str(file.resolve())
                cp=self._checkpoint(db,source.id,external)
                if cp and cp.content_hash == digest: cp.last_seen_at=datetime.utcnow(); skipped += 1; continue
                content=self.extractor.extract(file.name,data)
                previous=db.query(KnowledgeDocument).filter(KnowledgeDocument.source_ref == external, KnowledgeDocument.status == "ACTIVE").all()
                for row in previous: row.status="SUPERSEDED"
                result=self.knowledge.ingest(db,source_type="REQUIREMENT",title=file.stem,content=content,source_ref=external,
                    metadata={"sync":{"source_id":source.id,"source_name":source.name,"file_name":file.name,"modified_ns":file.stat().st_mtime_ns,"sha256":digest}})
                self._save_checkpoint(db,source.id,external,str(file.stat().st_mtime_ns),digest,str((result.get("document") or {}).get("id")))
                if cp: updated += 1
                else: created += 1
            except Exception: failed += 1
        db.commit()
        return {"status":"SUCCESS" if not failed else "PARTIAL","discovered":len(files),"created":created,"updated":updated,"skipped":skipped,"failed":failed}

    @staticmethod
    def _mapping_identity(file: Path) -> tuple[str, str | None]:
        match=re.search(r"(?:^|[_ .-])(V\d+(?:\.\d+)*)\b",file.stem,flags=re.I)
        version=match.group(1).upper() if match else None
        family=re.sub(r"(?:^|[_ .-])V\d+(?:\.\d+)*\b","",file.stem,flags=re.I).strip(" _.-") or file.stem
        return family,version

    def _sync_mapping_folder(self, db: Session, source: KnowledgeSyncSource) -> dict[str, Any]:
        folder=Path(str(source.location or "")).expanduser()
        if not folder.exists() or not folder.is_dir(): raise ValueError(f"Mapping folder does not exist: {folder}")
        files=[p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".xlsx",".xlsm"}]
        created=updated=skipped=failed=0
        for file in files:
            try:
                data=file.read_bytes(); digest=hashlib.sha256(data).hexdigest(); external=str(file.resolve()); cp=self._checkpoint(db,source.id,external)
                if cp and cp.content_hash == digest: cp.last_seen_at=datetime.utcnow(); skipped += 1; continue
                family,version=self._mapping_identity(file)
                result=self.mapping.import_excel(db,filename=file.name,content=data,title=file.stem,document_version=version,
                    mapping_family=family,source_ref=external)
                self._save_checkpoint(db,source.id,external,str(file.stat().st_mtime_ns),digest,str((result.get("document") or {}).get("id")))
                if result.get("status") == "ALREADY_IMPORTED": skipped += 1
                elif cp: updated += 1
                else: created += 1
            except Exception: failed += 1
        db.commit()
        return {"status":"SUCCESS" if not failed else "PARTIAL","discovered":len(files),"created":created,"updated":updated,"skipped":skipped,"failed":failed}

    @staticmethod
    def _refresh_indexes(db: Session) -> dict[str, Any]:
        results: dict[str, Any] = {}
        try:
            from services.retrieval.enterprise_hybrid_rag_service import EnterpriseHybridRagService
            results["enterprise_rag"] = EnterpriseHybridRagService().rebuild(db)
        except Exception as exc: results["enterprise_rag"]={"status":"UNAVAILABLE","error":str(exc)}
        return results

    def recent_runs(self, db: Session, limit: int = 25) -> dict[str, Any]:
        rows=db.query(KnowledgeSyncRun).order_by(KnowledgeSyncRun.id.desc()).limit(max(1,min(limit,100))).all()
        return {"runs":[{"id":x.id,"source_id":x.source_id,"trigger":x.trigger,"status":x.status,
                         "discovered":x.discovered,"created":x.created_count,"updated":x.updated_count,
                         "skipped":x.skipped_count,"failed":x.failed_count,"started_at":x.started_at,"completed_at":x.completed_at}
                        for x in rows]}

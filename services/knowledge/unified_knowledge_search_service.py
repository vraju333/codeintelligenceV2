from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from db_models import KnowledgeDocument
from services.knowledge.common_entity_extractor import CommonKnowledgeEntityExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService


class UnifiedKnowledgeSearchService:
    """Deterministic unified knowledge search: RAG discovery -> graph expansion -> DB evidence."""

    ENTITY_FIELDS = (
        "jira_ids", "attributes", "scenarios", "endpoints", "http_methods",
        "classes", "methods", "release_names", "tests", "commits", "files",
    )

    def __init__(self):
        self.knowledge = KnowledgeIngestionService()
        self.extractor = CommonKnowledgeEntityExtractor()

    def search(self, db: Session, query: str, top_k: int = 5) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")

        rag = self.knowledge.search(db, query, top_k)
        discovered = {field: [] for field in self.ENTITY_FIELDS}

        # Explicit IDs/labels in the user's question should become seeds even if
        # semantic retrieval ranks another chunk first.
        direct = self.extractor.extract(query, "REQUIREMENT")
        self._merge_entities(discovered, direct)
        for match in rag.get("results", []):
            self._merge_entities(discovered, match.get("entities") or {})

        graph = self._expand_graph(discovered)
        self._merge_entities(discovered, graph.get("entities") or {})

        evidence_ids = set(graph.get("document_ids") or [])
        evidence_ids.update(
            int(x["knowledge_document_id"])
            for x in rag.get("results", [])
            if x.get("knowledge_document_id") is not None
        )

        # PostgreSQL is the authoritative document store. Also expand by common
        # metadata so V1 still works when Neo4j is temporarily unavailable.
        rows = self._project_rows(db)
        seed_values = self._entity_values(discovered)
        for row in rows:
            entities = self._row_entities(row)
            if seed_values.intersection(self._entity_values(entities)):
                evidence_ids.add(row.id)
                self._merge_entities(discovered, entities)

        evidence = [self._evidence(row) for row in rows if row.id in evidence_ids]
        knowledge = defaultdict(list)
        for item in evidence:
            knowledge[self._bucket(item["source_type"])].append(item)

        return {
            "query": query,
            "discovered_entities": discovered,
            "knowledge": dict(knowledge),
            "evidence": evidence,
            "evidence_count": len(evidence),
            "rag_matches": rag.get("results", []),
            "graph": graph,
        }

    def _project_rows(self, db: Session) -> list[KnowledgeDocument]:
        project_path = self.knowledge._project_path()
        return (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_path == project_path)
            .filter(KnowledgeDocument.status == "ACTIVE")
            .order_by(KnowledgeDocument.id.asc())
            .all()
        )

    def _expand_graph(self, seeds: dict[str, list[str]]) -> dict[str, Any]:
        if not getattr(settings, "NEO4J_ENABLED", False):
            return {"enabled": False, "status": "DISABLED", "document_ids": [], "entities": {}}
        names = sorted(self._entity_values(seeds))
        if not names:
            return {"enabled": True, "status": "NO_SEEDS", "document_ids": [], "entities": {}}
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
            )
            project_name = Path(self.knowledge._project_path()).name
            cypher = (
                "MATCH (seed:CodeIntelligence:KnowledgeEntity) "
                "WHERE seed.project=$project AND seed.name IN $names "
                "MATCH (d:CodeIntelligence:KnowledgeDocument)-[:MENTIONS]->(seed) "
                "OPTIONAL MATCH (d)-[:MENTIONS]->(e:CodeIntelligence:KnowledgeEntity) "
                "RETURN DISTINCT d.id AS document_id, e.type AS entity_type, e.name AS entity_name"
            )
            doc_ids: set[int] = set()
            entities: dict[str, list[str]] = {field: [] for field in self.ENTITY_FIELDS}
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                for record in session.run(cypher, project=project_name, names=names):
                    raw_id = record.get("document_id") or ""
                    if str(raw_id).startswith("knowledge:"):
                        try:
                            doc_ids.add(int(str(raw_id).split(":", 1)[1]))
                        except ValueError:
                            pass
                    field = self._field_for_type(record.get("entity_type"))
                    value = record.get("entity_name")
                    if field and value and value not in entities[field]:
                        entities[field].append(value)
            driver.close()
            return {
                "enabled": True,
                "status": "EXPANDED",
                "seed_entities": names,
                "document_ids": sorted(doc_ids),
                "entities": entities,
            }
        except Exception as exc:
            return {"enabled": True, "status": "UNAVAILABLE", "error": str(exc), "document_ids": [], "entities": {}}

    @classmethod
    def _merge_entities(cls, target: dict[str, list[str]], source: dict[str, Any]) -> None:
        for field in cls.ENTITY_FIELDS:
            for value in source.get(field, []) or []:
                value = str(value).strip()
                if value and value not in target[field]:
                    target[field].append(value)

    @staticmethod
    def _row_entities(row: KnowledgeDocument) -> dict[str, Any]:
        try:
            return (json.loads(row.metadata_json or "{}").get("extracted") or {})
        except Exception:
            return {}

    @staticmethod
    def _entity_values(entities: dict[str, Any]) -> set[str]:
        return {str(v).strip() for values in entities.values() if isinstance(values, list) for v in values if str(v).strip()}

    @staticmethod
    def _evidence(row: KnowledgeDocument) -> dict[str, Any]:
        return {
            "id": row.id,
            "source_type": row.source_type,
            "title": row.title,
            "source_ref": row.source_ref,
            "content": row.content,
            "entities": UnifiedKnowledgeSearchService._row_entities(row),
        }

    @staticmethod
    def _bucket(source_type: str) -> str:
        return {
            "REQUIREMENT": "requirements", "JIRA": "requirements",
            "TEST": "tests", "TEST_REPORT": "tests",
            "CODE_CHANGE": "code_changes", "ARCHITECTURE": "architecture",
            "API": "api_docs", "RELEASE": "releases",
        }.get(source_type, source_type.lower())

    @staticmethod
    def _field_for_type(entity_type: str | None) -> str | None:
        return {
            "JIRA": "jira_ids", "ATTRIBUTE": "attributes", "SCENARIO": "scenarios",
            "ENDPOINT": "endpoints", "HTTP_METHOD": "http_methods", "CLASS": "classes",
            "METHOD": "methods", "RELEASE": "release_names", "TEST": "tests",
            "COMMIT": "commits", "FILE": "files",
        }.get(str(entity_type or "").upper())

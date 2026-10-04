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
from services.jira.jira_knowledge_service import jira_knowledge_service


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

        rows = self._project_rows(db)

        # Explicit entities in the question are authoritative candidates.
        direct = self.extractor.extract(query, "REQUIREMENT")
        exact_seeds = {field: [] for field in self.ENTITY_FIELDS}
        self._merge_entities(exact_seeds, direct)

        # The existing CodeIntelligence JIRA registry is a first-class knowledge
        # source. For an explicit JIRA id, resolve that registry BEFORE semantic
        # document retrieval so unrelated RAG hits cannot redefine the JIRA.
        jira_evidence: list[dict[str, Any]] = []
        requested_jiras = {
            str(value).strip().upper()
            for value in (exact_seeds.get("jira_ids") or [])
            if str(value).strip()
        }
        if requested_jiras:
            jira_candidates = jira_knowledge_service.search(db, query, top_k=max(top_k, 8))
            active_project = str(self.knowledge._project_path() or "").lower()

            for jira in jira_candidates or []:
                jira_id = str(jira.get("jira_id") or "").strip().upper()
                jira_project = str(jira.get("project_path") or "").lower()
                if jira_id not in requested_jiras:
                    continue
                if jira_project and active_project and jira_project != active_project:
                    continue

                title = str(jira.get("title") or "").strip()
                requirement = str(jira.get("requirement") or "").strip()
                jira_entities = self.extractor.extract(
                    "\n".join(
                        part for part in (
                            f"JIRA: {jira_id}",
                            title,
                            requirement,
                        )
                        if part
                    ),
                    "JIRA",
                )
                self._merge_entities(exact_seeds, jira_entities)
                if jira_id and jira_id not in exact_seeds["jira_ids"]:
                    exact_seeds["jira_ids"].append(jira_id)

                jira_evidence.append({
                    "id": f"jira:{jira_id}",
                    "source_type": "JIRA",
                    "title": title or jira_id,
                    "source_ref": jira_id,
                    "content": requirement,
                    "project_path": jira.get("project_path"),
                    "entities": jira_entities,
                })

        exact_values = self._entity_values(exact_seeds)
        exact_rows: list[KnowledgeDocument] = []
        if exact_values:
            for row in rows:
                row_entities = self._row_entities(row)
                row_values = self._entity_values(row_entities)
                if exact_values.intersection(row_values):
                    exact_rows.append(row)

        # Keep semantic retrieval available for diagnostics/support. It must not
        # establish relationships when an exact JIRA/entity has been resolved.
        rag = self.knowledge.search(db, query, top_k)

        exact_authority = bool(jira_evidence or exact_rows)
        if exact_authority:
            discovered = {field: [] for field in self.ENTITY_FIELDS}
            self._merge_entities(discovered, exact_seeds)
            for row in exact_rows:
                self._merge_entities(discovered, self._row_entities(row))

            graph = self._expand_graph(discovered)
            self._merge_entities(discovered, graph.get("entities") or {})

            evidence_ids = {row.id for row in exact_rows}
            evidence_ids.update(graph.get("document_ids") or [])

            # DB expansion is permitted only from the authoritative exact seed
            # set and graph-derived entities, never from semantic RAG candidates.
            seed_values = self._entity_values(discovered)
            for row in rows:
                entities = self._row_entities(row)
                if seed_values.intersection(self._entity_values(entities)):
                    evidence_ids.add(row.id)

            retrieval_mode = "EXACT_ENTITY"
        else:
            # Natural-language query: retain semantic discovery behavior.
            discovered = {field: [] for field in self.ENTITY_FIELDS}
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

            seed_values = self._entity_values(discovered)
            for row in rows:
                entities = self._row_entities(row)
                if seed_values.intersection(self._entity_values(entities)):
                    evidence_ids.add(row.id)
                    self._merge_entities(discovered, entities)

            retrieval_mode = "SEMANTIC"

        evidence = list(jira_evidence)
        evidence.extend(self._evidence(row) for row in rows if row.id in evidence_ids)

        knowledge = defaultdict(list)
        for item in evidence:
            knowledge[self._bucket(item["source_type"])].append(item)

        return {
            "query": query,
            "retrieval_mode": retrieval_mode,
            "discovered_entities": discovered,
            "knowledge": dict(knowledge),
            "evidence": evidence,
            "evidence_count": len(evidence),
            "rag_matches": rag.get("results", []),
            "graph": graph,
        }


    def correlate_concept(self, db: Session, concept: str, top_k: int = 8) -> dict[str, Any]:
        """Return only evidence that explicitly supports a relationship to concept.

        Semantic similarity is discovery only. A document/JIRA is confirmed only when
        its extracted entities explicitly contain the requested concept.
        """
        concept = str(concept or "").strip()
        if not concept:
            raise ValueError("concept is required")

        normalized = self._normalize_entity(concept)
        result = self.search(db, concept, top_k=max(1, min(int(top_k), 10)))
        confirmed: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []

        def classify(item: dict[str, Any], origin: str) -> None:
            entities = item.get("entities") or {}
            values = {self._normalize_entity(v) for v in self._entity_values(entities)}
            enriched = dict(item)
            enriched["origin"] = origin
            if normalized in values:
                enriched["relationship"] = "CONFIRMED"
                enriched["confidence"] = "HIGH"
                enriched["reason"] = f"Explicit entity match for '{concept}'."
                confirmed.append(enriched)
            else:
                enriched["relationship"] = "CANDIDATE_ONLY"
                enriched["confidence"] = "LOW"
                enriched["reason"] = "Semantic/graph discovery without an explicit entity match."
                candidates.append(enriched)

        seen = set()
        for item in result.get("evidence", []) or []:
            key = (str(item.get("source_type")), str(item.get("id")))
            if key not in seen:
                seen.add(key)
                classify(item, "KNOWLEDGE")

        # Live JIRA is also searched by concept. It is not trusted merely because
        # semantic search returned it: the extracted JIRA entities must contain
        # the exact changed concept.
        for jira in jira_knowledge_service.search(db, concept, top_k=max(top_k, 8)) or []:
            jira_id = str(jira.get("jira_id") or "").strip().upper()
            title = str(jira.get("title") or "").strip()
            requirement = str(jira.get("requirement") or "").strip()
            entities = self.extractor.extract(
                "\n".join(x for x in (f"JIRA: {jira_id}", title, requirement) if x),
                "JIRA",
            )
            item = {
                "id": f"jira:{jira_id}",
                "source_type": "JIRA",
                "title": title or jira_id,
                "source_ref": jira_id,
                "content": requirement,
                "project_path": jira.get("project_path"),
                "entities": entities,
            }
            key = ("JIRA", item["id"])
            if key not in seen:
                seen.add(key)
                classify(item, "LIVE_JIRA")

        return {
            "concept": concept,
            "relationship_status": "CONFIRMED" if confirmed else "NO_CONFIRMED_RELATIONSHIP",
            "confirmed_evidence": confirmed,
            "candidate_evidence": candidates,
            "confirmed_count": len(confirmed),
            "candidate_count": len(candidates),
        }

    @staticmethod
    def _normalize_entity(value: Any) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

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

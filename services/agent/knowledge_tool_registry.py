from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from services.knowledge.common_entity_extractor import CommonKnowledgeEntityExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService
from services.knowledge.unified_knowledge_search_service import UnifiedKnowledgeSearchService


class KnowledgeToolRegistry:
    """Build the tools exposed to the knowledge agent for one DB request."""

    def __init__(self, db: Session):
        self.db = db
        self.knowledge = KnowledgeIngestionService()
        self.unified = UnifiedKnowledgeSearchService()
        self.extractor = CommonKnowledgeEntityExtractor()

    def tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.rag_search,
                name="rag_search",
                description=(
                    "Semantic search over ingested engineering knowledge documents. "
                    "Use when the user describes a concept in natural language or asks "
                    "which documents discuss a topic and no exact engineering ID is required."
                ),
            ),
            StructuredTool.from_function(
                func=self.graph_search,
                name="graph_search",
                description=(
                    "Expand relationships for known engineering entities such as a JIRA ID, "
                    "attribute, scenario, class, method, test, commit, file, endpoint or release. "
                    "Best when the user asks what is connected/related to a known entity."
                ),
            ),
            StructuredTool.from_function(
                func=self.unified_knowledge_search,
                name="unified_knowledge_search",
                description=(
                    "Return complete cross-source engineering knowledge for a topic by combining "
                    "semantic RAG discovery, Neo4j relationship expansion and PostgreSQL evidence. "
                    "Use for broad requests such as complete impact, everything about a JIRA, "
                    "or a consolidated requirement/test/code-change view."
                ),
            ),
        ]

    def rag_search(self, query: str, top_k: int = 5) -> str:
        """Search engineering knowledge semantically."""
        result = self.knowledge.search(self.db, query, top_k=max(1, min(int(top_k), 10)))
        return json.dumps(result, default=str)

    def graph_search(self, entity: str) -> str:
        """Find knowledge documents and entities connected to an exact engineering entity."""
        direct = self.extractor.extract(str(entity or ""), "REQUIREMENT")
        seeds = {field: [] for field in self.unified.ENTITY_FIELDS}
        self.unified._merge_entities(seeds, direct)

        # If the generic extractor cannot classify a value (for example a method
        # name), seed all entity fields with it. Neo4j still performs an exact
        # entity-name match, so this does not broaden the graph result incorrectly.
        if not self.unified._entity_values(seeds):
            value = str(entity or "").strip()
            if value:
                for field in self.unified.ENTITY_FIELDS:
                    seeds[field] = [value]

        graph = self.unified._expand_graph(seeds)
        rows = self.unified._project_rows(self.db)
        ids = set(graph.get("document_ids") or [])
        evidence = [self.unified._evidence(row) for row in rows if row.id in ids]
        return json.dumps({"entity": entity, "graph": graph, "evidence": evidence}, default=str)

    def unified_knowledge_search(self, query: str, top_k: int = 5) -> str:
        """Search all connected knowledge and return consolidated evidence."""
        result = self.unified.search(self.db, query, top_k=max(1, min(int(top_k), 10)))
        return json.dumps(result, default=str)

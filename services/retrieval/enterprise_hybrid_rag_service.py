from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_huggingface import HuggingFaceEmbeddings
from rank_bm25 import BM25Okapi
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline, ScenarioTestBaseline
from config import settings
from db_models import JiraKnowledge, KnowledgeDocument, MappingDefinition, MappingDocument, Scenario


@dataclass
class RetrievalDocument:
    document_id: str
    source_type: str
    title: str
    content: str
    project_path: str | None
    metadata: dict[str, Any]


class EnterpriseHybridRagService:
    """Phase 8 production retrieval: pgvector + BM25 + metadata + reranking.

    PostgreSQL/pgvector is the persistent vector store. BM25 is built from the
    same persisted corpus for lexical precision. Neo4j/AST/DB tools remain the
    authoritative verification layer; retrieval results are discovery evidence.
    """

    TABLE = "engineering_knowledge_vectors"
    EMBEDDING_DIM = 384  # all-MiniLM-L6-v2

    def __init__(self) -> None:
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )

    def _pg_url(self) -> str:
        url = str(getattr(settings, "POSTGRES_DATABASE_URL", "") or "").strip()
        if not url:
            raise RuntimeError(
                "Phase 8 requires PostgreSQL. Configure POSTGRES_DATABASE_URL (or DATABASE_URL)."
            )
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise RuntimeError("Phase 8 pgvector requires a PostgreSQL connection URL.")
        return url

    def _engine(self):
        return create_engine(self._pg_url(), pool_pre_ping=True)

    def ensure_schema(self) -> dict[str, Any]:
        engine = self._engine()
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            conn.exec_driver_sql(f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    document_id VARCHAR(160) NOT NULL UNIQUE,
                    source_type VARCHAR(80) NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    project_path TEXT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    content_hash VARCHAR(64) NOT NULL,
                    embedding vector({self.EMBEDDING_DIM}) NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{self.TABLE}_source_type ON {self.TABLE}(source_type)"
            )
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_{self.TABLE}_project_path ON {self.TABLE}(project_path)"
            )
        return {"status": "READY", "backend": "POSTGRESQL_PGVECTOR", "table": self.TABLE}

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, default=str, ensure_ascii=False)

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9_./:-]+", str(value or "").lower())

    @staticmethod
    def _doc_id(source_type: str, key: Any) -> str:
        raw = f"{source_type}:{key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]

    def _collect_documents(self, db: Session) -> list[RetrievalDocument]:
        docs: list[RetrievalDocument] = []

        for row in db.query(KnowledgeDocument).filter(KnowledgeDocument.status == "ACTIVE").all():
            meta = json.loads(row.metadata_json) if row.metadata_json else {}
            docs.append(RetrievalDocument(self._doc_id("KNOWLEDGE", row.id), row.source_type.upper(), row.title,
                                          row.content, row.project_path, {**meta, "source_ref": row.source_ref, "db_id": row.id}))

        for row in db.query(JiraKnowledge).all():
            content = f"JIRA {row.jira_id}. {row.title or ''}. {row.requirement}"
            docs.append(RetrievalDocument(self._doc_id("JIRA", row.id), "JIRA", row.jira_id, content,
                                          row.project_path, {"jira_id": row.jira_id, "db_id": row.id}))

        for row in db.query(Scenario).all():
            content = " ".join(str(v or "") for v in [row.scenario_code, row.scenario_name, row.http_method,
                                                        row.endpoint, row.description, row.jira_id,
                                                        row.involved_classes, row.expected_db_effect])
            docs.append(RetrievalDocument(self._doc_id("SCENARIO", row.id), "SCENARIO", row.scenario_code,
                                          content, row.project_path, {"scenario_id": row.id, "jira_id": row.jira_id,
                                                                      "http_method": row.http_method, "endpoint": row.endpoint}))

        mapping_docs = {r.id: r for r in db.query(MappingDocument).all()}
        for row in db.query(MappingDefinition).filter(MappingDefinition.status == "ACTIVE").all():
            parent = mapping_docs.get(row.document_id)
            content = " ".join(str(v or "") for v in [row.source_type, row.source_system, row.source_path,
                                                        row.mapping_rule, row.target_class, row.target_attribute,
                                                        row.target_expression, row.null_rule, row.validation_rule,
                                                        row.comments, getattr(parent, "mapping_family", None),
                                                        getattr(parent, "document_version", None)])
            docs.append(RetrievalDocument(self._doc_id("MAPPING", row.id), "MAPPING", f"{row.source_path} -> {row.target_expression}",
                                          content, row.project_path, {"mapping_id": row.id, "document_id": row.document_id,
                                                                      "mapping_family": getattr(parent, "mapping_family", None),
                                                                      "document_version": getattr(parent, "document_version", None),
                                                                      "sheet_name": row.sheet_name, "row_number": row.row_number,
                                                                      "target_attribute": row.target_attribute}))

        for row in db.query(ScenarioBaseline).all():
            scenario = db.query(Scenario).filter(Scenario.id == row.scenario_id).first()
            content = self._json({"baseline": row.baseline_name, "release_version": row.release_version,
                                  "scenario": getattr(scenario, "scenario_code", None),
                                  "endpoint": row.endpoint, "expected_response": row.expected_response_json,
                                  "expected_db_effect": row.expected_db_effect, "involved_classes": row.involved_classes})
            docs.append(RetrievalDocument(self._doc_id("BASELINE", row.id), "BASELINE", row.baseline_name or row.scenario_code,
                                          content, getattr(scenario, "project_path", None),
                                          {"baseline_id": row.id, "scenario_id": row.scenario_id,
                                           "release_version": row.release_version}))

        for row in db.query(ScenarioTestBaseline).all():
            scenario = db.query(Scenario).filter(Scenario.id == row.scenario_id).first()
            content = self._json({"test_baseline": row.baseline_name, "status": row.status,
                                  "scenario": getattr(scenario, "scenario_code", None), "jira_ids": row.jira_ids,
                                  "endpoint": row.endpoint, "request": row.request_json,
                                  "expected": row.expected_response_json})
            docs.append(RetrievalDocument(self._doc_id("TEST_BASELINE", row.id), "TEST_BASELINE", row.baseline_name,
                                          content, getattr(scenario, "project_path", None),
                                          {"test_baseline_id": row.id, "scenario_id": row.scenario_id,
                                           "status": row.status, "jira_ids": row.jira_ids or []}))

        docs.extend(self._collect_code_documents())
        return docs

    def _collect_code_documents(self) -> list[RetrievalDocument]:
        root_value = str(getattr(settings, "JAVA_PROJECT_PATH", "") or "").strip()
        if not root_value:
            return []
        root = Path(root_value).expanduser()
        if not root.exists():
            return []
        docs: list[RetrievalDocument] = []
        for file in sorted(root.rglob("*.java")):
            try:
                content = file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            relative = str(file.relative_to(root))
            # File-sized chunks keep Phase 8 independent from the old FAISS parser;
            # authoritative method/line details still come from Phase 7 AST/source tools.
            for index, start in enumerate(range(0, len(content), 5000), start=1):
                chunk = content[start:start + 5500]
                if not chunk.strip():
                    continue
                docs.append(RetrievalDocument(
                    self._doc_id("CODE", f"{relative}:{index}"), "CODE", f"{relative} chunk {index}", chunk,
                    str(root.resolve()), {"relative_path": relative, "chunk": index, "language": "java"}
                ))
        return docs

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        return "[" + ",".join(f"{float(v):.9f}" for v in values) + "]"

    def rebuild(self, db: Session) -> dict[str, Any]:
        self.ensure_schema()
        documents = self._collect_documents(db)
        texts = [f"{d.title}\n{d.content}" for d in documents]
        vectors = self.embeddings.embed_documents(texts) if texts else []
        engine = self._engine()
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.TABLE}"))
            statement = text(f"""
                INSERT INTO {self.TABLE}
                    (document_id, source_type, title, content, project_path, metadata_json, content_hash, embedding, updated_at)
                VALUES
                    (:document_id, :source_type, :title, :content, :project_path,
                     CAST(:metadata_json AS jsonb), :content_hash, CAST(:embedding AS vector), NOW())
            """)
            for doc, vector in zip(documents, vectors):
                conn.execute(statement, {
                    "document_id": doc.document_id, "source_type": doc.source_type, "title": doc.title,
                    "content": doc.content, "project_path": doc.project_path,
                    "metadata_json": self._json(doc.metadata),
                    "content_hash": hashlib.sha256(doc.content.encode("utf-8", errors="ignore")).hexdigest(),
                    "embedding": self._vector_literal(vector),
                })
        counts: dict[str, int] = {}
        for doc in documents:
            counts[doc.source_type] = counts.get(doc.source_type, 0) + 1
        return {"status": "INDEXED", "phase": 8, "backend": "POSTGRESQL_PGVECTOR",
                "documents": len(documents), "source_types": counts, "faiss_used": False,
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2"}

    def _rows(self, source_types: list[str] | None = None, project_path: str | None = None) -> list[dict[str, Any]]:
        self.ensure_schema()
        clauses, params = [], {}
        if source_types:
            clauses.append("source_type = ANY(:source_types)")
            params["source_types"] = [str(x).upper() for x in source_types]
        if project_path:
            clauses.append("project_path = :project_path")
            params["project_path"] = project_path
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        engine = self._engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT document_id, source_type, title, content, project_path, metadata_json FROM {self.TABLE}{where}"
            ), params).mappings().all()
        return [dict(r) for r in rows]

    def search(self, query: str, top_k: int = 10, source_types: list[str] | None = None,
               project_path: str | None = None) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        rows = self._rows(source_types, project_path)
        if not rows:
            return {"query": query, "phase": 8, "results": [], "message": "Phase 8 index is empty. Run rebuild first."}

        qvec = self._vector_literal(self.embeddings.embed_query(query))
        clauses, params = [], {"q": qvec, "limit": max(20, min(200, int(top_k) * 8))}
        if source_types:
            clauses.append("source_type = ANY(:source_types)")
            params["source_types"] = [str(x).upper() for x in source_types]
        if project_path:
            clauses.append("project_path = :project_path")
            params["project_path"] = project_path
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._engine().connect() as conn:
            dense = conn.execute(text(f"""
                SELECT document_id, 1 - (embedding <=> CAST(:q AS vector)) AS similarity
                FROM {self.TABLE}{where}
                ORDER BY embedding <=> CAST(:q AS vector)
                LIMIT :limit
            """), params).mappings().all()
        dense_rank = {r["document_id"]: i + 1 for i, r in enumerate(dense)}
        dense_score = {r["document_id"]: max(0.0, float(r["similarity"] or 0.0)) for r in dense}

        corpus = [self._tokens(f"{r['title']} {r['content']}") for r in rows]
        bm25 = BM25Okapi(corpus)
        lexical_values = bm25.get_scores(self._tokens(query))
        lexical_order = sorted(range(len(rows)), key=lambda i: lexical_values[i], reverse=True)
        bm25_rank = {rows[idx]["document_id"]: rank + 1 for rank, idx in enumerate(lexical_order)}
        max_bm25 = max(lexical_values) if len(lexical_values) else 0.0

        query_lower = query.lower()
        ranked = []
        for idx, row in enumerate(rows):
            did = row["document_id"]
            dr = dense_rank.get(did, 10000)
            br = bm25_rank.get(did, 10000)
            rrf = (1.0 / (60 + dr)) + (1.0 / (60 + br))
            exact = 0.0
            haystack = f"{row['title']} {row['content']}".lower()
            if query_lower in haystack:
                exact += 0.03
            for token in set(self._tokens(query)):
                if len(token) > 2 and token in haystack:
                    exact += 0.003
            score = rrf + min(exact, 0.06)
            ranked.append((score, row, dense_score.get(did, 0.0),
                           (float(lexical_values[idx]) / max_bm25) if max_bm25 > 0 else 0.0))
        ranked.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, row, semantic, lexical in ranked[:max(1, min(int(top_k), 50))]:
            meta = row["metadata_json"] if isinstance(row["metadata_json"], dict) else json.loads(row["metadata_json"] or "{}")
            results.append({"document_id": row["document_id"], "source_type": row["source_type"],
                            "title": row["title"], "score": round(score, 6),
                            "semantic_score": round(semantic, 4), "bm25_score": round(lexical, 4),
                            "project_path": row["project_path"], "metadata": meta,
                            "snippet": row["content"][:900]})
        return {"query": query, "phase": 8, "retrieval": {
                    "strategy": "HYBRID_PGVECTOR_BM25_METADATA_RERANK",
                    "vector_store": "PGVECTOR", "lexical": "BM25", "metadata_filtering": True,
                    "reranking": "RRF_PLUS_EXACT_IDENTIFIER_BOOST", "faiss_used": False,
                    "source_of_truth": False},
                "candidate_documents": len(rows), "total_matches": len(results), "results": results}

    def status(self) -> dict[str, Any]:
        schema = self.ensure_schema()
        with self._engine().connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {self.TABLE}")).scalar_one()
            version = conn.exec_driver_sql("SELECT extversion FROM pg_extension WHERE extname='vector'").scalar_one_or_none()
        return {**schema, "phase": 8, "documents": int(count), "pgvector_version": version,
                "faiss_used": False, "retrieval": "PGVECTOR_BM25_METADATA_RERANK"}

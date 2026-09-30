from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.orm import Session

from config import settings
from db_models import KnowledgeDocument


class KnowledgeIngestionService:
    """Ingest engineering documents into DB + semantic RAG + Neo4j relationships."""

    ALLOWED_TYPES = {"ARCHITECTURE", "API", "RELEASE", "TEST_REPORT", "JIRA"}

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=900, chunk_overlap=120
        )
        self.index_root = Path("knowledge_rag_indexes")
        self._vectors: dict[str, FAISS] = {}

    def _project_path(self) -> str:
        value = (
            getattr(settings, "PYTHON_PROJECT_PATH", None)
            or getattr(settings, "JAVA_PROJECT_PATH", None)
            or "ACTIVE_PROJECT"
        )
        try:
            return str(Path(str(value)).expanduser().resolve())
        except Exception:
            return str(value)

    def ingest(
        self,
        db: Session,
        *,
        source_type: str,
        title: str,
        content: str,
        source_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        source_type = str(source_type or "").strip().upper()
        title = str(title or "").strip()
        content = str(content or "").strip()
        if source_type not in self.ALLOWED_TYPES:
            raise ValueError("source_type must be one of: " + ", ".join(sorted(self.ALLOWED_TYPES)))
        if not title:
            raise ValueError("title is required")
        if not content:
            raise ValueError("content is required")

        project_path = self._project_path()
        extracted = self._extract_metadata(content)
        combined_metadata = dict(metadata or {})
        combined_metadata["extracted"] = extracted

        row = KnowledgeDocument(
            source_type=source_type,
            title=title,
            content=content,
            project_path=project_path,
            source_ref=(source_ref or None),
            metadata_json=json.dumps(combined_metadata, ensure_ascii=False),
            status="ACTIVE",
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        rag = self.rebuild_rag(db)
        graph = self._sync_document_to_neo4j(row, extracted)
        return {
            "status": "INGESTED",
            "document": self._row(row),
            "extracted": extracted,
            "rag": rag,
            "neo4j": graph,
        }

    def list_documents(self, db: Session) -> dict:
        project_path = self._project_path()
        rows = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_path == project_path)
            .filter(KnowledgeDocument.status == "ACTIVE")
            .order_by(KnowledgeDocument.id.desc())
            .all()
        )
        return {"project_path": project_path, "documents": [self._row(x) for x in rows]}

    def rebuild_rag(self, db: Session) -> dict:
        project_path = self._project_path()
        rows = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.project_path == project_path)
            .filter(KnowledgeDocument.status == "ACTIVE")
            .all()
        )
        docs: list[Document] = []
        for row in rows:
            chunks = self.splitter.split_text(row.content)
            total_chunks = len(chunks)
            for i, chunk in enumerate(chunks):
                chunk_id = f"knowledge:{row.id}:chunk:{i}"
                docs.append(Document(
                    page_content=f"{row.title}\n{chunk}",
                    metadata={
                        "knowledge_document_id": row.id,
                        "chunk_id": chunk_id,
                        "chunk_index": i,
                        "total_chunks": total_chunks,
                        # Keep the old key for compatibility with any existing callers.
                        "chunk": i,
                        "source_type": row.source_type,
                        "title": row.title,
                        "project_path": row.project_path,
                        "source_ref": row.source_ref or "",
                    },
                ))

        key = self._project_key(project_path)
        folder = self.index_root / key
        if not docs:
            self._vectors.pop(project_path, None)
            return {"status": "EMPTY", "documents": 0, "chunks": 0}

        vector = FAISS.from_documents(docs, self.embeddings)
        self._vectors[project_path] = vector
        folder.mkdir(parents=True, exist_ok=True)
        vector.save_local(str(folder))
        return {"status": "INDEXED", "documents": len(rows), "chunks": len(docs)}

    def search(self, db: Session, query: str, top_k: int = 5) -> dict:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")
        project_path = self._project_path()
        vector = self._vectors.get(project_path)
        if vector is None:
            vector = self._load_vector(project_path)
        if vector is None:
            self.rebuild_rag(db)
            vector = self._vectors.get(project_path)
        if vector is None:
            return {"query": query, "results": [], "total_matches": 0}

        pairs = vector.similarity_search_with_score(query, k=max(1, min(int(top_k), 10)))
        results = []
        for doc, distance in pairs:
            results.append({
                "knowledge_document_id": doc.metadata.get("knowledge_document_id"),
                "chunk_id": doc.metadata.get("chunk_id"),
                "chunk_index": doc.metadata.get("chunk_index", doc.metadata.get("chunk")),
                "total_chunks": doc.metadata.get("total_chunks"),
                "title": doc.metadata.get("title"),
                "source_type": doc.metadata.get("source_type"),
                "source_ref": doc.metadata.get("source_ref"),
                "project_path": doc.metadata.get("project_path"),
                "text": doc.page_content,
                "distance": float(distance),
            })
        return {"query": query, "results": results, "total_matches": len(results)}

    def _load_vector(self, project_path: str):
        folder = self.index_root / self._project_key(project_path)
        if not (folder / "index.faiss").exists():
            return None
        try:
            vector = FAISS.load_local(
                str(folder), self.embeddings, allow_dangerous_deserialization=True
            )
            self._vectors[project_path] = vector
            return vector
        except Exception:
            return None

    @staticmethod
    def _extract_metadata(content: str) -> dict:
        jira_ids = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", content)))
        endpoints = sorted(set(re.findall(r"(?<!\w)/(?:api/)?[A-Za-z0-9_{}./:-]+", content)))
        http_methods = sorted(set(x.upper() for x in re.findall(
            r"\b(GET|POST|PUT|PATCH|DELETE)\b", content, re.I
        )))
        return {"jira_ids": jira_ids, "endpoints": endpoints[:50], "http_methods": http_methods}

    def _sync_document_to_neo4j(self, row: KnowledgeDocument, extracted: dict) -> dict:
        if not getattr(settings, "NEO4J_ENABLED", False):
            return {"enabled": False, "status": "DISABLED"}
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
            )
            doc_id = f"knowledge:{row.id}"
            project_name = Path(row.project_path).name or row.project_path
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                session.run(
                    "MERGE (d:CodeIntelligence:KnowledgeDocument {id:$id}) "
                    "SET d.type='KNOWLEDGE_DOCUMENT', d.name=$title, d.project=$project, "
                    "d.source_type=$source_type, d.source_ref=$source_ref",
                    id=doc_id, title=row.title, project=project_name,
                    source_type=row.source_type, source_ref=row.source_ref or "",
                )
                session.run(
                    "MERGE (p:CodeIntelligence:KnowledgeEntity {id:$id}) "
                    "SET p.type='PROJECT', p.name=$name, p.project=$name "
                    "WITH p MATCH (d:CodeIntelligence:KnowledgeDocument {id:$doc}) "
                    "MERGE (d)-[:BELONGS_TO]->(p)",
                    id=f"knowledge-project:{project_name}", name=project_name, doc=doc_id,
                )
                for jira in extracted.get("jira_ids", []):
                    session.run(
                        "MERGE (j:CodeIntelligence:KnowledgeEntity {id:$id}) "
                        "SET j.type='JIRA', j.name=$name, j.project=$project "
                        "WITH j MATCH (d:CodeIntelligence:KnowledgeDocument {id:$doc}) "
                        "MERGE (d)-[:MENTIONS]->(j)",
                        id=f"jira:{jira}", name=jira, project=project_name, doc=doc_id,
                    )
                for endpoint in extracted.get("endpoints", []):
                    session.run(
                        "MERGE (e:CodeIntelligence:KnowledgeEntity {id:$id}) "
                        "SET e.type='ENDPOINT', e.name=$name, e.project=$project "
                        "WITH e MATCH (d:CodeIntelligence:KnowledgeDocument {id:$doc}) "
                        "MERGE (d)-[:MENTIONS]->(e)",
                        id=f"endpoint:{project_name}:{endpoint}", name=endpoint,
                        project=project_name, doc=doc_id,
                    )
            driver.close()
            return {"enabled": True, "status": "SYNCED", "document_node": doc_id}
        except Exception as exc:
            return {"enabled": True, "status": "UNAVAILABLE", "error": str(exc)}

    @staticmethod
    def _row(row: KnowledgeDocument) -> dict:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except Exception:
            metadata = {}
        return {
            "id": row.id, "source_type": row.source_type, "title": row.title,
            "project_path": row.project_path, "source_ref": row.source_ref,
            "metadata": metadata, "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    @staticmethod
    def _project_key(project_path: str) -> str:
        name = Path(project_path).name or "project"
        digest = hashlib.sha1(project_path.encode()).hexdigest()[:10]
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", name)
        return f"{safe}-{digest}"

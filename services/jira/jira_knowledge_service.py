from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy.orm import Session

from db_models import JiraKnowledge
from services.scanner.java_scanner_service import JavaScannerService


class JiraKnowledgeService:
    """Local JIRA knowledge base: DB is source of truth, FAISS is rebuilt from DB."""

    def __init__(self):
        self.index_path = Path("jira_rag_index")
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.vector_store: FAISS | None = None
        self._load_if_present()

    def save_and_index(
        self,
        db: Session,
        jira_id: str,
        requirement: str,
        title: str | None = None,
    ) -> dict:
        jira_id = (jira_id or "").strip().upper()
        requirement = (requirement or "").strip()
        title = (title or "").strip() or None

        if not jira_id:
            raise ValueError("JIRA ID is required")
        if len(requirement) < 3:
            raise ValueError("Requirement is required")

        try:
            project_path = JavaScannerService().scan().project_path
        except Exception:
            project_path = None

        row = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira_id).first()
        if row:
            row.title = title
            row.requirement = requirement
            row.project_path = project_path
            action = "updated"
        else:
            row = JiraKnowledge(
                jira_id=jira_id,
                title=title,
                requirement=requirement,
                project_path=project_path,
            )
            db.add(row)
            action = "created"

        db.commit()
        db.refresh(row)
        index_info = self.rebuild_index(db)

        return {
            "status": "SAVED_AND_INDEXED",
            "action": action,
            "jira": self._to_dict(row),
            "index": index_info,
        }

    def list_all(self, db: Session) -> list[dict]:
        rows = db.query(JiraKnowledge).order_by(JiraKnowledge.updated_at.desc()).all()
        return [self._to_dict(row) for row in rows]

    def rebuild_index(self, db: Session) -> dict:
        rows = db.query(JiraKnowledge).order_by(JiraKnowledge.id).all()
        if not rows:
            self.vector_store = None
            return {"documents": 0, "status": "EMPTY"}

        documents = []
        for row in rows:
            text = "\n".join(filter(None, [
                f"JIRA: {row.jira_id}",
                f"Title: {row.title}" if row.title else None,
                f"Requirement: {row.requirement}",
                f"Project: {row.project_path}" if row.project_path else None,
            ]))
            documents.append(Document(
                page_content=text,
                metadata={
                    "jira_id": row.jira_id,
                    "title": row.title or "",
                    "project_path": row.project_path or "",
                }
            ))

        self.vector_store = FAISS.from_documents(documents, self.embeddings)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.vector_store.save_local(str(self.index_path))
        return {"documents": len(documents), "status": "INDEXED"}

    def search(self, db: Session, query: str, top_k: int = 5) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if self.vector_store is None:
            self.rebuild_index(db)
        if self.vector_store is None:
            return []

        matches = self.vector_store.similarity_search_with_score(query, k=top_k)
        results = []
        for doc, score in matches:
            jira_id = doc.metadata.get("jira_id")
            row = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira_id).first()
            if not row:
                continue
            results.append({
                **self._to_dict(row),
                "similarity_score": float(score),
                "matched_by": "JIRA_RAG",
            })
        return results

    def _load_if_present(self):
        if not (self.index_path / "index.faiss").exists():
            return
        try:
            self.vector_store = FAISS.load_local(
                str(self.index_path),
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
        except Exception:
            self.vector_store = None

    @staticmethod
    def _to_dict(row: JiraKnowledge) -> dict:
        return {
            "id": row.id,
            "jira_id": row.jira_id,
            "title": row.title,
            "requirement": row.requirement,
            "project_path": row.project_path,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


jira_knowledge_service = JiraKnowledgeService()

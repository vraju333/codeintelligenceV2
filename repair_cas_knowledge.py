"""One-time, project-scoped knowledge metadata and FAISS/Neo4j document repair.

Run from the CodeIntelligence backend directory, after backing up the database:
    python repair_cas_knowledge.py --project customer-account-service

Uses the configured database and the existing embedding/Neo4j settings.
Does not invent Jira-to-code relationships or modify unrelated projects.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from database import SessionLocal
from db_models import KnowledgeDocument
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService


def repair(project: str) -> dict:
    service = KnowledgeIngestionService()
    with SessionLocal() as db:
        paths = [x[0] for x in db.query(KnowledgeDocument.project_path).distinct().all() if x[0]]
        matches = [p for p in paths if Path(p).name.casefold() == project.casefold() or p.casefold() == project.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one matching project; found {len(matches)}. Known project names: {[Path(p).name for p in paths]}")
        selected = matches[0]
        service._project_path = lambda: selected
        before = db.query(KnowledgeDocument).filter(KnowledgeDocument.project_path == selected, KnowledgeDocument.status == 'ACTIVE').count()
        result = service.rebuild_rag(db)
        incorrect = []
        for row in db.query(KnowledgeDocument).filter(KnowledgeDocument.project_path == selected).all():
            if row.source_type.upper() != 'MAPPING':
                continue
            entities = json.loads(row.metadata_json or '{}').get('extracted') or {}
            if any(str(p).startswith('/CustomerAccount/') for p in entities.get('endpoints', [])):
                incorrect.append(row.id)
        if incorrect:
            raise RuntimeError(f"Mapping XPath endpoint metadata remains in documents: {incorrect}")
        return {'project': selected, 'active_documents': before, 'rag': result, 'incorrect_mapping_documents': incorrect,
                'note': 'Engineering Jira-to-code graph links require independent source evidence and are not fabricated by this repair.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='customer-account-service')
    args = parser.parse_args()
    print(json.dumps(repair(args.project), indent=2))

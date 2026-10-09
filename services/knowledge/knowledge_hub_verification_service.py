"""Read-only verification of project-scoped Knowledge Hub persistence."""
from sqlalchemy import text
from db_models import JiraKnowledge, KnowledgeDocument, MappingDocument, MappingDefinition, KnowledgeSyncSource, KnowledgeSyncRun
from services.knowledge.knowledge_hub_onboarding_service import normalized
from config import settings


def verify_source(db, source_id: int) -> dict:
    row = db.query(KnowledgeSyncSource).filter_by(id=source_id).first()
    if row is None:
        raise ValueError("Unknown source")
    project = row.project_path or ""
    run = db.query(KnowledgeSyncRun).filter_by(source_id=source_id).order_by(KnowledgeSyncRun.id.desc()).first()
    result = {"source_id": source_id, "project_path": project, "source_type": row.source_type}
    result["postgresql"] = {"status": "VERIFIED" if run and run.status == "SUCCESS" else "NOT_VERIFIED", "latest_run": run.status if run else None}
    if row.source_type == "JIRA":
        import json
        config = json.loads(row.config_json or "{}")
        key = (config.get("project_key") or "").upper()
        rows = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id.like(key + "-%")).all() if key else []
        matches = [x for x in rows if normalized(x.project_path) == normalized(project)]
        result["records"] = {"status": "VERIFIED" if matches else "NOT_VERIFIED", "count": len(matches), "keys": [x.jira_id for x in matches[:30]]}
    elif row.source_type == "MAPPING_FOLDER":
        docs = [x for x in db.query(MappingDocument).all() if normalized(x.project_path) == normalized(project) and x.status == "ACTIVE"]
        result["records"] = {"status": "VERIFIED" if docs else "NOT_VERIFIED", "documents": len(docs), "rows": db.query(MappingDefinition).filter(MappingDefinition.document_id.in_([d.id for d in docs])).count() if docs else 0}
    else:
        docs = [x for x in db.query(KnowledgeDocument).all() if normalized(x.project_path) == normalized(project) and x.status == "ACTIVE"]
        result["records"] = {"status": "VERIFIED" if docs else "NOT_VERIFIED", "documents": len(docs)}
    try:
        from sqlalchemy import create_engine
        from services.retrieval.enterprise_hybrid_rag_service import EnterpriseHybridRagService
        engine = create_engine(str(settings.POSTGRES_DATABASE_URL), pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT source_type, count(*) FROM engineering_knowledge_vectors WHERE lower(replace(project_path, chr(92), '/')) = :project GROUP BY source_type"), {"project": normalized(project)}).all()
        counts = {kind: int(count) for kind, count in rows}
        expected = {"JIRA": "JIRA", "MAPPING_FOLDER": "MAPPING", "DOCUMENT_FOLDER": None}.get(row.source_type)
        total = sum(counts.values()) if expected is None else counts.get(expected, 0)
        result["pgvector"] = {"status": "VERIFIED" if total else "NOT_VERIFIED", "matching_documents": total, "project_counts": counts}
        if row.source_type == "JIRA" and result["records"].get("keys"):
            import hashlib
            issue_rows = [j for j in db.query(JiraKnowledge).all()
                          if j.jira_id in result["records"]["keys"]]
            expected_ids = {hashlib.sha256(f"JIRA:{j.id}".encode()).hexdigest()[:40]: j.jira_id
                            for j in issue_rows}
            with engine.connect() as conn:
                indexed = conn.execute(text("""
                    SELECT document_id, project_path, metadata_json ->> 'jira_id' AS jira_id
                    FROM engineering_knowledge_vectors
                    WHERE source_type = 'JIRA' AND document_id = ANY(:ids)
                """), {"ids": list(expected_ids)}).all()
            found = sorted({expected_ids[r[0]] for r in indexed
                            if r[0] in expected_ids and normalized(r[1]) == normalized(project)})
            mismatches = [{"jira_id": expected_ids[r[0]], "indexed_project_path": r[1]}
                          for r in indexed if r[0] in expected_ids and normalized(r[1]) != normalized(project)]
            result["pgvector"]["indexed_jira_keys"] = found
            result["pgvector"]["project_mismatches"] = mismatches
            result["pgvector"]["status"] = "VERIFIED" if len(found) == len(expected_ids) else "NOT_VERIFIED"
    except Exception as exc:
        result["pgvector"] = {"status": "UNAVAILABLE", "error": str(exc)}
    finally:
        if "engine" in locals():
            engine.dispose()
    try:
        from services.cross_project.engineering_knowledge_graph_service import EngineeringKnowledgeGraphService
        from pathlib import Path
        graph = EngineeringKnowledgeGraphService()
        driver = graph._driver()
        with driver.session(database=settings.NEO4J_DATABASE) as session:
            project_name = Path(project.replace("\\", "/")).name
            count = session.run("MATCH (p:EngineeringKnowledge:Project {name:$name}) RETURN count(p) AS n", name=project_name).single()["n"]
            if row.source_type == "JIRA":
                keys = result["records"].get("keys", [])
                linked = session.run("MATCH (j:EngineeringKnowledge:Jira) WHERE j.name IN $keys RETURN count(j) AS n", keys=keys).single()["n"]
                relations = session.run("MATCH (j:EngineeringKnowledge:Jira)-[r]-(n:EngineeringKnowledge) WHERE j.name IN $keys RETURN count(r) AS n", keys=keys).single()["n"]
                ownership = session.run("""
                    MATCH (j:EngineeringKnowledge:Jira)-[:BELONGS_TO_PROJECT]->
                          (p:EngineeringKnowledge:Project {name:$project_name})
                    WHERE j.name IN $keys
                    RETURN count(DISTINCT j) AS n
                """, keys=keys, project_name=project_name).single()["n"]
                implementation = session.run("""
                    MATCH (j:EngineeringKnowledge:Jira)-[:IMPLEMENTS|IMPLEMENTED_BY]-(m:EngineeringKnowledge:Method)
                    WHERE j.name IN $keys RETURN count(DISTINCT m) AS n
                """, keys=keys).single()["n"]
                code_references = session.run("""
                    MATCH (j:EngineeringKnowledge:Jira)<-[:REFERENCES_JIRA]-(m:EngineeringKnowledge:Method)
                    WHERE j.name IN $keys RETURN count(DISTINCT m) AS n
                """, keys=keys).single()["n"]
                inferred = session.run("""
                    MATCH (s:EngineeringKnowledge:ScenarioEvidence {project:$project})
                          -[:POSSIBLY_COVERS_JIRA]->(j:EngineeringKnowledge:Jira)
                    WHERE j.name IN $keys RETURN count(DISTINCT s) AS n
                """, project=project_name, keys=keys).single()["n"]
                explicit_scenarios = session.run("""
                    MATCH (s:EngineeringKnowledge:ScenarioEvidence {project:$project})
                          -[:COVERS_JIRA]->(j:EngineeringKnowledge:Jira)
                    WHERE j.name IN $keys RETURN count(DISTINCT s) AS n
                """, project=project_name, keys=keys).single()["n"]
            else:
                linked, relations, ownership, implementation, code_references = None, None, None, None, None
                inferred, explicit_scenarios = None, None
            mapping_links = session.run("""
                MATCH (m:EngineeringKnowledge:MappingEvidence {project:$project})
                      -[:MAPS_TO_ATTRIBUTE]->(a:EngineeringKnowledge:Attribute {project:$project})
                RETURN count(DISTINCT m) AS n
            """, project=project_name).single()["n"]
        driver.close()
        graph_ok = bool(count) and (linked is None or (linked > 0 and ownership > 0))
        result["neo4j"] = {"status": "VERIFIED" if graph_ok else "NOT_VERIFIED",
                           "project_nodes": count, "jira_nodes": linked,
                           "jira_relationships": relations,
                           "jira_project_links": ownership,
                           "jira_method_links": implementation,
                           "explicit_code_references": code_references,
                           "mapping_attribute_links": mapping_links,
                           "inferred_jira_scenario_links": inferred,
                           "explicit_jira_scenario_links": explicit_scenarios,
                           "implementation_status": "NOT_LINKED" if implementation == 0 else "LINKED" if implementation is not None else "NOT_APPLICABLE"}
    except Exception as exc:
        result["neo4j"] = {"status": "UNAVAILABLE", "error": str(exc)}
    required = [result["postgresql"]["status"], result["records"]["status"], result["pgvector"]["status"], result["neo4j"]["status"]]
    result["status"] = "VERIFIED" if all(x == "VERIFIED" for x in required) else "PARTIAL"
    return result

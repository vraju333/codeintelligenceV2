"""Opt-in Neo4j persistence of direct syntax evidence; no inferred execution paths."""
from __future__ import annotations
import hashlib
from pathlib import Path


def persist_field_evidence(result: dict, project_id: str, revision: str) -> dict:
    from neo4j import GraphDatabase
    from config import settings
    if not settings.NEO4J_ENABLED:
        raise RuntimeError('Neo4j is disabled; enable it explicitly before syncing')
    if not project_id.strip() or not revision.strip():
        raise ValueError('project_id and revision are required for graph persistence')
    records=[]
    for kind, key in [('DECLARATION','declarations'),('READ','readers'),('WRITE','writers')]:
        for entry in result.get(key,[]):
            source_file=entry.get('file','')
            signature='|'.join((project_id,revision,source_file,str(entry.get('line')),kind,entry.get('detail','')))
            records.append({'id':hashlib.sha256(signature.encode()).hexdigest(), 'file':source_file,
                            'line':entry.get('line',0),'method':entry.get('method',''),
                            'kind':kind,'detail':entry.get('detail',''),
                            'scope':entry.get('source_scope','UNKNOWN')})
    driver=GraphDatabase.driver(settings.NEO4J_URI,auth=(settings.NEO4J_USERNAME,settings.NEO4J_PASSWORD))
    try:
        with driver.session(database=settings.NEO4J_DATABASE) as session:
            # A revision is immutable; syncing it twice is idempotent.
            session.run('''MERGE (p:FieldImpactProject {id:$project})
                MERGE (v:FieldImpactRevision {project:$project, revision:$revision})
                MERGE (p)-[:HAS_FIELD_REVISION]->(v)
                MERGE (f:FieldImpactField {project:$project, revision:$revision, name:$field})
                MERGE (v)-[:HAS_FIELD]->(f)''',project=project_id,revision=revision,field=result['field']).consume()
            session.run('''MATCH (f:FieldImpactField {project:$project, revision:$revision, name:$field})
                UNWIND $records AS record
                MERGE (e:FieldImpactEvidence {id:record.id})
                SET e += record
                MERGE (f)-[:HAS_SYNTAX_EVIDENCE]->(e)''',project=project_id,revision=revision,field=result['field'],records=records).consume()
    finally:
        driver.close()
    return {'project_id':project_id,'revision':revision,'field':result['field'],
            'evidence_records':len(records),'status':'SYNCED'}

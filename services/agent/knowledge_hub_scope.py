"""Deterministic Knowledge Hub answers for explicit single-source requests."""
from __future__ import annotations
import re
from pathlib import Path


def norm(path):
    return str(path or "").strip().replace("\\", "/").rstrip("/").casefold()


def selected_project(project):
    return str(project or "").strip()


def _scope(row, project):
    return norm(getattr(row, "project_path", "")) == norm(project)


def answer(db, query, project):
    from db_models import KnowledgeSyncSource, JiraKnowledge, MappingDocument, MappingDefinition
    from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService
    q = query.lower()
    project = selected_project(project)
    if not project:
        return None
    # Connector status is never answered from document metadata or ingestion timestamps.
    if re.search(r"\b(?:connector|connection|sync sources?|saved sources?|knowledge hub sources?)\b", q) and re.search(r"\b(?:list|show|status|persist|refresh|restart|saved|sync|configured|connected|verify)\b", q):
        rows = [r for r in db.query(KnowledgeSyncSource).all() if _scope(r, project)]
        items = [EnterpriseKnowledgeSyncService._source_dict(r) for r in rows]
        lines = [f"Saved Knowledge Hub connectors for {project}: {len(items)}"]
        for x in items:
            lines.append(f"- {x['name']} [{x['source_type']}] enabled={x['enabled']} last_status={x['last_status'] or 'NEVER_SYNCED'} last_sync_at={x['last_sync_at'] or 'NEVER_SYNCED'}")
        return dict(status="FOUND" if items else "NOT_FOUND", source="PERSISTED_CONNECTOR_STATUS", project_path=project, connectors=items, answer="\n".join(lines), execution_verified=False)
    jira = re.search(r"\b([A-Z][A-Z0-9_]+-\d+)\b", query, re.I)
    jira_key = jira.group(1).upper() if jira else None
    exclusions = bool(re.search(r"\b(?:do not include|exclude|without|only|no unrelated)\b", q))
    mentions_other = lambda pattern: bool(re.search(pattern, q))
    # Strict selected-project isolation: never borrow another project's indexed records.
    if jira_key and exclusions and mentions_other(r"selected|currently selected|other projects?|project.scoped"):
        row = next((r for r in db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira_key).all() if _scope(r, project)), None)
        if row is None:
            return dict(status="NOT_FOUND", source="PROJECT_SCOPED_KNOWLEDGE_HUB", project_path=project, jira=jira_key, answer=f"No project-scoped match for {jira_key} in {project}. Evidence from other workspaces was excluded.", jiras=[], mapping_rows=[], test_cases=[], execution_verified=False)
    jira_only = bool(jira_key and exclusions and mentions_other(r"requirement|acceptance criteria|jira") and not mentions_other(r"test cases? linked|imported test|mapping of|documented mapping"))
    if jira_only:
        rows = [r for r in db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira_key).all() if _scope(r, project)]
        if not rows:
            return dict(status="NOT_FOUND", source="PROJECT_SCOPED_JIRA", project_path=project, jira=jira_key, jiras=[], answer=f"No indexed {jira_key} requirement in selected project {project}.", execution_verified=False)
        r = rows[0]
        return dict(status="FOUND", source="PROJECT_SCOPED_JIRA", project_path=project, jira=jira_key, jiras=[dict(jira_id=r.jira_id,title=r.title,requirement=r.requirement,project_path=r.project_path)], answer=f"Indexed JIRA {r.jira_id}: {r.title or ''}\n{r.requirement}\nLive status and acceptance criteria are not separately verified unless present in indexed requirement text.", execution_verified=False)
    mapping_only = exclusions and mentions_other(r"\bmapping\b|xml.*java|source file.*sheet.*row") and not mentions_other(r"changing|impact of|blast radius")
    if mapping_only:
        docs = [d for d in db.query(MappingDocument).all() if _scope(d, project)]
        ids = {d.id for d in docs}
        wanted = 'countrycode' if 'countrycode' in q or 'country_code' in q else None
        definitions = [r for r in db.query(MappingDefinition).all() if r.document_id in ids and _scope(r, project) and (not wanted or wanted in str(r.target_attribute or '').replace('_','').lower() or wanted in str(r.source_path or '').replace('_','').lower())]
        by_id = {d.id:d for d in docs}
        seen = set(); items=[]
        for r in definitions:
            d=by_id[r.document_id]
            # Same content/version/row imported more than once is one logical mapping.
            identity=(str(d.checksum_sha256 or d.filename).casefold(),str(d.document_version or ''),r.sheet_name,r.row_number,str(r.source_path or '').casefold(),str(r.target_class or '').casefold(),str(r.target_attribute or '').casefold(),str(r.mapping_rule or '').strip().casefold())
            if identity in seen: continue
            seen.add(identity)
            items.append(dict(document_id=d.id,filename=d.filename,source_ref=d.source_ref,document_version=d.document_version,sheet=r.sheet_name,row=r.row_number,source_path=r.source_path,target_class=r.target_class,target_attribute=r.target_attribute,mapping_rule=r.mapping_rule,validation_rule=r.validation_rule,version_status="ACTIVE_VERSION_NOT_VERIFIED"))
        lines=[f"Project-scoped mapping evidence for {project}: {len(items)} unique rows"]
        lines += [f"- {r['filename']} version={r['document_version'] or 'UNKNOWN'} sheet={r['sheet']} row={r['row']}: {r['source_path']} -> {r['target_class']}.{r['target_attribute']} rule={r['mapping_rule'] or 'UNSPECIFIED'}" for r in items]
        # Distinguish actual mapping-to-mapping differences from requirement-vs-mapping differences.
        rules_by_source = {}
        for item in items:
            key = (str(item['source_path']).casefold(), str(item['target_class']).casefold(), str(item['target_attribute']).casefold())
            rules_by_source.setdefault(key, set()).add(str(item['mapping_rule'] or '').strip().casefold())
        conflicts = [dict(source_path=k[0], target_class=k[1], target_attribute=k[2], distinct_rules=sorted(v)) for k, v in rules_by_source.items() if len(v) > 1]
        lines.append(f"Mapping-to-mapping conflicts: {len(conflicts)}; identical rules across versions are not conflicts.")
        lines.append("Active mapping version is NOT VERIFIED by document recency alone.")
        return dict(status="FOUND" if items else "NOT_FOUND",source="PROJECT_SCOPED_MAPPING",project_path=project,mapping_rows=items,mapping_count=len(items),active_mapping_version_verified=False,mapping_conflicts=conflicts,answer="\n".join(lines),execution_verified=False)
    test_only = bool(jira_key and exclusions and mentions_other(r"test cases?|imported tests?|test suite"))
    if test_only:
        from services.knowledge.test_suite_service import TestSuiteService
        from services.agent.sdlc_report_service import unique_cases
        result=TestSuiteService().search(db,project_path=project,jira=jira_key,limit=500)
        cases=unique_cases(result.get('test_cases') or [])
        lines=[f"Imported test definitions for {jira_key} in {project}: {len(cases)} unique cases", "Imported definitions are not verified executions."]
        for c in cases:
            lines.append(f"- {c.get('test_id')}: input={c.get('input')} expected={c.get('expected')} execution=NOT_VERIFIED sources={c.get('sources')}")
        return dict(status="FOUND" if cases else "NOT_FOUND",source="PROJECT_SCOPED_TEST_CASES",project_path=project,jira=jira_key,test_cases=cases,execution_verified=False,answer="\n".join(lines))
    return None

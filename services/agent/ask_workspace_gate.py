"""Fail-closed workspace/JIRA gate and persisted Knowledge Hub source inventory.

These decisions precede LLM tool selection. No sync, remote Jira access or writes.
"""
from __future__ import annotations
import re

JIRA_KEY = re.compile(r'\b[A-Z][A-Z0-9_]+-\d+\b', re.I)
CONNECTOR_INTENT = re.compile(r'\b(?:knowledge\s+hub\s+)?(?:connections?|connectors?|connected\s+sources?|configured\s+sources?|saved\s+sources?|sync\s+sources?)\b', re.I)
CONNECTOR_ACTION = re.compile(r'\b(?:list|show|which|what|status|saved|configured|enabled|available|please|display|view|my|our|knowledge|hub|connections?|connectors?|connected|sources?|sync|synchroni[sz]ation|their|and|for|project|workspace|of|the|are)\b', re.I)

def canonical(value: str | None) -> str:
    return str(value or '').strip().replace('\\', '/').rstrip('/').casefold()

def is_connector_inventory(query: str) -> bool:
    if not CONNECTOR_INTENT.search(query or ''):
        return False
    # Never hijack a question about graph/code dependencies or a sync command.
    if re.search(r'\b(?:run|trigger|start|execute|refresh|import|connect\s+to|create|delete|remove|impact|call\s+graph|dependency|dependencies)\b', query, re.I):
        return False
    remaining = CONNECTOR_ACTION.sub(' ', query or '')
    return not re.search(r'[A-Za-z]{3,}', remaining)

def connector_inventory(db, query: str, project_path: str | None) -> dict:
    from db_models import KnowledgeSyncSource
    project = str(project_path or '').strip()
    base = {'query': query, 'source': 'PERSISTED_KNOWLEDGE_SYNC_SOURCES',
            'intent': 'KNOWLEDGE_HUB_CONNECTION_INVENTORY', 'project_path': project,
            'execution_verified': False, 'connections': []}
    if not project:
        return {**base, 'status': 'PROJECT_REQUIRED', 'answer': 'Select a workspace project before listing its saved Knowledge Hub connections.'}
    rows = db.query(KnowledgeSyncSource).order_by(KnowledgeSyncSource.id).all()
    connections = []
    for row in rows:
        if canonical(row.project_path) != canonical(project):
            continue
        connections.append({'id': row.id, 'name': row.name, 'source_type': row.source_type,
                            'enabled': str(row.enabled or '').lower() == 'true',
                            'last_status': row.last_status or 'NOT_SYNCED',
                            'last_sync_at': row.last_sync_at.isoformat() if row.last_sync_at else None,
                            'last_error': row.last_error,
                            'project_path': row.project_path})
    lines = [f'Saved Knowledge Hub connections for {project}: {len(connections)}']
    for item in connections:
        lines.append(f"- {item['name']} ({item['source_type']}): {'enabled' if item['enabled'] else 'disabled'}; last sync: {item['last_status']}")
    if not connections:
        lines.append('No saved Knowledge Hub connections belong to this workspace. Imported documents are not connector records.')
    return {**base, 'status': 'FOUND' if connections else 'NOT_FOUND',
            'connections': connections, 'answer': '\n'.join(lines),
            'note': 'Read-only persisted connector inventory; no sync or live source checks performed.'}

def foreign_jira_guard(db, query: str, project_path: str | None) -> dict | None:
    """Prevent an indexed JIRA from another workspace leaking via generic RAG.

    Only blocks when a JIRA key is explicitly requested and that key is not
    owned by the selected workspace. Does not claim remote Jira nonexistence.
    """
    keys = list(dict.fromkeys(key.upper() for key in JIRA_KEY.findall(query or '')))
    if not keys:
        return None
    project = str(project_path or '').strip()
    base = {'query': query, 'source': 'WORKSPACE_JIRA_OWNERSHIP_GATE',
            'intent': 'WORKSPACE_SCOPED_JIRA_LOOKUP', 'project_path': project,
            'jira_ids': keys, 'execution_verified': False}
    if not project:
        return {**base, 'status': 'PROJECT_REQUIRED',
                'answer': 'Select a workspace project before querying JIRA evidence.'}
    from db_models import JiraKnowledge
    rows = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id.in_(keys)).all()
    owned = {str(row.jira_id).upper() for row in rows if canonical(row.project_path) == canonical(project)}
    missing = [key for key in keys if key not in owned]
    if not missing:
        return None
    return {**base, 'status': 'NOT_FOUND', 'missing_jira_ids': missing,
            'answer': f"No indexed JIRA requirement for {', '.join(missing)} belongs to the selected workspace ({project}). No evidence from other workspaces was searched or returned. Live JIRA was not checked.",
            'note': 'Fail-closed indexed ownership check; this does not establish whether a remote JIRA issue exists.'}

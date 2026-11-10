"""Strict selected-workspace JIRA requirement lookup (not impact analysis)."""
from __future__ import annotations
import json
import re

KEY = re.compile(r'\b([A-Z][A-Z0-9_]+-\d+)\b', re.I)
EXCLUDED = re.compile(r'\b(?:do not|don\'t|without|exclude|excluding|not include)\b[^.!?]*', re.I)


def jira_requirement_only_request(query: str) -> bool:
    q = query or ''
    if not KEY.search(q):
        return False
    positive = EXCLUDED.sub('', q)
    # Must explicitly request the requirement or its acceptance criteria.
    if not re.search(r'\b(?:requirements?|acceptance criteria|jira (?:details?|status)|issue (?:details?|status))\b', positive, re.I):
        return False
    # Multi-source questions retain the existing impact analysis route.
    if re.search(r'\b(?:impact|impacted|affect|affected|traceability|cross.source|compare|comparison|git|commit|code|java|methods?|apis?|endpoints?|mappings?|workbooks?|test cases?|test suites?|regression|baselines?)\b', positive, re.I):
        return False
    return True


def jira_requirement_only_answer(db, query: str, project_path: str | None) -> dict:
    from db_models import JiraKnowledge
    from services.lineage.mapping_intelligence_service import MappingIntelligenceService
    match = KEY.search(query or '')
    jira = match.group(1).upper() if match else ''
    project = str(project_path or '').strip()
    base = {'query': query, 'intent': 'JIRA_REQUIREMENT_LOOKUP',
            'source': 'INDEXED_JIRA_REQUIREMENT', 'jira_id': jira,
            'project_path': project, 'execution_verified': False}
    if not project:
        return {**base, 'status': 'PROJECT_REQUIRED', 'answer': 'Select an indexed workspace project before retrieving a JIRA requirement.'}
    canonical = MappingIntelligenceService._canonical_project_key
    record = next((r for r in db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == jira).all()
                   if canonical(r.project_path or '') == canonical(project)), None)
    if record is None:
        return {**base, 'status': 'NOT_FOUND', 'answer': f'No indexed requirement for {jira} belongs to the selected project. Live JIRA was not checked.'}
    requirement = str(record.requirement or '')
    metadata = {}
    metadata_match = re.search(r'Jira metadata:\s*(\{[^\n]*\})', requirement)
    if metadata_match:
        try:
            metadata = json.loads(metadata_match.group(1))
        except (ValueError, TypeError):
            pass
    requirement_text = re.sub(r'\n*Jira metadata:\s*\{[^\n]*\}\s*$', '', requirement).strip()
    criteria = re.search(r'Acceptance Criteria\s*:\s*(.*?)(?=\n\s*(?:Impacted Business Attribute|Expected Test Scenarios)\s*:|\Z)', requirement_text, re.I | re.S)
    attribute_match = re.search(r'Impacted Business Attribute\s*:\s*([^\n]+)', requirement_text, re.I)
    attributes = [attribute_match.group(1).strip()] if attribute_match else []
    return {**base, 'status': 'FOUND', 'jira': {'jira_id': jira, 'summary': record.title,
            'requirement': requirement_text, 'status': metadata.get('status', 'NOT_VERIFIED'),
            'issue_type': metadata.get('issue_type', 'NOT_VERIFIED')},
            'acceptance_criteria': criteria.group(1).strip() if criteria else None,
            'related_attributes': attributes,
            'source_evidence': {'type': 'INDEXED_JIRA_REQUIREMENT', 'jira_id': jira,
                                'project_path': record.project_path, 'record_id': record.id,
                                'updated_at': record.updated_at.isoformat() if record.updated_at else None},
            'answer': '\n'.join([f'JIRA: {jira} — {record.title or ""}',
                                 f'Status: {metadata.get("status", "NOT_VERIFIED")}',
                                 f'Issue type: {metadata.get("issue_type", "NOT_VERIFIED")}',
                                 f'Related attributes: {", ".join(attributes) or "NOT_SPECIFIED"}',
                                 'Requirement and acceptance criteria:', requirement_text,
                                 f'Source: indexed JIRA {jira} for {project}']),
            'note': 'Indexed JIRA evidence only; live status not independently verified. No code, mapping, Git, or QA impact was assessed.'}

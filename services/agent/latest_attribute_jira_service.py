"""ASK latest Jira requirement lookup; never confuse Jira recency with Git recency."""
from __future__ import annotations
import re
from datetime import datetime
from config import settings
from services.jira.attribute_jira_lookup_service import lookup
from services.agent.ask_evidence_service import detect_attribute

LATEST_JIRA = re.compile(r'\b(?:latest|newest|most recent|last)\b', re.I)
JIRA_NOUN = re.compile(r'\b(?:jiras?|issues?|requirements?|tickets?)\b', re.I)


def is_latest_jira_question(question: str) -> bool:
    return bool(LATEST_JIRA.search(question) and JIRA_NOUN.search(question) and detect_attribute(question))


def _parse_created(value):
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def latest_attribute_jira(db, question: str, *, live_client=None) -> dict:
    attribute = detect_attribute(question)
    if not attribute:
        raise ValueError('No supported attribute found in latest Jira question')
    project = str(getattr(settings, 'JAVA_PROJECT_PATH', '') or '')
    candidates = lookup(db, attribute, project_path=project)
    matches = candidates.get('matches') or []
    if not matches:
        return {'status': 'NOT_FOUND', 'source': 'INDEXED_JIRA_REQUIREMENTS', 'attribute': attribute,
                'answer': f'No indexed Jira requirements mention {attribute} in the configured project scope.',
                'candidate_jiras': [], 'recency_verified': False}
    if live_client is None and bool(getattr(settings, 'JIRA_LIVE_ENABLED', False)):
        from services.jira.jira_live_client import jira_live_client
        live_client = jira_live_client
    dated, undated, errors = [], [], []
    for item in matches:
        issue = None
        if live_client is not None:
            try:
                issue = live_client.get_issue(item['jira_id'])
            except (ValueError, LookupError, RuntimeError, OSError) as exc:
                errors.append({'jira': item['jira_id'], 'error_type': type(exc).__name__})
        created = _parse_created((issue or {}).get('created_at'))
        entry = {'jira': item['jira_id'], 'summary': (issue or {}).get('summary') or item.get('summary'),
                 'jira_created_at': created.isoformat() if created else None,
                 'source': 'LIVE_JIRA' if created else 'INDEXED_REQUIREMENT'}
        (dated if created else undated).append((created, entry))
    if undated:
        # Cannot assert newest while even one candidate lacks comparable Jira creation time.
        answer = (f'Cannot verify the latest Jira for {attribute}: Jira creation dates are unavailable '
                  f'for {len(undated)} of {len(matches)} indexed matches. Related indexed Jiras: ' +
                  ', '.join(sorted(x['jira'] for _, x in dated + undated)) +
                  '. These are requirement mentions, not verified code changes. ' +
                  'Enable live Jira access and ensure all candidate issues are accessible to verify recency.')
        return {'status': 'RECENCY_NOT_VERIFIED', 'source': 'INDEXED_JIRA_PLUS_OPTIONAL_LIVE_JIRA',
                'attribute': attribute, 'answer': answer, 'candidate_jiras': [x for _, x in dated + undated],
                'recency_verified': False, 'lookup_errors': errors, 'coverage': candidates.get('coverage')}
    dated.sort(key=lambda pair: pair[0].timestamp(), reverse=True)
    winner = dated[0][1]
    return {'status': 'FOUND', 'source': 'LIVE_JIRA_CREATED_AT', 'attribute': attribute,
            'answer': f"Latest indexed Jira mentioning {attribute}: {winner['jira']} — {winner['summary']} "
                      f"(Jira created: {winner['jira_created_at']}). "
                      'This is the latest among indexed matches, not necessarily all live Jira issues; '
                      'a requirement mention does not prove implementation.',
            'latest_jira': winner, 'candidate_jiras': [x for _, x in dated],
            'recency_verified': True, 'coverage': candidates.get('coverage')}

"""Generic JIRA SDLC reports, evidence-aware QA mapping and release aggregation."""
from __future__ import annotations
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from config import settings
from services.agent.jira_commit_trace_service import JiraCommitTraceService
from services.agent.commit_call_path_service import analyse_commit_paths
from services.knowledge.test_suite_service import TestSuiteService
from sdlc_models import SdlcTestExecution

JIRA_RE = re.compile(r'[A-Z][A-Z0-9_]*-\d+\Z')


def scope(path):
    return re.sub(r'/+', '/', str(path or '').strip().replace('\\', '/')).rstrip('/').casefold()


def jira_key(value):
    key = str(value or '').strip().upper()
    if not JIRA_RE.fullmatch(key):
        raise ValueError('Invalid JIRA key')
    return key


def project_path(value):
    result = str(value or settings.JAVA_PROJECT_PATH or '').strip()
    if not result:
        raise ValueError('project_path is required')
    return result


def unique_cases(records):
    """Deduplicate test IDs, preserve every source and flag conflicting case definitions."""
    groups = defaultdict(list)
    for c in records:
        if c.get('test_id'):
            groups[str(c['test_id']).strip().casefold()].append(c)
    output = []
    for _, copies in sorted(groups.items()):
        first = copies[0]
        descriptions = {(str(c.get('scenario') or '').strip(), str(c.get('expected') or '').strip()) for c in copies}
        output.append({'test_id': first['test_id'], 'jira': first.get('jira'),
                       'scenario': first.get('scenario'), 'input': first.get('input'),
                       'expected': first.get('expected'), 'attribute': first.get('attribute'),
                       'declared_operation': first.get('operation'),
                       'conflicting_source_content': len(descriptions) > 1,
                       'sources': [{'kind': c.get('source_kind'), 'reference': c.get('source_ref'),
                                    'filename': c.get('source_filename'), 'sheet': c.get('sheet'), 'row': c.get('row')}
                                   for c in copies]})
    return output


def map_tests(cases, paths):
    """Only explicit operation/endpoint declarations qualify as verified mappings.

    Scenario wording is useful as a suggestion, never proof of coverage.
    """
    for case in cases:
        explicit = str(case.get('declared_operation') or '').strip()
        confirmed, suggestions = [], []
        for p in paths:
            endpoint = p['endpoint']
            operation = p['service_method']
            if explicit and (explicit.casefold() == endpoint.casefold() or explicit.casefold() == operation.casefold()):
                confirmed.append({'endpoint': endpoint, 'classification': 'DECLARED_OPERATION_MATCH',
                                  'note': 'QA source explicitly declares this operation; execution not proven'})
            elif not explicit and re.search(r'\b' + re.escape(operation) + r'\b', str(case.get('scenario') or ''), re.I):
                suggestions.append({'endpoint': endpoint, 'classification': 'INFERRED_FROM_SCENARIO_TEXT'})
        case['operation_mappings'] = confirmed
        case['candidate_mappings'] = suggestions
        case['execution_status'] = 'NOT_VERIFIED'
    return cases


def _jira_details(key):
    if not settings.JIRA_LIVE_ENABLED:
        return {'status': 'NOT_AVAILABLE', 'reason': 'Live JIRA disabled'}
    try:
        from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService
        service = EnterpriseKnowledgeSyncService()
        issue = service._jira_request(f'/rest/api/3/issue/{key}?fields=summary,description,status,issuetype,fixVersions,labels')
        fields = issue.get('fields') or {}
        return {'status': 'RETRIEVED', 'key': key, 'summary': fields.get('summary'),
                'description': service._adf_text(fields.get('description') or {}),
                'issue_type': (fields.get('issuetype') or {}).get('name'),
                'jira_status': (fields.get('status') or {}).get('name'),
                'fix_versions': [x.get('name') for x in fields.get('fixVersions') or []],
                'labels': fields.get('labels') or []}
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'reason': str(exc)[:300]}


def build_report(db, *, jira, project, include_jira=True):
    key = jira_key(jira)
    project = project_path(project)
    git_service = JiraCommitTraceService(project)
    trace = git_service.analyse(key)
    commit_paths = analyse_commit_paths(git_service, trace)
    paths = []
    semantic = []
    for entry in commit_paths.get('commits', []):
        paths.extend([{**p, 'commit': entry['commit']} for p in entry.get('verified_source_paths', [])])
        semantic.extend([{**s, 'commit': entry['commit']} for s in entry.get('semantic_changes', [])])
    raw = TestSuiteService().search(db, project_path=project, jira=key, limit=500)
    cases = map_tests(unique_cases(raw['test_cases']), paths)
    evidence_rows = db.query(SdlcTestExecution).filter(SdlcTestExecution.jira_key == key).order_by(SdlcTestExecution.id.desc()).all()
    evidence_rows = [e for e in evidence_rows if scope(e.project_path) == scope(project)]
    by_test = {}
    for e in evidence_rows:
        by_test.setdefault(e.test_id.casefold(), e)
    for case in cases:
        e = by_test.get(case['test_id'].casefold())
        if e:
            case['execution_status'] = e.status
            case['execution_evidence'] = {'record_id': e.id, 'git_revision': e.git_revision,
                                          'evidence_ref': e.evidence_ref, 'executed_at': e.executed_at.isoformat(),
                                          'recorded_by': e.recorded_by}
    unique_paths = list({(p['commit'], p['endpoint'], p['path']): p for p in paths}.values())
    verified = sum(1 for c in cases if c['execution_status'] in ('PASSED', 'FAILED', 'BLOCKED', 'SKIPPED'))
    return {'jira': key, 'project_path': project, 'requirement': _jira_details(key) if include_jira else {'status': 'NOT_REQUESTED'},
            'git': {'association': trace.get('status'), 'commits': trace.get('commits', []), 'link_rule': trace.get('link_rule')},
            'semantic_changes': semantic, 'source_verified_paths': unique_paths,
            'qa': {'imported_records': raw['count'], 'unique_test_count': len(cases),
                   'tests_with_recorded_execution': verified, 'test_cases': cases,
                   'note': 'Recorded execution evidence is user-supplied; it is not independently validated by CodeIntelligence'},
            'evidence_gaps': [message for condition, message in (
                (not trace.get('commits'), 'No explicit JIRA-linked Git commit found'),
                (not unique_paths, 'No source-verified controller paths found'),
                (not cases, 'No imported QA cases found'),
                (verified < len(cases), 'Some QA cases lack recorded execution evidence')) if condition],
            'release_readiness': 'NOT_ESTABLISHED',
            'provenance': 'Git commit history + source call analysis + project-scoped imported QA + separately recorded execution'}


def release_report(db, release):
    keys = json.loads(release.jira_keys_json)
    reports = [build_report(db, jira=k, project=release.project_path) for k in keys]
    tests = {}
    endpoints = {}
    for r in reports:
        for p in r['source_verified_paths']:
            endpoints.setdefault(p['endpoint'], []).append({'jira': r['jira'], 'commit': p['commit'], 'path': p['path']})
        for c in r['qa']['test_cases']:
            # Same test ID across JIRAs is distinct unless explicitly associated.
            tests[(r['jira'], c['test_id'].casefold())] = {'jira': r['jira'], **c}
    return {'release_key': release.release_key, 'project_path': release.project_path,
            'jira_keys': keys, 'jira_reports': reports, 'unique_affected_endpoints': endpoints,
            'test_cases': list(tests.values()),
            'summary': {'jira_count': len(keys), 'unique_endpoint_count': len(endpoints),
                        'unique_jira_test_pairs': len(tests),
                        'tests_with_recorded_execution': sum(c['execution_status'] != 'NOT_VERIFIED' for c in tests.values())},
            'readiness': 'NOT_ESTABLISHED',
            'note': 'Live report; only captured snapshots are immutable. Test execution and approval are not inferred.'}

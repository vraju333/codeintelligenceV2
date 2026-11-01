"""Shared, evidence-preserving investigation contract for Web and IntelliJ.

Never convert candidate relationships into verified impacts or unexecuted plans into
successful tests. The payload is intentionally derived from the existing ASK result.
"""
from __future__ import annotations
from typing import Any


def _rows(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ('items', 'results', 'records', 'test_cases'):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def build_investigation(query: str, project_path: str | None, result: dict) -> dict:
    evidence = []
    for field, kind, label in (
        ('attribute_verified_paths', 'SOURCE_VERIFIED', 'Verified attribute paths'),
        ('verified_change_jiras', 'SOURCE_VERIFIED', 'Verified change JIRAs'),
        ('predictive_api_candidates', 'CANDIDATE', 'Potentially affected APIs'),
        ('predictive_scenario_candidates', 'CANDIDATE', 'Potentially affected scenarios'),
        ('jira_test_baselines', 'RECORDED_BASELINE', 'JIRA-linked baselines'),
        ('mapping_rows', 'DOCUMENT_EVIDENCE', 'Mapping rows'),
        ('mapping_documents', 'DOCUMENT_EVIDENCE', 'Mapping documents'),
        ('project_release_baselines', 'PROJECT_SCOPE_ONLY', 'Project baselines'),
    ):
        items = _rows(result.get(field))
        if items:
            evidence.append({'source_field': field, 'title': label, 'classification': kind,
                             'count': len(items), 'items': items})
    predictive = result.get('predictive_impact')
    if isinstance(predictive, dict):
        for field, label in (('regression_recommendations', 'Regression recommendations'),
                             ('risk_summary', 'Risk assessment'),
                             ('mapping_contract_checks', 'Mapping contract checks')):
            value = predictive.get(field)
            if value:
                evidence.append({'source_field': 'predictive_impact.' + field, 'title': label,
                                 'classification': 'PREDICTIVE_NOT_EXECUTED', 'items': value})
    warnings = list(result.get('evidence_warnings') or [])
    warnings.extend(result.get('predictive_warnings') or [])
    if not evidence:
        warnings.append('No structured impact evidence was returned; do not infer test coverage or risk.')
    return {
        'contract_version': 'codeintelligence.investigation.v1',
        'query': query, 'project_path': project_path,
        'status': result.get('status', 'UNKNOWN'),
        'answer': result.get('answer', ''),
        'source': result.get('source', 'KNOWLEDGE_AGENT'),
        'evidence': evidence, 'warnings': warnings,
        'coverage': {'status': 'NOT_DETERMINED', 'note': 'Missing indexed tests are not proof that a path is untested.'},
        'execution_verified': bool(result.get('execution_verified', False)),
        'presentation_plan': result.get('presentation_plan'),
    }

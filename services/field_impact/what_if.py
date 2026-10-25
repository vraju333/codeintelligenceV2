"""Requirement simulation: distinguish observed syntax from proposed changes."""
from __future__ import annotations

import re


def _mentioned(requirement: str, field: str) -> bool:
    return bool(re.search(r'(?<!\w)' + re.escape(field) + r'(?!\w)', requirement, re.IGNORECASE))


def simulate_requirement(requirement: str, results: list[dict]) -> dict:
    referenced = sorted({r['field'] for r in results if _mentioned(requirement, r['field'])})
    existing = []
    for result in results:
        field = result['field']
        if field not in referenced:
            continue
        files = sorted({item['file'] for kind in ('writers', 'readers', 'declarations')
                        for item in result.get(kind, []) if item.get('file')})
        # An empty syntax scan cannot establish that a field exists in source.
        if not files:
            continue
        existing.append({
            'field': field,
            'source_files': files,
            'kind': 'EXISTING_FIELD_IMPACT',
            'evidence_level': 'DIRECT_SYNTAX',
            'verified_runtime_path': False,
        })

    proposed = []
    by_name = {r['field'].lower(): r for r in results}
    if _mentioned(requirement, 'fullName') and {'firstname', 'lastname'}.issubset(
        {field.lower() for field in referenced}
    ):
        full_name = by_name.get('fullname', {})
        has_syntax = any(full_name.get(kind) for kind in ('writers', 'readers', 'declarations'))
        proposed.append({
            'proposed_field': 'fullName',
            'depends_on': ['firstName', 'lastName'],
            'kind': 'PROPOSED_DERIVED_ATTRIBUTE',
            'evidence_level': 'REQUIREMENT_INFERENCE',
            'implemented_in_source': bool(has_syntax),
        })

    return {
        'requirement': requirement,
        'referenced_fields': referenced,
        'existing_code_evidence': existing,
        'proposed_changes': proposed,
        'limitations': [
            'Document/JIRA mapping evidence must be verified separately',
            'No runtime execution, database schema migration or test result is inferred',
        ],
    }

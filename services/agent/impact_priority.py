"""Evidence-bounded impact prioritization for agent answers.

Never promotes attribute overlap to a verified Jira link or an endpoint to a
verified call edge. Uses only tool results returned in this request.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _obj(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def _category(method: str, occurrence: list[dict]) -> str:
    name = method.rsplit('.', 1)[-1]
    if re.fullmatch(r'(?:get|set|is)[A-Z].*', name):
        return 'ATTRIBUTE_REFERENCE_ONLY'
    text = ' '.join(str(item.get(k) or '') for item in occurrence for k in ('statement', 'source_line', 'line_text', 'code', 'snippet'))
    if re.search(r'validat|verif|check|require|assert', name, re.I) or re.search(r'\.matches\(|Pattern\.compile|throw new .*Exception', text):
        return 'PRIMARY_CHANGE_CANDIDATE'
    return 'RELATED_REGRESSION_CANDIDATE'


def build_priority_report(tool_results: list[dict], query: str) -> str:
    if not re.search(r'\b[A-Z][A-Z0-9]+-\d+\b', query or ''):
        return ''
    statics = []
    scenarios = []
    for item in tool_results:
        name = item.get('name')
        obj = _obj(item.get('content'))
        if name == 'static_code_analysis' and obj.get('attribute'):
            statics.append(obj)
        elif name == 'scenario_impact' and obj.get('attribute'):
            scenarios.append(obj)
    if not statics:
        return ''
    parts = ['### Deterministic impact prioritization (source candidates, not verified Jira implementations)']
    for data in statics:
        attribute = str(data['attribute'])
        parts.append(f'**Attribute:** `{attribute}`')
        occurrences = data.get('occurrences') or []
        methods = sorted(set(str(m) for m in data.get('direct_attribute_methods') or [] if m))
        groups: dict[str, list[str]] = {'PRIMARY_CHANGE_CANDIDATE': [], 'RELATED_REGRESSION_CANDIDATE': [], 'ATTRIBUTE_REFERENCE_ONLY': []}
        for method in methods:
            relevant = [row for row in occurrences if f"{row.get('class_name')}.{row.get('method_name')}" == method]
            groups[_category(method, relevant)].append(method)
        for category, label in (
            ('PRIMARY_CHANGE_CANDIDATE', 'Primary change candidates (inspect validation rules)'),
            ('RELATED_REGRESSION_CANDIDATE', 'Related regression candidates (change not established)'),
            ('ATTRIBUTE_REFERENCE_ONLY', 'Attribute-only references (no edit implied)'),
        ):
            values = groups[category]
            if values:
                parts.append(f'- **{label}:** ' + ', '.join(f'`{v}`' for v in values[:30]))
        primary = set(groups['PRIMARY_CHANGE_CANDIDATE'])
        matching = [row for row in scenarios if str(row.get('attribute')).casefold() == attribute.casefold()]
        if matching:
            high, conditional, not_established = [], [], []
            for row in matching[0].get('affected_scenarios') or []:
                verb = str(row.get('http_method') or '').upper()
                path = str(row.get('endpoint') or '')
                if not verb or not path:
                    continue
                label = f'`{verb} {path}`'
                matched = set(row.get('matched_methods') or [])
                # A matching method name without a call path is not proof of execution.
                relevance = str(row.get('relevance') or '').upper()
                if verb in {'POST', 'PUT'} and relevance in {'DIRECT', 'TRANSITIVE'} and (not primary or matched.intersection(primary)):
                    high.append(label)
                elif verb in {'POST', 'PUT', 'PATCH'}:
                    conditional.append(label)
                else:
                    not_established.append(label)
            if high:
                parts.append('- **High-priority regression candidates (tool-reported method path):** ' + ', '.join(dict.fromkeys(high)))
            if conditional:
                parts.append('- **Conditional regression (validate actual call path):** ' + ', '.join(dict.fromkeys(conditional)))
            if not_established:
                parts.append('- **No validation-path impact established:** ' + ', '.join(dict.fromkeys(not_established)))
    parts.append('**Evidence boundary:** Direct attribute references are not verified Jira-to-code links. Endpoint relevance from the existing scanner is candidate flow evidence, not a resolved call-graph proof. Existing test-baseline coverage requires an explicit linked record.')
    return '\n'.join(parts)

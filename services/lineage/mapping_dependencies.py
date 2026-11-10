"""Conservative, documentary mapping-rule dependency candidates.

Only known target attributes are considered. This is not Java data-flow proof.
"""
from __future__ import annotations

import re


def canonical(value: str | None) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def references(rule: str | None, attribute: str | None) -> bool:
    """Match complete identifiers, including camelCase, snake_case and spaced names."""
    if not rule or not attribute:
        return False
    parts = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', attribute).replace('_', ' ').split()
    if not parts:
        return False
    expression = r'(?<![A-Za-z0-9_])' + r'[\s_-]*'.join(map(re.escape, parts)) + r'(?![A-Za-z0-9_])'
    return bool(re.search(expression, rule, flags=re.I))


def dependencies(rows, selected: str | None = None) -> list[dict]:
    """Return evidence-backed edges from rule references to target attributes."""
    names = {canonical(r.target_attribute): r.target_attribute for r in rows if r.target_attribute}
    selected_key = canonical(selected) if selected else None
    result = []
    seen = set()
    for row in rows:
        target = row.target_attribute
        rule = row.mapping_rule
        for key, source in names.items():
            if key == canonical(target) or (selected_key and key != selected_key):
                continue
            if not references(rule, source):
                continue
            marker = (row.document_id, row.id, key)
            if marker in seen:
                continue
            seen.add(marker)
            result.append({
                'source_attribute': source,
                'dependent_attribute': target,
                'target': row.target_expression or target,
                'mapping_rule': rule,
                'mapping_row_id': row.id,
                'document_id': row.document_id,
                'classification': 'DOCUMENTED_MAPPING_RULE_REFERENCE_NOT_VERIFIED_CODE_FLOW',
            })
    return result

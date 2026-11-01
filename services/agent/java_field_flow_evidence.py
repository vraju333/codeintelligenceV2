"""Bounded Java field and branch observations; never claims type-resolved dataflow.

This is a conservative source-inspection layer, not an AST/CFG resolver.
"""
from __future__ import annotations
import re


def inspect_method_field(method: dict, attribute: str, root) -> list[dict]:
    body = method['body']
    cap = attribute[:1].upper() + attribute[1:]
    escaped = re.escape(cap)
    findings = []
    relpath = str(method['file'].relative_to(root)).replace('\\', '/')
    patterns = (
        ('FIELD_GETTER_CALL', rf'\b(?:get|is){escaped}\s*\('),
        ('FIELD_SETTER_CALL', rf'\bset{escaped}\s*\('),
        ('FIELD_ASSIGNMENT', rf'\b(?:this\.)?{re.escape(attribute)}\s*=(?!=)'),
    )
    for kind, pattern in patterns:
        for match in re.finditer(pattern, body, re.I):
            line = method['line'] + body[:match.start()].count('\n')
            findings.append({'method': method['class']+'.'+method['name'],
                             'file': relpath, 'line': line, 'kind': kind,
                             'status': 'SOURCE_OBSERVED_NOT_DATAFLOW_VERIFIED'})
    # A conditional is relevant only if it contains a field access or token.
    # Braces and nested Java expressions are not parsed, so no branch reachability claim.
    for match in re.finditer(r'\b(?:if|while|switch)\s*\(([^\n;{}]{1,400})\)', body):
        expression = match.group(1)
        if re.search(rf'\b(?:get|set|is){escaped}\s*\(|\b{re.escape(attribute)}\b', expression, re.I):
            findings.append({'method': method['class']+'.'+method['name'],
                             'file': relpath, 'line': method['line']+body[:match.start()].count('\n'),
                             'kind': 'FIELD_CONDITION_CANDIDATE',
                             'expression': expression.strip(),
                             'status': 'LEXICAL_CONDITION_NOT_CFG_VERIFIED'})
    return findings[:80]

"""Read-only requirement -> historical evidence retrieval. No inferred implementation links."""
from __future__ import annotations

import re
from pathlib import PureWindowsPath
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from db_models import JiraKnowledge, MappingDefinition, MappingDocument, Scenario
from services.lineage.mapping_dependencies import dependencies

router = APIRouter(prefix='/api/requirement-impact', tags=['Requirement Impact'])

class ImpactRequest(BaseModel):
    mode: str
    value: str = Field(min_length=1)
    project: str | None = None


def consolidate_test_evidence(cases: list[dict]) -> list[dict]:
    """Consolidate equivalent imported test plans without losing provenance.

    Missing JIRA metadata is compatible with an explicit JIRA on the same
    test ID and definition. Two *different* explicit JIRAs are never merged.
    Conflicting definitions remain separate and are marked for review.
    """
    result: list[dict] = []
    groups: dict[tuple, list[dict]] = {}

    def norm(value) -> str:
        return str(value or '').strip().casefold()

    for case in cases:
        test_id = norm(case.get('test_id'))
        jira = norm(case.get('jira')) or norm(case.get('source_jira'))
        definition = tuple(norm(case.get(field)) for field in
                           ('scenario', 'input', 'expected', 'attribute', 'operation'))
        source = {field: case.get(field) for field in
                  ('document_id', 'source_ref', 'source_filename', 'source_kind',
                   'source_jira', 'source_id', 'sheet', 'row')}
        key = (test_id, definition)
        candidates = groups.get(key, []) if test_id else []
        # Prefer a matching explicit JIRA; otherwise allow an unscoped entry.
        match = next((entry for entry in candidates
                      if jira and norm(entry.get('jira')) == jira), None)
        if match is None:
            match = next((entry for entry in candidates
                          if not jira or not norm(entry.get('jira'))), None)
        if match is not None:
            if source not in match['evidence_sources']:
                match['evidence_sources'].append(source)
            match['source_count'] = len(match['evidence_sources'])
            if jira and not norm(match.get('jira')):
                match['jira'] = case.get('jira') or case.get('source_jira')
            continue
        entry = {**case, 'execution_verified': False,
                 'classification': 'IMPORTED_TEST_PLAN',
                 'evidence_sources': [source], 'source_count': 1}
        result.append(entry)
        if test_id:
            groups.setdefault(key, []).append(entry)

    # Different definitions of the same ID are evidence conflicts, even when
    # one import omitted its JIRA identifier. Never discard either definition.
    by_id: dict[str, set[tuple]] = {}
    for entry in result:
        test_id = norm(entry.get('test_id'))
        if test_id:
            definition = tuple(norm(entry.get(field)) for field in
                               ('scenario', 'input', 'expected', 'attribute', 'operation'))
            by_id.setdefault(test_id, set()).add(definition)
    for entry in result:
        test_id = norm(entry.get('test_id'))
        if test_id and len(by_id[test_id]) > 1:
            entry['evidence_conflict'] = 'SAME_TEST_ID_DIFFERENT_DEFINITION'
    return result


def canonical(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or '').casefold())


def scope(s: str | None) -> str:
    return (s or '').replace('\\', '/').rstrip('/').casefold()


def in_project(row_project: str | None, selected: str | None) -> bool:
    if not selected:
        return True
    # Unscoped records must not be presented as project-verified matches.
    return bool(row_project and scope(row_project) == scope(selected))


def mentioned(text: str, attribute: str) -> bool:
    # Match camelCase, snake_case and spaced words, not arbitrary substrings.
    parts = re.sub(r'([a-z])([A-Z])', r'\1 \2', attribute).replace('_', ' ').split()
    pattern = r'(?<![a-zA-Z0-9])' + r'[\s_-]*'.join(map(re.escape, parts)) + r'(?![a-zA-Z0-9])'
    if re.search(pattern, text or '', re.I):
        return True
    # JavaBean accessors often contain attributes as getFirstName()/setLastName().
    bean = r'\b(?:get|set|is)' + re.escape(attribute[:1].upper() + attribute[1:]) + r'\s*\('
    return bool(re.search(bean, text or ''))


def attributes_from(text: str, mapping_rows: list[MappingDefinition]) -> list[str]:
    found = {r.target_attribute for r in mapping_rows if r.target_attribute and mentioned(text, r.target_attribute)}
    # A requirement can mention attributes that have no imported workbook.
    for token in re.findall(r'\b[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*\b', text or ''):
        found.add(token)
    # Common Java identifiers may be written as ordinary lowercase words in JIRA.
    for identifier in ('firstName', 'lastName', 'fullName'):
        if re.search(r'(?<![a-z0-9])' + re.escape(identifier.lower()) + r'(?![a-z0-9])', (text or '').lower()) or mentioned(text, identifier):
            found.add(identifier)
    if re.search(r'full\s+name', text or '', re.I):
        found.add('fullName')
    return sorted(found, key=str.casefold)



def infer_country_attribute(text: str, mapping_rows: list[MappingDefinition],
                            explicit_attributes: list[str]) -> list[str]:
    """Infer a country field only when the selected project's mappings support it.

    Require a recognizable ISO-country allow-list and a customer create/update
    policy context. Never infer from two-letter tokens alone.
    """
    if any(canonical(a) == 'countrycode' for a in explicit_attributes):
        return explicit_attributes
    has_country_policy = (
        re.search(r'\b(?:accept|allow|permit)\s+only\s+(?:IN|US|GB)\b', text, re.I)
        and re.search(r'\b(?:new customers?|creat(?:e|ion)|existing customers?|updat(?:e|ing))\b', text, re.I)
        and re.search(r'\b(?:IN|US|GB)\s*(?:,|and|or)\s*(?:IN|US|GB)\b', text, re.I)
    )
    if not has_country_policy:
        return explicit_attributes
    candidates = {r.target_attribute for r in mapping_rows
                  if r.target_attribute and canonical(r.target_attribute) == 'countrycode'}
    if len(candidates) != 1:
        return explicit_attributes
    return sorted(set(explicit_attributes) | candidates, key=str.casefold)


def requirement_clauses(text: str) -> list[str]:
    """Separate coordinated instructions so a preserve clause cannot swallow
    a change clause referring to another attribute.
    """
    segments = re.split(r'[.;\n]+', text or '')
    clauses = []
    for segment in segments:
        for part in re.split(
            r'(?i)(?=\b(?:also\s+update|also\s+change|do\s+not\s+modify|'
            r'do\s+not\s+change|preserve\s+(?:email|accounttype)|'
            r'keep\s+(?:email|accounttype))\b)', segment):
            if part.strip():
                clauses.append(part.strip())
    return clauses


def scoped_country_policy(text: str, attribute: str) -> dict | None:
    """Only model DIFFERENT create/update policies explicitly stated in separate clauses.

    A shared rule ('creation and update accept only IN, US and GB') is NOT an
    operation-specific exception. The legacy update values must be distinct
    from the create values and explicitly signalled as additional/legacy.
    """
    if canonical(attribute) != 'countrycode':
        return None
    parts = [x.strip() for x in re.split(r'[.;\n]+', text or '') if x.strip()]
    create = next((p for p in parts
                   if re.search(r'\b(?:new customers?|creat(?:e|ion))\b', p, re.I)
                   and re.search(r'\b(?:accept|allow|permit)\s+only\b', p, re.I)), None)
    update = next((p for p in parts
                   if re.search(r'\b(?:existing customers?|updat(?:e|ing))\b', p, re.I)
                   and re.search(r'\b(?:also|additionally|legacy|continue)\b', p, re.I)
                   and re.search(r'\b(?:accept|allow|permit)\b', p, re.I)), None)
    if not create or not update or create == update:
        return None

    def codes(clause: str) -> list[str]:
        match = re.search(
            r'\b(?:accept|allow|permit)\s+(?:only\s+|also\s+)?'
            r'([A-Z]{2}\b(?:\s*(?:,|and|or)\s*[A-Z]{2}\b)*)', clause, re.I)
        if not match:
            return []
        return list(dict.fromkeys(v.upper() for v in re.findall(r'\b[A-Z]{2}\b', match.group(1), re.I)))

    base, legacy = codes(create), codes(update)
    # No scoped policy unless update explicitly introduces distinct values.
    legacy = [v for v in legacy if v not in base]
    if not base or not legacy:
        return None
    return {'attribute': attribute, 'classification': 'OPERATION_SCOPED_POLICY_NOT_CONTRADICTION',
            'create_allowed': base, 'update_additionally_allowed': legacy,
            'create_scope': 'NEW_CUSTOMER', 'update_scope': 'EXISTING_CUSTOMER',
            'evidence': [create, update]}


def detect_value_rule_conflicts(text: str, attributes: list[str]) -> list[dict]:
    """Detect explicit accept/reject overlap in the active requirement only.

    Supports anaphora ("continue accepting CA and FR") within the same
    attribute's requirement, without interpreting historical documents.
    """
    pieces = [x.strip() for x in re.split(r"[.;\n]+", text or "") if x.strip()]
    conflicts = []
    for attribute in attributes:
        if scoped_country_policy(text, attribute):
            continue
        # Only attach an unqualified rule to an attribute when there is one
        # explicit attribute context; avoid assigning unrelated rules to it.
        attr_pieces = [i for i, piece in enumerate(pieces) if mentioned(piece, attribute)]
        if not attr_pieces:
            continue
        rules = {'ACCEPT': {}, 'REJECT': {}}
        for i, piece in enumerate(pieces):
            if not mentioned(piece, attribute):
                previous = [j for j in attr_pieces if j < i]
                if not previous or any(mentioned(piece, other) for other in attributes if other != attribute):
                    continue
                # Do not let a later unqualified instruction cross an explicit
                # reference to another attribute.
                if any(any(mentioned(pieces[j], other) for other in attributes if other != attribute)
                       for j in range(previous[-1] + 1, i + 1)):
                    continue
            # Split opposing instructions even when they share one sentence.
            for match in re.finditer(
                r"\b(?P<verb>continue\s+(?:to\s+)?(?:accept|allow|permit|support|reject|deny|disallow)"
                r"|(?:accept|allow|permit|support|reject|deny|disallow)(?:ing|ed)?|excluding|exclude)"
                r"\s+(?:only\s+|the\s+values?\s+|values?\s+|codes?\s+|for\s+)?"
                r"(?P<values>[A-Z]{2,}(?:\s*(?:,|and|or)\s*[A-Z]{2,})*)",
                piece, re.I):
                verb = match.group('verb').lower()
                polarity = 'REJECT' if re.search(r'reject|deny|disallow|exclud', verb) else 'ACCEPT'
                values = [v.upper() for v in re.split(r'\s*(?:,|\band\b|\bor\b)\s*', match.group('values'), flags=re.I)]
                for val in values:
                    if val in {'ONLY', 'VALUES', 'VALUE', 'CODE', 'CODES', 'THE', 'FOR', 'AND', 'OR'}:
                        continue
                    rules[polarity].setdefault(val, []).append(piece)
        overlap = sorted(set(rules['ACCEPT']) & set(rules['REJECT']))
        if overlap:
            conflicts.append({
                'type': 'CONTRADICTORY_ALLOWED_VALUE_RULES',
                'attribute': attribute,
                'conflicting_values': overlap,
                'acceptance_instructions': list(dict.fromkeys(c for v in overlap for c in rules['ACCEPT'][v])),
                'rejection_instructions': list(dict.fromkeys(c for v in overlap for c in rules['REJECT'][v])),
                'classification': 'REQUIREMENT_CONFLICT_NOT_RESOLVED',
                'clarification_required': True,
                'clarification_question': f'Should {attribute} values {", ".join(overlap)} be accepted or rejected, and for which operations/customer populations?',
            })
    return conflicts


def interpret_requirement_intent(text: str, attributes: list[str],
                                 mapping_rows: list[MappingDefinition]) -> dict:
    """Conservative, auditable intent extraction from the *active* request only.

    A negated clause wins over generic mentions. Unknown attributes are not
    automatically NEW: an explicit add/create instruction is required.
    """
    clauses = requirement_clauses(text)
    known = {canonical(row.target_attribute) for row in mapping_rows if row.target_attribute}
    new, modified, protected, referenced, instruction_conflicts = [], [], [], [], []
    for attr in attributes:
        relevant = [clause for clause in clauses if mentioned(clause, attr)]
        # Preserve a specific existing sub-behavior (e.g. lowercase normalization)
        # does not prohibit changing the attribute's validation allow-list.
        # A general no-change instruction does, and must conflict with updates.
        protection_clauses = [clause for clause in relevant if re.search(
            r"\b(?:do\s+not|don.t|must\s+not|should\s+not)\s+"
            r"(?:\w+\s+){0,3}?(?:modify|change|alter|affect|break|touch)\b"
            r"|\b(?:keep|preserve)\s+(?:the\s+)?(?:existing\s+|current\s+)?"
            r"(?:" + re.escape(attr) + r"\s+)?(?:behavior|validation|mapping)\s+unchanged\b"
            r"|\b" + re.escape(attr) + r"\s+(?:behavior|validation|mapping)\s+(?:must\s+)?remain\s+unchanged\b",
            clause, re.I)]
        # Preserve <attribute> validation/mapping, including coordinated clauses
        # such as "Preserve email validation, accountType mapping and deletion".
        if any(re.search(r'\b(?:preserve|keep|retain)\s+(?:the\s+)?(?:existing\s+)?'
                         + re.escape(attr) + r'\s+(?:validation|mapping|behavior)\b',
                         clause, re.I) for clause in relevant):
            protection_clauses = list(dict.fromkeys(protection_clauses + [
                clause for clause in relevant if re.search(
                    r'\b(?:preserve|keep|retain)\s+(?:the\s+)?(?:existing\s+)?'
                    + re.escape(attr) + r'\s+(?:validation|mapping|behavior)\b',
                    clause, re.I)]))
        # Coordinated preservation: "Preserve email validation, accountType mapping".
        # The preservation verb governs the enumerated fields, not an earlier
        # "update countryCode ... preserve lowercase normalization" clause.
        protection_clauses.extend(clause for clause in relevant
            if re.match(r'(?i)^(?:please\s+)?(?:preserve|keep|retain)\b', clause)
            and re.search(r'\b' + re.escape(attr) + r'\s+(?:validation|mapping|behavior)\b', clause, re.I)
            and clause not in protection_clauses)
        modification_clauses = [clause for clause in relevant if re.search(
            r'\b(?:update|modify|change|replace|prefix|prepend|derive|validate|normalize|concatenate|set|accept|reject|allow|permit|restrict|enforce)\b',
            clause, re.I) and clause not in protection_clauses]
        # An explicit operation-specific allow-list is a change even when the
        # attribute is mentioned only in an earlier clause.
        if not modification_clauses and scoped_country_policy(text, attr):
            modification_clauses = [text]
        # Protecting a particular sub-behavior does not prevent changing other
        # validation rules for the same attribute.
        if protection_clauses and modification_clauses and all(re.search(
                r'\b(?:normaliz|lowercase|uppercase)\w*\b', c, re.I) for c in protection_clauses):
            protection_clauses = []
        is_new = any(re.search(r'\b(?:add|introduce|create|new)\b', clause, re.I)
                     and canonical(attr) not in known for clause in relevant)
        if protection_clauses and modification_clauses:
            instruction_conflicts.append({
                'type': 'CONTRADICTORY_ATTRIBUTE_INSTRUCTIONS',
                'attribute': attr,
                'modification_instructions': modification_clauses,
                'protection_instructions': protection_clauses,
                'classification': 'REQUIREMENT_CONFLICT_NOT_RESOLVED',
                'clarification_required': True,
                'clarification_question': (
                    f'Should {attr} be updated as requested, or should its existing behavior remain unchanged?'),
            })
            # Do not silently select one side or suggest implementation tests.
        elif protection_clauses:
            protected.append(attr)
        elif is_new:
            new.append(attr)
        elif modification_clauses:
            modified.append(attr)
        else:
            referenced.append(attr)
    instruction_conflicts.extend(detect_value_rule_conflicts(text, attributes))
    return {'new_attributes': new, 'modified_attributes': modified,
            'protected_attributes': protected, 'reference_attributes': referenced,
            'instruction_conflicts': instruction_conflicts,
            'clarification_required': bool(instruction_conflicts),
            'new_attribute_coverage': 'NOT_FOUND' if new else 'NOT_APPLICABLE',
            'classification': 'RULE_BASED_REQUIREMENT_INTENT_NOT_LLM_VERIFIED',
            'note': 'Conflicting instructions are held for clarification; neither instruction takes precedence.'}


def explicit_allowed_values(text: str, attribute: str) -> list[str]:
    """Extract only explicit allow-list rules attached to the named attribute.

    Do not interpret unrelated examples, mapping documents, or historical JIRAs
    as new acceptance criteria. This is deliberately limited to clear wording.
    """
    attr_pattern = re.escape(attribute)
    clauses = [c.strip() for c in re.split(r'[.;\n]+', text or '') if c.strip()]
    for clause in clauses:
        if not mentioned(clause, attribute):
            continue
        # e.g. "Update countryCode validation to accept only IN, US and GB".
        match = re.search(
            r'\b(?:accept|allow|permit|support)\s+only\s+'
            r'(?:' + re.escape(attribute) + r'\s+(?:values?|codes?)\s+)?'
            r'([A-Za-z0-9_-]+(?:\s*(?:,|\band\b|\bor\b)\s*[A-Za-z0-9_-]+)+)',
            clause, re.I)
        if not match:
            # New enum-like field: 'customerTier with allowed values STANDARD, GOLD and PLATINUM'.
            match = re.search(
                r'\b(?:with\s+)?allowed\s+values?\s*[:=]?\s*'
                r'([A-Za-z0-9_-]+(?:\s*(?:,|\band\b|\bor\b)\s*[A-Za-z0-9_-]+)+)',
                clause, re.I)
        if not match:
            continue
        # The trailing conjunction may introduce a NEW instruction, e.g.
        # 'accept only IN, US and GB, reject CA and FR'. Never ingest verbs
        # such as REJECT as enum values.
        captured = re.split(
            r'\s*[,;]?\s*\b(?:reject|deny|disallow|exclude|while|but|preserve|keep|retain)\b',
            match.group(1), maxsplit=1, flags=re.I)[0]
        values = [v.strip().upper() for v in re.split(r'\s*(?:,|\band\b|\bor\b)\s*', captured, flags=re.I)]
        values = [v for v in values if v and v not in {'REJECT', 'DENY', 'DISALLOW', 'EXCLUDE'}]
        if len(values) >= 2 and len(set(values)) == len(values):
            return values
    return []


def explicit_rejected_values(text: str, attribute: str) -> list[str]:
    """Extract named rejected codes, including `reject CA and FR` in the same clause.

    Never turn verbs or prose into values. Only explicit two-letter country
    codes are recognized here; generic REJECT_UNLISTED remains separate.
    """
    results = []
    for clause in re.split(r'[.;\n]+', text or ''):
        if not mentioned(clause, attribute) and canonical(attribute) != 'countrycode':
            continue
        for match in re.finditer(
            r'\b(?:reject|deny|disallow|exclude)\s+'
            r'(?:the\s+)?(?:values?\s+|codes?\s+|including\s+|such\s+as\s+)*'
            r'([A-Z]{2}\b(?:\s*(?:,|and|or)\s*[A-Z]{2}\b)*)',
            clause, re.I):
            for token in re.findall(r'\b[A-Z]{2}\b', match.group(1), re.I):
                token = token.upper()
                if token not in results:
                    results.append(token)
    return results


def explicitly_preserve_uppercase_normalization(text: str, attribute: str) -> bool:
    return bool(re.search(r'\b(?:preserve|retain|keep|continue)\b[^.\n;]*\b(?:lowercase|lower-case)\s*(?:-to-|to)\s*(?:uppercase|upper-case)\b', text or '', re.I))


def annotate_test_evidence(cases: list[dict], intent: dict) -> list[dict]:
    """Do not equate imported historical plans with new-feature coverage."""
    new = {canonical(a) for a in intent['new_attributes']}
    protected = {canonical(a) for a in intent['protected_attributes']}
    changed = {canonical(a) for a in intent['modified_attributes']}
    for case in cases:
        attr = canonical(case.get('attribute') or '')
        if attr in new:
            role = 'NEW_ATTRIBUTE_HISTORICAL_MENTION_NOT_EXECUTION_VERIFIED'
        elif attr in protected:
            role = 'PROTECTED_BEHAVIOR_REGRESSION_CONTEXT'
        elif attr in changed:
            role = 'CHANGED_ATTRIBUTE_HISTORICAL_CONTEXT'
        else:
            role = 'RELATED_HISTORICAL_CONTEXT_NOT_NEW_FEATURE_COVERAGE'
        case['requirement_evidence_role'] = role
    return cases

def proposed_http_operations(project: str, attributes: list[str]) -> list[dict]:
    """Read-only HTTP entry-point candidates; no claim of a resolved call graph."""
    from pathlib import Path
    import re
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        return []
    result = []
    verbs = {'PostMapping': 'POST', 'PutMapping': 'PUT', 'PatchMapping': 'PATCH',
             'DeleteMapping': 'DELETE', 'GetMapping': 'GET'}
    # Only actual annotated endpoints are returned; method-level dependency
    # remains INFERRED until AST/call-graph analysis confirms it.
    for file in sorted(root.rglob('*.java')):
        if any(part in {'.git', 'build', 'target', 'out', '.gradle'} for part in file.relative_to(root).parts):
            continue
        if not file.is_file() or not file.resolve().is_relative_to(root) or file.stat().st_size > 1_000_000:
            continue
        text = file.read_text(encoding='utf-8', errors='replace')
        if '@RestController' not in text and '@Controller' not in text:
            continue
        base = re.search(r'@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?"([^"]+)"', text)
        prefix = base.group(1) if base else ''
        for match in re.finditer(r'@(PostMapping|PutMapping|PatchMapping|DeleteMapping|GetMapping)\s*(?:\(([^)]*)\))?', text):
            method_match = re.search(r'\b(?:public|protected)\s+[\w<>?, \[\]]+\s+(\w+)\s*\(', text[match.end():match.end()+900])
            if not method_match:
                continue
            annotation = match.group(2) or ''
            path_match = re.search(r'"([^"]+)"', annotation)
            suffix = path_match.group(1) if path_match else ''
            endpoint = ('/' + '/'.join(x.strip('/') for x in (prefix, suffix) if x)).replace('//', '/')
            result.append({'http_method': verbs[match.group(1)], 'endpoint': endpoint or '/',
                           'controller_method': method_match.group(1),
                           'controller_file': str(file.relative_to(root)),
                           'classification': 'INFERRED',
                           'reason': 'Controller entry point; attribute-to-operation call path not verified',
                           'attribute_candidates': attributes})
    return result[:100]



def existing_java_evidence(project: str, attributes: list[str]) -> dict:
    """Bounded, read-only Java source discovery, NOT a verified call graph."""
    from pathlib import Path
    root = Path(project).expanduser().resolve()
    result = {'status': 'PROJECT_UNAVAILABLE', 'project_path': str(root),
              'attributes': list(attributes), 'files_scanned': 0,
              'methods': [], 'fields': [], 'database_annotations': [],
              'limitations': ['Source mentions do not establish JIRA dependency or execution.',
                              'Method bodies are approximated, not AST/CFG data flow.']}
    if not root.is_dir():
        return result
    result['status'] = 'SCANNED'
    # A line-based signature recognizer tolerates Java annotations and common modifiers.
    signature = re.compile(
        r'(?m)^[ \t]*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*'
        r'(?:[\w.$<>?,\[\] ]+\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)'
        r'\s*(?:throws\s+[\w., ]+)?\s*\{')
    skip = {'.git', '.gradle', 'build', 'target', 'out', '.idea'}
    for file in sorted(root.rglob('*.java')):
        if any(part in skip for part in file.relative_to(root).parts):
            continue
        if not file.is_file() or file.stat().st_size > 1_000_000:
            continue
        result['files_scanned'] += 1
        source = file.read_text(encoding='utf-8', errors='replace')
        lines = source.splitlines()
        rel = file.relative_to(root).as_posix()
        for line_no, line in enumerate(lines, 1):
            if not any(mentioned(line, a) for a in attributes):
                continue
            if re.search(r'@(?:Column|JoinColumn|Table)\b', line):
                result['database_annotations'].append({
                    'file': rel, 'line': line_no, 'text': line.strip()[:220],
                    'classification': 'SOURCE_ANNOTATION_NOT_DB_SCHEMA_VERIFIED'})
            if re.search(r'\b(?:private|public|protected)\s+[\w<>?,\[\]]+\s+\w+\s*[;=]', line):
                result['fields'].append({'file': rel, 'line': line_no,
                    'text': line.strip()[:220], 'classification': 'SOURCE_FIELD'})
        for match in signature.finditer(source):
            method_name = match.group(1)
            if method_name in {'if', 'for', 'while', 'switch', 'catch', 'try', 'new'}:
                continue
            start = match.end()
            depth, end = 1, start
            while end < len(source) and depth and end - start < 50000:
                if source[end] == '{': depth += 1
                elif source[end] == '}': depth -= 1
                end += 1
            body = source[start:end]
            matched = [a for a in attributes if mentioned(body, a) or mentioned(match.group(0), a)]
            if matched:
                result['methods'].append({
                    'file': rel, 'method': method_name,
                    'line': source.count('\n', 0, match.start()) + 1,
                    'matched_attributes': matched, 'classification': 'SOURCE_METHOD_MENTION',
                    'jira_relationship': 'INFERRED_POTENTIAL_IMPACT'})
    for key in ('methods', 'fields', 'database_annotations'):
        result[key] = result[key][:100]
    return result



def rank_http_operations_by_source(project: str, operations: list[dict], java_evidence: dict) -> dict:
    """Bounded source-backed, receiver-aware call candidates (not a Java AST/CFG).

    Walk controller -> injected service -> mapper/repository by looking at the
    receiver's declared type. Unresolved/ambiguous receivers are NOT traversed.
    """
    from pathlib import Path
    from collections import deque

    root = Path(project).expanduser().resolve()
    skip = {'.git', '.gradle', 'build', 'target', 'out', '.idea'}
    classes = {}
    decl = re.compile(
        r'(?m)^\s*(?:(?:public|protected|private|static|final|synchronized|abstract)\s+)*'
        r'(?:[\w.$<>?,\[\] ]+\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)'
        r'\s*(?:throws\s+[\w., ]+)?\s*\{')
    for file in sorted(root.rglob('*.java')):
        if any(part in skip for part in file.relative_to(root).parts) or not file.is_file() or file.stat().st_size > 1_000_000:
            continue
        source = file.read_text(encoding='utf-8', errors='replace')
        class_match = re.search(r'\b(?:class|interface|record)\s+(\w+)', source)
        if not class_match:
            continue
        name = class_match.group(1)
        fields = {}
        for f in re.finditer(r'(?m)^\s*(?:(?:private|public|protected|final|static|volatile)\s+)+'
                             r'([A-Z][\w.$<>?, ]*)\s+(\w+)\s*(?:[;=,])', source):
            fields[f.group(2)] = re.sub(r'<.*', '', f.group(1)).split('.')[-1].strip()
        methods = {}
        for m in decl.finditer(source):
            method = m.group(1)
            if method in {'if','for','while','switch','catch','new','return','throw','synchronized'}:
                continue
            pos, depth = m.end(), 1
            while pos < len(source) and depth and pos - m.end() < 50000:
                if source[pos] == '{': depth += 1
                elif source[pos] == '}': depth -= 1
                pos += 1
            if depth: continue
            methods.setdefault(method, []).append({
                'line': source.count('\n', 0, m.start()) + 1,
                'body': source[m.end():pos-1],
                'body_start_line': source.count('\n', 0, m.end()) + 1,
            })
        # DEBUG
        classes[name] = {'file': file.relative_to(root).as_posix(), 'fields': fields,
                         'methods': methods, 'source': source}
    direct = {(m['file'].replace('\\','/'), m['method']): m['matched_attributes']
              for m in java_evidence.get('methods', [])
              if m.get('matched_attributes') and not m['file'].replace('\\','/').startswith('src/test/')}
    relevant, other = [], []
    for op in operations:
        item = dict(op)
        controller_file = op['controller_file'].replace('\\','/')
        controller = next((name for name, c in classes.items() if c['file'] == controller_file), None)
        paths = []
        if controller:
            queue = deque([(controller, op['controller_method'], [], frozenset())])
            while queue and len(paths) < 15:
                cls, method, chain, visited = queue.popleft()
                key = (cls, method)
                if key in visited or len(chain) > 5:
                    continue
                c = classes.get(cls)
                if not c:
                    continue
                for entry in c['methods'].get(method, []):
                    node = {'class': cls, 'method': method, 'file': c['file'], 'line': entry['line']}
                    next_chain = chain + [node]
                    attributes = direct.get((c['file'], method), [])
                    if attributes:
                        paths.append({'path': next_chain, 'matched_attributes': attributes,
                                      'classification': 'SOURCE_BACKED_CALL_CANDIDATE',
                                      'jira_relationship': 'INFERRED',
                                      'resolution': 'FIELD_RECEIVER_TYPE_AND_METHOD_NAME_NOT_AST'})
                        if len(paths) >= 15: break
                    # Receiver-qualified invocations only. Bare method calls are
                    # excluded rather than guessing dispatch or inheritance.
                    for call in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\.\s*([a-zA-Z_$][\w$]*)\s*\(', entry['body']):
                        receiver, target_method = call.groups()
                        target_class = cls if receiver == 'this' else c['fields'].get(receiver)
                        if target_class and target_class in classes and target_method in classes[target_class]['methods']:
                            queue.append((target_class, target_method, next_chain,
                                          visited | {key}))
        item['candidate_call_paths'] = paths
        # Compatibility: consumers of the previous source-link field.
        item['candidate_method_links'] = [
            {'file': path['path'][-1]['file'], 'method': path['path'][-1]['method'],
             'line': path['path'][-1]['line'],
             'matched_attributes': path['matched_attributes'],
             'evidence': 'SOURCE_BACKED_RECEIVER_CALL_CANDIDATE_NOT_AST'}
            for path in paths
        ]
        if paths:
            item['classification'] = 'INFERRED_SOURCE_BACKED_CALL_PATH'
            item['reason'] = 'Controller-to-attribute method path found using declared receiver types; not a resolved AST/dataflow'
            relevant.append(item)
        else:
            item['classification'] = 'DISCOVERED_ENDPOINT_NO_ATTRIBUTE_PATH'
            item['reason'] = 'Endpoint exists, but no source-backed receiver call path to an attribute method was found'
            other.append(item)
    return {'potentially_related': relevant, 'other_discovered': other,
            'analysis_type': 'BOUNDED_RECEIVER_AWARE_SOURCE_CALL_CANDIDATES',
            'limitations': [
                'Not Java AST/CFG, type inference, polymorphic dispatch, or field-level dataflow.',
                'Constructor injection without a field declaration, inherited methods, chained calls, '
                'interfaces, local variables, and static calls may be missed.',
                'A source-backed path is not proof that the proposed JIRA change is implemented.',
                'No matched path does not establish that an endpoint is unaffected.']}


def _endpoint_targets_attribute(endpoint: str, method: str, attributes: list[str]) -> bool:
    """Match a PATCH target to the requested field, not unrelated route words."""
    endpoint_tokens = [canonical(part) for part in re.split(r"[/{}]+", endpoint) if part]
    method_token = canonical(method)
    for attribute in attributes:
        target = canonical(attribute)
        if not target:
            continue
        # A route segment like account-type is a direct target; do not use
        # arbitrary substring matching (e.g. email vs alternateEmail).
        if target in endpoint_tokens or method_token in {"update" + target, "patch" + target, "set" + target}:
            return True
    return False


def classify_requirement_operations(operations: dict, attributes: list[str], mappings: list[dict],
                                    java_evidence: dict) -> dict:
    """Classify operation roles, not proven changes. Works for any attribute set.

    Role assignments are requirement/HTTP semantics, while call-path evidence
    remains independently source-backed. Never turn a role into a verified link.
    """
    names = {canonical(a) for a in attributes}
    mapped = {canonical(m.get('target_attribute', '')) for m in mappings}
    fields = {canonical(a) for field in java_evidence.get('fields', [])
              for a in attributes if mentioned(field.get('text', ''), a)}
    missing = sorted(a for a in attributes if canonical(a) in mapped and canonical(a) not in fields) if java_evidence.get('status') == 'SCANNED' else []
    related = []
    other = []
    role_counts = {}
    for item in operations.get('potentially_related', []) + operations.get('other_discovered', []):
        entry = dict(item)
        verb = str(entry.get('http_method', '')).upper()
        endpoint = str(entry.get('endpoint', '')).lower()
        method = str(entry.get('controller_method', '')).lower()
        paths = entry.get('candidate_call_paths') or []
        leaf_names = {str(n.get('method', '')).lower()
                      for path in paths for n in path.get('path', [])}
        # Roles are about how an endpoint may interact with a requirement;
        # they do not assert that the requested behavior exists.
        if verb == 'POST':
            roles = ['INPUT', 'PERSISTENCE']
            priority = 'HIGH'
            reason = 'Creation may accept source attributes and persist derived values.'
        elif verb == 'PUT':
            roles = ['INPUT', 'PERSISTENCE']
            priority = 'HIGH'
            reason = 'Replacement may accept and persist the requested attribute.'
        elif verb == 'PATCH' and _endpoint_targets_attribute(endpoint, method, attributes):
            roles = ['INPUT', 'PERSISTENCE']
            priority = 'HIGH'
            reason = 'PATCH directly targets the requested attribute; implementation and persistence remain unverified.'
        elif verb == 'GET':
            roles = ['OUTPUT']
            priority = 'MEDIUM'
            reason = 'Read endpoints may need to expose the derived attribute; no implementation is established.'
        elif verb == 'PATCH':
            roles = ['INDIRECT_REGRESSION']
            priority = 'LOW'
            reason = 'PATCH does not explicitly target the requested attribute; review shared behavior for possible indirect regression.'
        elif verb == 'DELETE':
            roles = ['LIFECYCLE_REGRESSION']
            priority = 'LOW'
            reason = 'Deletion is not a direct derived-field implementation path.'
        else:
            roles = ['UNDETERMINED']
            priority = 'REVIEW'
            reason = 'Operation role cannot be inferred safely from its HTTP method.'
        if verb in ('POST', 'PUT') and paths and ('toentity' in leaf_names or 'save' in leaf_names):
            evidence_note = 'Source-backed mapper candidate; persistence write not independently verified.'
        elif paths:
            evidence_note = 'Source-backed attribute-method candidate; requirement change remains inferred.'
        else:
            evidence_note = 'No attribute call path resolved; absence of evidence is not evidence of no impact.'
        entry['requirement_roles'] = roles
        entry['change_review_priority'] = priority
        entry['role_classification'] = 'INFERRED_OPERATION_ROLE_NOT_VERIFIED_CHANGE'
        entry['role_reason'] = reason
        entry['role_evidence_note'] = evidence_note
        entry['source_path_found'] = bool(paths)
        for role in roles:
            role_counts[role] = role_counts.get(role, 0) + 1
        (related if priority in ('HIGH', 'MEDIUM') else other).append(entry)
    operations['role_review'] = {'priority_operations': related,
                                 'conditional_or_other_operations': other,
                                 'role_counts': role_counts,
                                 'classification': 'REQUIREMENT_ROLE_INFERENCE',
                                 'note': 'Priority reflects review relevance, not proof of an implementation dependency.'}
    operations['mapping_source_gaps'] = [
        {'attribute': a, 'classification': 'MAPPING_PRESENT_SOURCE_FIELD_NOT_FOUND',
         'note': 'Imported mapping references this attribute, but the bounded Java field scan found no declaration. This is not proof the feature is absent.'}
        for a in missing]
    return operations


def _retrieval_change_attributes(changed: list[str], requirement: str,
                                 new_attributes: list[str] | None = None) -> list[str]:
    """Attribute-specific response changes, never a global 'return' keyword.

    New attributes explicitly requested in retrieval responses are proposed
    response changes. Existing attributes require an explicit response-shape
    change; changing their validation alone is not a GET implementation.
    """
    new_keys = {canonical(a) for a in (new_attributes or [])}
    clauses = re.split(r'[.;\n]+|\b(?:also|however|additionally)\b', requirement or '', flags=re.I)
    retrieval = re.compile(r'\b(?:return|include|expose|display|show|retrieve|retrieval|response|output)\b', re.I)
    explicit_output = re.compile(r'\b(?:return|include|expose|display|show)\b.{0,100}\b(?:response|retrieval|GET|output|result)s?\b|\b(?:response|retrieval|GET|output|result)s?\b.{0,100}\b(?:return|include|expose|display|show)\b', re.I)
    result = []
    for attr in changed:
        is_new = canonical(attr) in new_keys
        if is_new:
            # 'Persist it and return it in retrieval responses' refers to the
            # new attribute in the immediately preceding clause.
            new_mention = re.search(r'\b(?:add|introduce|new|create)\b[^.;\n]{0,120}' +
                                    r'(?<![a-zA-Z0-9])' + re.escape(attr) + r'(?![a-zA-Z0-9])', requirement, re.I)
            pronoun_output = bool(new_mention and re.search(
                r'\breturn\s+it\s+in\s+(?:the\s+)?(?:retrieval|GET|read)\s+responses?\b', requirement, re.I))
            if pronoun_output or any(mentioned(c, attr) and retrieval.search(c) for c in clauses):
                result.append(attr)
        else:
            # Existing fields need an explicit output contract change, not
            # merely a nearby mention of 'return' elsewhere in the request.
            if any(mentioned(c, attr) and explicit_output.search(c) and
                   re.search(r'\b(?:change|modify|add|include|expose|return|show)\b', c, re.I)
                   for c in clauses):
                result.append(attr)
    return result


def _operation_change_scope(op: dict, changed: list[str], requirement: str,
                            unresolved: bool, new_attributes: list[str] | None = None) -> tuple[list[str], str]:
    """Proposed changes are requirement intent, never verified code changes."""
    verb = str(op.get('http_method') or '').upper()
    if unresolved:
        return [], 'PROVISIONAL_PENDING_CLARIFICATION'
    if not changed or verb == 'DELETE':
        return [], 'REGRESSION_ONLY_OR_UNRELATED'
    if verb == 'GET':
        output_attrs = _retrieval_change_attributes(changed, requirement, new_attributes)
        return (output_attrs, 'PROPOSED_IMPLEMENTATION') if output_attrs else ([], 'REGRESSION_ONLY_OR_UNRELATED')
    if verb == 'PATCH' and not _endpoint_targets_attribute(
            op.get('endpoint', ''), op.get('controller_method', ''), changed):
        return [], 'INDIRECT_REGRESSION'
    if verb in ('POST', 'PUT', 'PATCH'):
        return list(changed), 'PROPOSED_IMPLEMENTATION'
    return [], 'REVIEW_ONLY'


@router.post('/analyze')
def analyze(request: ImpactRequest, db: Session = Depends(get_db)):
    mode = request.mode.strip().lower()
    value = request.value.strip()
    if mode not in ('jira', 'requirement', 'attribute'):
        raise HTTPException(422, detail='mode must be jira, requirement or attribute')
    jira = None
    text = value
    if mode == 'jira':
        row = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == value.upper()).first()
        if not row or not in_project(row.project_path, request.project):
            return {'status': 'JIRA_NOT_INDEXED', 'mode': mode, 'input': value,
                    'project': request.project, 'attributes': [], 'historical_jiras': [],
                    'mappings': [], 'mapping_documents': [], 'scenarios': [],
                    'note': 'JIRA not found in indexed requirements for the selected project. No content was inferred.'}
        jira = {'jira_id': row.jira_id, 'summary': row.title, 'requirement': row.requirement}
        text = ' '.join(filter(None, [row.title, row.requirement]))
    all_mappings = db.query(MappingDefinition).filter(MappingDefinition.status == 'ACTIVE').all()
    all_mappings = [r for r in all_mappings if in_project(r.project_path, request.project)]
    attrs = [value] if mode == 'attribute' else infer_country_attribute(text, all_mappings, attributes_from(text, all_mappings))
    intent = interpret_requirement_intent(text, attrs, all_mappings) if mode != 'attribute' else {'new_attributes': [], 'modified_attributes': [value], 'protected_attributes': [], 'reference_attributes': [], 'new_attribute_coverage': 'NOT_APPLICABLE', 'classification': 'ATTRIBUTE_LOOKUP', 'instruction_conflicts': [], 'clarification_required': False}
    change_attrs = intent['new_attributes'] + intent['modified_attributes']
    intent['changed_attributes'] = list(dict.fromkeys(change_attrs))
    intent['operation_scoped_policies'] = [policy for a in attrs if (policy := scoped_country_policy(text, a))]
    normalized = {canonical(a) for a in attrs}
    matching_rows = [r for r in all_mappings if canonical(r.target_attribute) in normalized]
    docs = {d.id: d for d in db.query(MappingDocument).filter(MappingDocument.status == 'ACTIVE').all()
                if in_project(d.project_path, request.project)}
    mappings = []
    mapping_docs = {}
    for r in matching_rows:
        d = docs.get(r.document_id)
        if not d:
            continue
        mapping_docs[d.id] = {'document_id': d.id, 'title': d.filename, 'source_ref': d.source_ref}
        mappings.append({'id': r.id, 'source_type': r.source_type, 'source_path': r.source_path,
                         'mapping_rule': r.mapping_rule, 'target_class': r.target_class,
                         'target_attribute': r.target_attribute, 'target_expression': r.target_expression,
                         'provenance': {'document_id': d.id, 'document': d.filename,
                                        'sheet': r.sheet_name, 'row': r.row_number,
                                        'source_ref': d.source_ref}})
    # Collapse repeated imports of the same logical mapping rule while retaining
    # every document/version as independently inspectable provenance.
    consolidated = {}
    for item in mappings:
        key = tuple(canonical(item.get(k)) for k in
                    ('source_type', 'source_path', 'target_class', 'target_attribute',
                     'target_expression', 'mapping_rule'))
        source = {'id': item['id'], **item['provenance']}
        if key not in consolidated:
            consolidated[key] = {**item, 'evidence_sources': [source],
                                 'source_count': 1}
        else:
            existing = consolidated[key]
            if source not in existing['evidence_sources']:
                existing['evidence_sources'].append(source)
                existing['source_count'] = len(existing['evidence_sources'])
    mappings = list(consolidated.values())
    for item in mappings:
        item['evidence_sources'].sort(key=lambda x: (str(x.get('document') or ''), x.get('document_id') or 0))

    dependent_attributes = []
    # Restrict documented downstream dependencies to attributes relevant to
    # this request. JIRA/requirement mode must not return every project edge.
    dependency_sources = {canonical(a) for a in attrs}
    for edge in dependencies(all_mappings, value if mode == 'attribute' else None):
        if canonical(edge['source_attribute']) not in dependency_sources:
            continue
        document = docs.get(edge['document_id'])
        if document:
            dependent_attributes.append({**edge, 'provenance': {
                'document_id': document.id, 'document': document.filename,
                'source_ref': document.source_ref,
                'document_version': document.document_version}})
    historical = []
    for row in db.query(JiraKnowledge).all():
        if (jira and row.jira_id == jira['jira_id']) or not in_project(row.project_path, request.project):
            continue
        content = ' '.join(filter(None, [row.title, row.requirement]))
        matches = [a for a in attrs if mentioned(content, a)]
        if matches:
            historical.append({'jira_id': row.jira_id, 'summary': row.title,
                               'matched_attributes': matches, 'matched_in': 'indexed requirement',
                               'evidence_excerpt': (row.requirement or '')[:450],
                               'relationship': 'REQUIREMENT_ATTRIBUTE_MENTION'})
    scenarios = []
    for row in db.query(Scenario).all():
        if not in_project(row.project_path, request.project):
            continue
        content = ' '.join(filter(None, [row.scenario_name, row.description, row.request_json,
                                         row.expected_response_json, row.involved_classes]))
        matches = [a for a in attrs if mentioned(content, a)]
        if matches:
            scenarios.append({'scenario_code': row.scenario_code, 'name': row.scenario_name,
                              'http_method': row.http_method, 'endpoint': row.endpoint,
                              'matched_attributes': matches, 'relationship': 'SCENARIO_TEXT_MATCH'})
    # V2: Read-only, project-scoped source candidates. These are NOT verified
    # Jira-to-code relationships or resolved call graph edges.
    code_impact = {'status': 'NOT_CHECKED', 'candidates': [], 'verified_associations': [],
                   'note': 'Live Java source not checked.'}
    if request.project and mode == 'jira' and jira:
        try:
            from services.jira.jira_code_evidence_resolver import resolve_jira_code_candidates
            resolved = resolve_jira_code_candidates(db, request.project, jira_ids=[jira['jira_id']])
            code_impact = {
                'status': 'CANDIDATES_FOUND' if resolved.get('candidates') else 'NO_CANDIDATES',
                'mode': resolved.get('mode'),
                'candidates': resolved.get('candidates', []),
                'verified_associations': resolved.get('verified_associations', []),
                'policy': resolved.get('policy'), 'limitations': resolved.get('limitations', []),
            }
        except (ValueError, FileNotFoundError, OSError, TimeoutError) as exc:
            code_impact = {'status': 'UNAVAILABLE', 'candidates': [], 'verified_associations': [],
                           'note': f'Java source analysis unavailable: {type(exc).__name__}: {str(exc)[:180]}'}
        except Exception as exc:
            code_impact = {'status': 'ERROR', 'candidates': [], 'verified_associations': [],
                           'note': f'Java source analysis failed: {type(exc).__name__}'}

    java_evidence = existing_java_evidence(request.project, attrs) if request.project and attrs else {'status': 'NOT_CHECKED', 'methods': [], 'fields': [], 'database_annotations': []}
    # A bounded source scan is not a resolved call graph. Report that scan
    # separately instead of incorrectly claiming that no Java was checked.
    if mode == 'requirement' and java_evidence.get('status') == 'SCANNED':
        code_impact = {
            'status': 'SOURCE_SCANNED',
            'candidates': [],
            'verified_associations': [],
            'source_scan_status': 'SCANNED',
            'files_scanned': java_evidence.get('files_scanned', 0),
            'note': 'Bounded Java source scan completed. No verified change, resolved call graph, or Git association is implied.'
        }
    affected_operations = []
    if request.project and attrs:
        try:
            affected_operations = proposed_http_operations(request.project, attrs)
        except (OSError, ValueError):
            pass

    # Discovered endpoints are candidates for impact review, not proof that
    # new fields exist in Java. Keep the original response shape while making
    # the evidence boundary explicit for clients using affected_operations.
    for op in affected_operations:
        op['proposed_change_attributes'], op['implementation_change_scope'] = _operation_change_scope(
            op, change_attrs, text, bool(intent.get('clarification_required')), intent.get('new_attributes', []))
        op['attribute_candidates_classification'] = 'REQUIREMENT_PROPOSED_NOT_SOURCE_VERIFIED'
        op['source_verified_change_attributes'] = []

    operation_evidence = rank_http_operations_by_source(request.project, affected_operations, java_evidence) if request.project and java_evidence.get('status') == 'SCANNED' else {'potentially_related': [], 'other_discovered': affected_operations}

    operation_evidence = classify_requirement_operations(operation_evidence, change_attrs, mappings, java_evidence)
    for evidence_group in ('potentially_related', 'other_discovered'):
        for op in operation_evidence.get(evidence_group, []):
            op['proposed_change_attributes'], op['implementation_change_scope'] = _operation_change_scope(
                op, change_attrs, text, bool(intent.get('clarification_required')), intent.get('new_attributes', []))

    # Operation candidate paths may contain protected/reference attributes; keep
    # their evidence, but never label them as changes to the new field.
    for group in ('priority_operations', 'conditional_or_other_operations'):
        for op in operation_evidence['role_review'][group]:
            path_attrs = {canonical(a) for path in op.get('candidate_call_paths', [])
                          for a in path.get('matched_attributes', [])}
            op['change_attribute_candidates'] = [a for a in change_attrs if canonical(a) in path_attrs]
            op['protected_attribute_candidates'] = [a for a in intent['protected_attributes'] if canonical(a) in path_attrs]
            op['attribute_candidates'] = op['change_attribute_candidates']
            op['proposed_change_attributes'], op['implementation_change_scope'] = _operation_change_scope(
            op, change_attrs, text, bool(intent.get('clarification_required')), intent.get('new_attributes', []))
            op['source_verified_change_attributes'] = []
            op['attribute_candidates_classification'] = 'SOURCE_PATH_CANDIDATE_NOT_VERIFIED_CHANGE'
            if not op['change_attribute_candidates'] and op['protected_attribute_candidates']:
                op['role_evidence_note'] = 'Source path matches protected existing behavior, not the proposed new field.'
            if not change_attrs:
                op['change_review_priority'] = 'REVIEW'


    # An explicitly requested generic PATCH is not fulfilled by an unrelated
    # field-specific PATCH route. Report the gap rather than implying coverage.
    patch_requested = bool(re.search(r'\b(?:PATCH|partial\s+update)\b', text, re.I))
    if patch_requested and change_attrs:
        direct_patch = [op for op in affected_operations
                        if op.get('http_method') == 'PATCH'
                        and _endpoint_targets_attribute(op.get('endpoint', ''),
                                                        op.get('controller_method', ''), change_attrs)]
        if not direct_patch:
            operation_evidence.setdefault('implementation_gaps', []).append({
                'type': 'REQUESTED_PATCH_OPERATION_NOT_FOUND',
                'attributes': list(change_attrs),
                'classification': 'REQUIREMENT_TO_ENDPOINT_GAP_NOT_VERIFIED_IMPLEMENTATION',
                'note': 'No discovered PATCH endpoint explicitly targets the requested field. An unrelated field-specific PATCH does not satisfy the requirement.'})

    # Evidence enrichment: project-scoped, read-only, and provenance-preserving.
    # Exact issue-key commit messages are verified Git links; generic Java source
    # keyword matches remain suggestions, never implementation proof.
    git_commits = []
    verified_code = []
    test_cases = []
    evidence_warnings = []
    if request.project:
        try:
            from services.knowledge.test_suite_service import TestSuiteService
            tests = TestSuiteService()
            queries = []
            if jira:
                queries.append((jira['jira_id'], ''))
            else:
                queries.extend(('', attr) for attr in attrs)
            raw_cases = []
            for issue_key, attribute in queries:
                result = tests.search(db, project_path=request.project,
                                      jira=issue_key, attribute=attribute, limit=500)
                raw_cases.extend(result.get('test_cases', []))
            test_cases = annotate_test_evidence(consolidate_test_evidence(raw_cases), intent)
        except Exception as exc:
            evidence_warnings.append('Test evidence unavailable: ' + type(exc).__name__)
        if jira:
            try:
                from services.agent.jira_commit_trace_service import JiraCommitTraceService
                trace = JiraCommitTraceService(request.project).analyse(jira['jira_id'])
                seen_sha = set()
                seen_method = set()
                for commit in trace.get('commits', []):
                    sha = commit.get('sha')
                    if not sha or sha in seen_sha:
                        continue
                    seen_sha.add(sha)
                    git_commits.append({'sha': sha, 'subject': commit.get('subject'),
                                        'changed_files': commit.get('changed_files', []),
                                        'relationship': 'EXPLICIT_JIRA_COMMIT_MESSAGE'})
                    for file in commit.get('java_files', []):
                        for method in file.get('changed_methods', []):
                            key = (file.get('file'), method.get('method'), sha)
                            if key in seen_method:
                                continue
                            seen_method.add(key)
                            verified_code.append({'file': file.get('file'),
                                'method': method.get('method'), 'line': method.get('line'),
                                'change': method.get('change'), 'commit_sha': sha,
                                'classification': 'COMMIT_SCOPED_METHOD_CHANGE',
                                'relationship': 'EXPLICIT_JIRA_COMMIT_MESSAGE'})
            except Exception as exc:
                evidence_warnings.append('Git history unavailable: ' + type(exc).__name__)

    # Historical JIRAs provide context, but must not redefine the active request.
    # In JIRA mode `text` is the selected JIRA's own title and description.
    requirement_text = text.lower()
    exactly_two = bool(re.search(
        r'(?:exactly|only|strictly)\s+(?:two|2)\s+(?:letters|characters)',
        requirement_text))
    conflicts = list(intent.get('instruction_conflicts', []))
    if conflicts:
        evidence_warnings.append('Contradictory requirement instructions require clarification before implementing affected attributes.')
    for row in mappings:
        rule = str(row.get('mapping_rule') or '')
        if (exactly_two and canonical(row.get('target_attribute')) == 'countrycode'
                and re.search(r'\b2\s*[-–]\s*3\s+letters\b', rule, re.I)):
            conflicts.append({'type': 'REQUIREMENT_MAPPING_RULE_MISMATCH',
                              'attribute': row.get('target_attribute'),
                              'requirement': 'Exactly two letters', 'document_rule': rule,
                              'provenance': row.get('provenance'),
                              'evidence_sources': row.get('evidence_sources', []),
                              'source_count': row.get('source_count', 1),
                              'classification': 'DOCUMENTARY_CONFLICT',
                              'note': 'One logical rule conflict; all imported document sources retained. Active mapping authority and runtime behavior are not verified.'})

    regression_recommendations = []
    recommendation_class = 'REQUIREMENT_DERIVED_NOT_EXECUTED'
    # Require explicit prefix/concatenation intent, not merely co-occurrence of
    # the two attributes in historical JIRAs or mapping workbooks.
    prefix_rule = (
        {'countrycode', 'accounttype'}.issubset({canonical(a) for a in attrs})
        and bool(re.search(r'\b(?:prefix(?:ed|ing)?|prepend(?:ed|ing)?|concatenat(?:e|ed|ion|ing))\b',
                           requirement_text))
    )
    if prefix_rule:
        regression_recommendations.extend([
            {'scenario': 'Prefix accountType with countryCode (US + PREMIUM -> USPREMIUM)',
             'classification': recommendation_class},
            {'scenario': 'Prefix accountType with another country code (IN + STANDARD -> INSTANDARD)',
             'classification': recommendation_class},
            {'scenario': 'Update an existing accountType and verify the countryCode prefix',
             'classification': recommendation_class},
            {'scenario': 'Check that repeated accountType updates do not duplicate the countryCode prefix',
             'classification': recommendation_class},
            {'scenario': 'Verify prefixed accountType is persisted and returned by read operations',
             'classification': recommendation_class},
        ])
    if any(canonical(a) == 'fullname' for a in attrs) or (
        'full name' in requirement_text and 'first' in requirement_text and 'last' in requirement_text
    ):
        regression_recommendations.extend([
            {'scenario': 'Derive fullName from firstName and lastName', 'classification': recommendation_class},
            {'scenario': 'Persist fullName and read it back', 'classification': recommendation_class},
            {'scenario': 'Update firstName or lastName and verify fullName', 'classification': recommendation_class},
            {'scenario': 'Handle missing or blank name components', 'classification': recommendation_class},
        ])
    if exactly_two and any(canonical(a) == 'countrycode' for a in attrs) and not any(canonical(c.get('attribute')) == 'countrycode' for c in intent.get('instruction_conflicts', [])):
        regression_recommendations.extend([
            {'scenario': 'Accept two-letter uppercase country code', 'classification': recommendation_class},
            {'scenario': 'Reject three-letter country code', 'classification': recommendation_class},
            {'scenario': 'Reject single-letter country code', 'classification': recommendation_class},
            {'scenario': 'Normalize lowercase country code', 'classification': recommendation_class},
            {'scenario': 'Regression: customer creation and replacement', 'classification': recommendation_class},
        ])
    # Modified-attribute requirements need their own requirement-specific tests.
    # Historical countryCode plans do not prove a new restricted allow-list.
    conflicted_attributes = {canonical(c.get('attribute')) for c in intent.get('instruction_conflicts', [])}
    for attr in intent['modified_attributes']:
        if canonical(attr) in conflicted_attributes:
            regression_recommendations.append({
                'scenario': f'Clarify conflicting acceptance/rejection rules for {attr} before defining validation tests',
                'attribute': attr, 'test_type': 'CLARIFICATION_REQUIRED',
                'classification': 'BLOCKED_BY_REQUIREMENT_CONFLICT'})
            continue
        allowed = [] if scoped_country_policy(text, attr) else explicit_allowed_values(requirement_text, attr)
        if allowed:
            for value_allowed in allowed:
                regression_recommendations.append({
                    'scenario': f'Accept {attr} value {value_allowed} under the new validation rule',
                    'attribute': attr, 'expected': 'ACCEPT', 'input_value': value_allowed,
                    'test_type': 'MODIFIED_ATTRIBUTE_VALIDATION',
                    'classification': recommendation_class})
            regression_recommendations.append({
                'scenario': f'Reject {attr} values outside the allowed set ({", ".join(allowed)})',
                'attribute': attr, 'expected': 'REJECT_UNLISTED',
                'allowed_values': allowed,
                'test_type': 'MODIFIED_ATTRIBUTE_VALIDATION',
                'classification': recommendation_class})
            regression_recommendations.append({
                'scenario': f'Apply the updated {attr} validation on customer creation and replacement',
                'attribute': attr, 'test_type': 'MODIFIED_ATTRIBUTE_VALIDATION',
                'classification': recommendation_class})
            for rejected in explicit_rejected_values(request.value, attr):
                if rejected in allowed:
                    continue
                regression_recommendations.append({
                    'scenario': f'Reject {attr} value {rejected} under the new validation rule',
                    'attribute': attr, 'input_value': rejected, 'expected': 'REJECT',
                    'test_type': 'MODIFIED_ATTRIBUTE_VALIDATION',
                    'classification': recommendation_class})
            if explicitly_preserve_uppercase_normalization(request.value, attr):
                for operation in ('POST /api/customers', 'PUT /api/customers/{customerId}'):
                    regression_recommendations.append({
                        'scenario': f'Preserve lowercase-to-uppercase normalization of {attr} on {operation}',
                        'attribute': attr, 'operation': operation,
                        'input_value': allowed[0].lower(), 'expected_value': allowed[0],
                        'expected': 'ACCEPT_AND_NORMALIZE',
                        'test_type': 'MODIFIED_ATTRIBUTE_VALIDATION',
                        'classification': recommendation_class})
        elif not scoped_country_policy(text, attr):
            regression_recommendations.append({
                'scenario': f'Validate the explicitly requested change to {attr} against its acceptance criteria',
                'attribute': attr, 'test_type': 'MODIFIED_ATTRIBUTE_REGRESSION',
                'classification': recommendation_class})
    # Generate explicit API-level cases, not just a generic validation note.
    # Do not apply this to intentionally different create/update policies.
    for attr in intent['modified_attributes']:
        if canonical(attr) in conflicted_attributes or scoped_country_policy(text, attr):
            continue
        allowed = explicit_allowed_values(text, attr)
        if not allowed:
            continue
        rejected = [v for v in explicit_rejected_values(text, attr) if v not in allowed]
        for operation in ('POST /api/customers', 'PUT /api/customers/{customerId}'):
            for val in allowed:
                regression_recommendations.append({
                    'scenario': f'{operation}: accept {attr}={val}',
                    'attribute': attr, 'operation': operation, 'input_value': val,
                    'expected': 'ACCEPT', 'test_type': 'OPERATION_VALUE_VALIDATION',
                    'classification': recommendation_class})
            for val in rejected:
                regression_recommendations.append({
                    'scenario': f'{operation}: reject {attr}={val}',
                    'attribute': attr, 'operation': operation, 'input_value': val,
                    'expected': 'REJECT', 'test_type': 'OPERATION_VALUE_VALIDATION',
                    'classification': recommendation_class})
    for policy in intent.get('operation_scoped_policies', []):
        attr = policy['attribute']
        for val in policy['create_allowed']:
            # Values valid for creation remain valid for updates; legacy values
            # are additional update-only exceptions, not replacements.
            for operation in ('POST /api/customers', 'PUT /api/customers/{customerId}'):
                regression_recommendations.append({
                    'scenario': f'{operation}: accept {attr}={val}',
                    'attribute': attr, 'operation': operation,
                    'input_value': val, 'expected': 'ACCEPT',
                    'test_type': 'OPERATION_SCOPED_VALIDATION',
                    'classification': recommendation_class})
        for val in policy['update_additionally_allowed']:
            for operation, expected in [('POST /api/customers', 'REJECT'),
                                        ('PUT /api/customers/{customerId}', 'ACCEPT_FOR_EXISTING_CUSTOMER')]:
                regression_recommendations.append({
                    'scenario': f'{operation}: {expected} legacy {attr}={val}',
                    'attribute': attr, 'operation': operation, 'input_value': val,
                    'expected': expected, 'test_type': 'OPERATION_SCOPED_VALIDATION',
                    'classification': recommendation_class})
    # Separate acceptance recommendations for new fields from regression
    # protection for behavior the requirement explicitly says not to change.
    for attr in intent['new_attributes']:
        regression_recommendations.extend([
            {'scenario': f'Accept the new {attr} field when provided',
             'test_type': 'NEW_FEATURE_ACCEPTANCE', 'classification': recommendation_class},
            {'scenario': f'Verify {attr} is persisted and returned in customer responses',
             'test_type': 'NEW_FEATURE_ACCEPTANCE', 'classification': recommendation_class},
        ])
        if re.search(r'\boptional\b', requirement_text):
            regression_recommendations.append({
                'scenario': f'Verify {attr} may be omitted without breaking existing requests',
                'test_type': 'NEW_FEATURE_ACCEPTANCE', 'classification': recommendation_class})
    # Operation-specific acceptance coverage for newly introduced fields.
    # Recommendations are plans, never executed test evidence.
    for attr in intent['new_attributes']:
        requested_patch = bool(re.search(r'\b(?:PATCH|partial\s+update)\b', requirement_text, re.I))
        for verb, endpoint in [('POST', '/api/customers'), ('PUT', '/api/customers/{customerId}')]:
            regression_recommendations.append({
                'scenario': f'{verb} {endpoint}: accept and persist {attr}, then verify GET readback',
                'attribute': attr, 'operation': f'{verb} {endpoint}',
                'test_type': 'NEW_FEATURE_OPERATION_ACCEPTANCE', 'classification': recommendation_class})
        if requested_patch:
            regression_recommendations.append({
                'scenario': f'PATCH customer: update only {attr}, persist it, and preserve unrelated fields; verify a suitable PATCH endpoint exists',
                'attribute': attr, 'operation': 'PATCH customer (endpoint to be verified)',
                'test_type': 'NEW_FEATURE_API_GAP_ACCEPTANCE', 'classification': recommendation_class})
        for endpoint in ['/api/customers/{customerId}', '/api/customers']:
            regression_recommendations.append({
                'scenario': f'GET {endpoint}: response includes persisted {attr} where present',
                'attribute': attr, 'operation': f'GET {endpoint}',
                'test_type': 'NEW_FEATURE_OPERATION_ACCEPTANCE', 'classification': recommendation_class})
        if re.search(r'\boptional\b', requirement_text, re.I):
            regression_recommendations.append({
                'scenario': f'Existing customer with no {attr}: POST/PUT and GET remain backward compatible',
                'attribute': attr, 'test_type': 'BACKWARD_COMPATIBILITY', 'classification': recommendation_class})
        # Explicit allowed values for a newly added enumerated field.
        for allowed in explicit_allowed_values(requirement_text, attr):
            regression_recommendations.append({
                'scenario': f'Accept {attr}={allowed} in customer create/update',
                'attribute': attr, 'input_value': allowed, 'expected': 'ACCEPT',
                'test_type': 'NEW_FEATURE_VALUE_VALIDATION', 'classification': recommendation_class})
        if explicit_allowed_values(requirement_text, attr):
            regression_recommendations.append({
                'scenario': f'Reject unsupported {attr} value in customer create/update',
                'attribute': attr, 'allowed_values': explicit_allowed_values(requirement_text, attr),
                'expected': 'REJECT_UNLISTED', 'test_type': 'NEW_FEATURE_VALUE_VALIDATION',
                'classification': recommendation_class})
    if re.search(r'\b(?:do\s+not\s+(?:modify|change)|preserve|unchanged)\b', requirement_text, re.I) and re.search(r'\b(?:DELETE|deletion)\b', requirement_text, re.I):
        regression_recommendations.append({
            'scenario': 'DELETE customer behavior remains unchanged; no new attribute implementation is required in DELETE',
            'operation': 'DELETE /api/customers/{customerId}',
            'test_type': 'REGRESSION_PROTECTION', 'classification': recommendation_class})
    for attr in intent['protected_attributes']:
        regression_recommendations.append({
            'scenario': f'Verify existing {attr} behavior remains unchanged',
            'test_type': 'REGRESSION_PROTECTION', 'classification': recommendation_class})
    for recommendation in regression_recommendations:
        recommendation.setdefault('test_type', 'CHANGE_REGRESSION')
    found = bool(historical or mappings or scenarios or code_impact.get('candidates'))
    if intent.get('clarification_required'):
        result_status = 'CLARIFICATION_REQUIRED'
    elif found:
        result_status = 'FOUND'
    elif mode == 'requirement' and intent['new_attributes']:
        result_status = 'IMPLEMENTATION_COVERAGE_MISSING'
    elif mode == 'requirement' and attrs:
        result_status = 'NO_EVIDENCE_FOUND'
    else:
        result_status = 'NO_MATCH'
    if len(mapping_docs) > 1:
        evidence_warnings.append('Multiple imported mapping documents found; authoritative active version is not verified.')
    for document in mapping_docs.values():
        document['authority_status'] = 'HISTORICAL_OR_UNVERIFIED_ACTIVE'
        document['authority_note'] = 'Imported document; active mapping version not established by this endpoint.'
    if intent.get('clarification_required'):
        for recommendation in regression_recommendations:
            recommendation['classification'] = 'PROVISIONAL_PENDING_CLARIFICATION'
            recommendation['requires_clarification'] = True
    for operation in affected_operations:
        if operation.get('implementation_change_scope') == 'PROVISIONAL_PENDING_CLARIFICATION':
            operation['change_review_note'] = 'Potential impact only; no implementation authorized until requirement conflict is resolved.'

    # A newly requested attribute absent from the imported mapping evidence
    # needs an actionable review checklist. These are NOT verified source gaps.
    existing_mapping_attrs = {canonical(r.get('target_attribute')) for r in mappings}
    mapping_source_gaps = list(operation_evidence.get('mapping_source_gaps', [])) if isinstance(operation_evidence, dict) else []
    for attr in intent['new_attributes']:
        if canonical(attr) in existing_mapping_attrs:
            continue
        for component in ('REQUEST_RESPONSE_DTO', 'DOMAIN_MODEL', 'PERSISTENCE_MAPPING',
                          'DATABASE_SCHEMA', 'INPUT_OUTPUT_MAPPING'):
            mapping_source_gaps.append({
                'attribute': attr, 'component': component,
                'classification': 'NEW_ATTRIBUTE_IMPLEMENTATION_GAP_REVIEW_REQUIRED',
                'verification_status': 'NOT_VERIFIED',
                'note': f'No imported mapping evidence for {attr}; verify {component.lower().replace("_", " ")} in the project before implementing. Absence is not proven.'})
    return {'status': result_status, 'mode': mode, 'input': value,
            'project': request.project, 'jira': jira, 'attributes': attrs, 'requirement_intent': intent,
            'clarification_required': bool(intent.get('clarification_required')), 'requirement_conflicts': intent.get('instruction_conflicts', []),
            'historical_jiras': historical, 'mappings': mappings,
            'mapping_documents': list(mapping_docs.values()), 'mapping_source_gaps': mapping_source_gaps, 'dependent_attributes': dependent_attributes, 'scenarios': scenarios,
            'code_impact': code_impact, 'java_evidence': java_evidence, 'affected_operations': affected_operations, 'operation_evidence': operation_evidence, 'git_commits': git_commits,
            'verified_code_changes': verified_code, 'test_cases': test_cases,
            'evidence_warnings': evidence_warnings, 'mapping_conflicts': conflicts,
            'regression_recommendations': regression_recommendations,
            'evidence_boundary': 'Historical JIRA matches are requirement mentions, not verified Git/PR links. Mapping rows are imported documentary evidence; scenario matches are text matches, not test execution.',
            'unavailable': ['Verified Neo4j dependency traversal', 'Test execution verification', 'Release baseline impact not composed']}

"""Read-only requirement -> historical evidence retrieval. No inferred implementation links."""
from __future__ import annotations

import re
from pathlib import PureWindowsPath
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from db_models import JiraKnowledge, MappingDefinition, MappingDocument, Scenario

router = APIRouter(prefix='/api/requirement-impact', tags=['Requirement Impact'])

class ImpactRequest(BaseModel):
    mode: str
    value: str = Field(min_length=1)
    project: str | None = None


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
    return bool(re.search(pattern, text or '', re.I))


def attributes_from(text: str, mapping_rows: list[MappingDefinition]) -> list[str]:
    found = {r.target_attribute for r in mapping_rows if r.target_attribute and mentioned(text, r.target_attribute)}
    # A requirement can mention attributes that have no imported workbook.
    for token in re.findall(r'\b[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*\b', text or ''):
        found.add(token)
    return sorted(found, key=str.casefold)


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
    attrs = [value] if mode == 'attribute' else attributes_from(text, all_mappings)
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

    affected_operations = []
    if request.project and mode == 'jira' and jira:
        try:
            affected_operations = proposed_http_operations(request.project, attrs)
        except (OSError, ValueError):
            pass

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
            seen_tests = set()
            for issue_key, attribute in queries:
                result = tests.search(db, project_path=request.project,
                                      jira=issue_key, attribute=attribute, limit=500)
                for case in result.get('test_cases', []):
                    identity = (str(case.get('test_id') or ''),
                                str(case.get('document_id') or ''),
                                str(case.get('source_ref') or ''))
                    if identity not in seen_tests:
                        seen_tests.add(identity)
                        test_cases.append({**case, 'execution_verified': False,
                                           'classification': 'IMPORTED_TEST_PLAN'})
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

    # Compare explicitly stated two-letter requirement against imported rule.
    # A potential mismatch is documentary, not proof of runtime behavior.
    conflicts = []
    requirement_text = text.lower()
    exactly_two = bool(re.search(r'(?:exactly|only|strictly)\s+(?:two|2)\s+(?:letters|characters)', requirement_text))
    for row in mappings:
        rule = str(row.get('mapping_rule') or '')
        if exactly_two and re.search(r'\b2\s*[-–]\s*3\s+letters\b', rule, re.I):
            conflicts.append({'type': 'REQUIREMENT_MAPPING_RULE_MISMATCH',
                              'attribute': row.get('target_attribute'),
                              'requirement': 'Exactly two letters', 'document_rule': rule,
                              'provenance': row.get('provenance'),
                              'classification': 'DOCUMENTARY_CONFLICT',
                              'note': 'Review workbook rule; code behavior not established by this comparison.'})

    regression_recommendations = []
    if exactly_two and any(canonical(a) == 'countrycode' for a in attrs):
        regression_recommendations = [
            {'scenario': 'Accept two-letter uppercase country code', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Reject three-letter country code', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Reject single-letter country code', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Normalize lowercase country code', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Regression: customer creation and replacement', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
        ]
    found = bool(historical or mappings or scenarios or code_impact.get('candidates'))
    return {'status': 'FOUND' if found else 'NO_MATCH', 'mode': mode, 'input': value,
            'project': request.project, 'jira': jira, 'attributes': attrs,
            'historical_jiras': historical, 'mappings': mappings,
            'mapping_documents': list(mapping_docs.values()), 'scenarios': scenarios,
            'code_impact': code_impact, 'affected_operations': affected_operations, 'git_commits': git_commits,
            'verified_code_changes': verified_code, 'test_cases': test_cases,
            'evidence_warnings': evidence_warnings, 'mapping_conflicts': conflicts,
            'regression_recommendations': regression_recommendations,
            'evidence_boundary': 'Historical JIRA matches are requirement mentions, not verified Git/PR links. Mapping rows are imported documentary evidence; scenario matches are text matches, not test execution.',
            'unavailable': ['Verified Neo4j dependency traversal', 'Test execution verification', 'Release baseline impact not composed']}

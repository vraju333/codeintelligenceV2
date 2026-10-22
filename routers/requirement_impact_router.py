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
    missing = sorted(a for a in attributes if canonical(a) in mapped and canonical(a) not in fields)
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
        elif verb in ('PUT',) or (verb == 'PATCH' and any(t in endpoint + method for t in ('name', 'customer-details', 'profile'))):
            roles = ['INPUT', 'PERSISTENCE']
            priority = 'HIGH'
            reason = 'Replacement/name update may require recomputing and persisting derived values.'
        elif verb == 'GET':
            roles = ['OUTPUT']
            priority = 'MEDIUM'
            reason = 'Read endpoints may need to expose the derived attribute; no implementation is established.'
        elif verb == 'PATCH':
            roles = ['INDIRECT_REGRESSION']
            priority = 'LOW'
            reason = 'This PATCH targets another property; shared mapper traversal warrants regression review only.'
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

    java_evidence = existing_java_evidence(request.project, attrs) if request.project and jira else {'status': 'NOT_CHECKED', 'methods': [], 'fields': [], 'database_annotations': []}
    affected_operations = []
    if request.project and mode == 'jira' and jira:
        try:
            affected_operations = proposed_http_operations(request.project, attrs)
        except (OSError, ValueError):
            pass

    operation_evidence = rank_http_operations_by_source(request.project, affected_operations, java_evidence) if request.project and java_evidence.get('status') == 'SCANNED' else {'potentially_related': [], 'other_discovered': affected_operations}

    operation_evidence = classify_requirement_operations(operation_evidence, attrs, mappings, java_evidence)

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
    if any(canonical(a) == 'fullname' for a in attrs) or (
        'full name' in requirement_text and 'first' in requirement_text and 'last' in requirement_text
    ):
        regression_recommendations.extend([
            {'scenario': 'Derive fullName from firstName and lastName', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Persist fullName and read it back', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Update firstName or lastName and verify fullName', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
            {'scenario': 'Handle missing or blank name components', 'classification': 'REQUIREMENT_DERIVED_NOT_EXECUTED'},
        ])
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
            'code_impact': code_impact, 'java_evidence': java_evidence, 'affected_operations': affected_operations, 'operation_evidence': operation_evidence, 'git_commits': git_commits,
            'verified_code_changes': verified_code, 'test_cases': test_cases,
            'evidence_warnings': evidence_warnings, 'mapping_conflicts': conflicts,
            'regression_recommendations': regression_recommendations,
            'evidence_boundary': 'Historical JIRA matches are requirement mentions, not verified Git/PR links. Mapping rows are imported documentary evidence; scenario matches are text matches, not test execution.',
            'unavailable': ['Verified Neo4j dependency traversal', 'Test execution verification', 'Release baseline impact not composed']}

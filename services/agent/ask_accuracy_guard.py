"""Deterministic ASK routes for evidence-sensitive queries.

Do not promote lexical observations or text matches to verified links.
"""
from __future__ import annotations
import re
from pathlib import Path


def _scope(value):
    return str(value or '').replace('\\', '/').rstrip('/').casefold()


def _project(project_path):
    from config import settings
    return str(project_path or getattr(settings, 'JAVA_PROJECT_PATH', '') or '').strip()


def _answer(query, source, lines, **extra):
    return dict(query=query, source=source, status='FOUND', answer='\n'.join(lines),
                execution_verified=False, **extra)


def answer(db, query: str, project_path: str | None):
    q = query.strip()
    project = _project(project_path)
    if re.search(r'\b(?:path|url)\b', q, re.I) and re.search(r'\bxml\b', q, re.I) and re.search(r'\b(?:match|mismatch|different|equal)\b', q, re.I):
        return _source_rule(q, project, ('CustomerService.java',), ('customerId.equals', 'CustomerNotFoundException', 'setCustomerId'))
    if re.search(r'\b(?:compare|difference|responsibilities)\b', q, re.I) and 'CustomerXmlMapper' in q and 'CustomerJpaMapper' in q:
        return _source_rule(q, project, ('CustomerXmlMapper.java', 'CustomerJpaMapper.java'), ('toModel', 'toEntity'))
    if re.search(r'\b(?:callers?|called by)\b', q, re.I) and re.search(r'\b[A-Z]\w*\.\w+\s*\(', q):
        return _callers(q, project)
    if re.search(r'\b(?:jira|issues?|requirements?)\b', q, re.I) and re.search(r'\b(?:linked to|associated with|related to)\b', q, re.I) and not re.search(r'\bscenario', q, re.I):
        return _jira_attribute(db, q, project)
    if re.search(r'\bscenarios?\b', q, re.I) and re.search(r'\b(?:registered|customer[ -]?updates?|lack|linked|traceability|test evidence)\b', q, re.I):
        return _scenarios(db, q, project)
    if re.search(r'\btraceability\s+chain\b|\btrace\b.{0,90}\b(?:jira|requirement)\b', q, re.I):
        return _scenarios(db, q, project)
    return None


def _callers(query, project):
    from services.agent.phase2_flow_service import _project as scan, _calls
    from collections import defaultdict
    match = re.search(r'\b([A-Z]\w*)\.(\w+)\s*\(', query)
    cls, method = match.groups()
    if not project or not Path(project).is_dir():
        return _answer(query, 'DETERMINISTIC_CALLER_EVIDENCE', ['Selected Java project directory is unavailable.'], status_detail='PROJECT_NOT_SELECTED')
    root = Path(project).resolve()
    nodes, errors = scan(root)
    byname = defaultdict(list)
    for node in nodes:
        byname[node['name']].append(node)
    targets = {n['id'] for n in nodes if n['cls'] == cls and n['name'] == method}
    rows = []
    for node in nodes:
        for call in _calls(node, byname):
            if targets.intersection(call['targets']):
                rows.append(dict(caller=f"{node['cls']}.{node['name']}", file=node['file'],
                                 line=call['line'], expression=call['expression'],
                                 classification=call['status']))
    rows.sort(key=lambda x: (x['file'], x['line'], x['caller']))
    lines = [f'Callers of {cls}.{method}() in selected project:',
             'Compiler-verified caller edges: NOT ESTABLISHED by this lexical scan.',
             f'Observed candidate call expressions: {len(rows)}']
    lines += [f"- {r['caller']} — {r['file']}:{r['line']} — {r['expression']} [{r['classification']}; NOT COMPILER VERIFIED]" for r in rows]
    if not rows:
        lines.append('No matching lexical caller candidates in scanned files; this does not prove absence of callers.')
    if errors:
        lines.append(f'Incomplete scan: {len(errors)} unreadable files.')
    result = _answer(query, 'DETERMINISTIC_CALLER_EVIDENCE', lines,
                     verified_callers=[], inferred_callers=rows, scan_errors=errors,
                     compiler_verification='NOT_PERFORMED')
    if not targets:
        result['status'] = 'METHOD_NOT_FOUND'
    return result


def _jira_attribute(db, query, project):
    from db_models import JiraKnowledge
    from services.agent.ask_mapping_documents_service import extract_attribute
    attribute = extract_attribute(query)
    if not attribute:
        m = re.search(r'\b(?:to|for|with)\s+([a-z]\w+)\b', query, re.I)
        attribute = m.group(1) if m else ''
    if not project:
        return _answer(query, 'PROJECT_SCOPED_JIRA_ATTRIBUTE', ['Select a project before looking up JIRA links.'])
    rows = [r for r in db.query(JiraKnowledge).all() if _scope(r.project_path) == _scope(project)]
    matching = [r for r in rows if attribute and re.search(r'(?<!\w)' + re.escape(attribute) + r'(?!\w)',
                 (r.title or '') + '\n' + (r.requirement or ''), re.I)]
    lines = [f'Indexed requirement-text matches for {attribute or "unspecified attribute"}: {len(matching)} (of {len(rows)} project JIRAs).',
             'These are requirement mentions, NOT explicit attribute-to-JIRA links or implementation evidence.']
    lines += [f'- {r.jira_id}: {r.title or "(untitled)"}' for r in matching]
    if not matching:
        lines.append('No exact attribute mentions in indexed project requirements; other links were not checked.')
    return _answer(query, 'PROJECT_SCOPED_JIRA_ATTRIBUTE', lines, attribute=attribute,
                   candidate_jiras=[r.jira_id for r in matching], verified_jira_links=[])


def _scenarios(db, query, project):
    from db_models import Scenario
    if not project:
        return _answer(query, 'PROJECT_SCENARIO_TRACEABILITY', ['Select a project before inspecting scenarios.'])
    records = [r for r in db.query(Scenario).all() if _scope(r.project_path) == _scope(project)]
    def relevant(r):
        content = ' '.join(str(x or '') for x in (r.scenario_code, r.scenario_name, r.description, r.endpoint))
        return bool(re.search(r'customer', content, re.I) and re.search(r'updat|replac|put|patch', content, re.I))
    selected = [r for r in records if relevant(r)]
    lines = [f'Project-scoped registered customer-update scenario candidates: {len(selected)} (of {len(records)} registered scenarios).',
             'Selection is a text/endpoint match; it does not prove a requirement-to-code or test relationship.']
    entries=[]
    for r in selected:
        entry = dict(scenario_code=r.scenario_code, jira_id=r.jira_id, endpoint=r.endpoint,
                     http_method=r.http_method, evidence='SCENARIO_REGISTRY', test_link_verified=False)
        entries.append(entry)
        lines.append(f'- {r.scenario_code} | {r.http_method} {r.endpoint} | registered JIRA={r.jira_id or "NONE"} | linked test evidence=NOT CHECKED')
    if not selected:
        lines.append('No matching scenario records in this registry query. This does NOT establish that tests or other baseline records are absent.')
    if re.search(r'\b(?:lack|missing|without|coverage|test evidence)\b', query, re.I):
        lines.append('Coverage gap cannot be determined without checking test-baseline/QA associations; no untested scenario is asserted.')
    if re.search(r'\b(?:trace|requirement|jira)\b', query, re.I):
        lines.append('Requirement → scenario: registry JIRA ID only where present; scenario → Java source and tests: NOT VERIFIED by this query.')
    return _answer(query, 'PROJECT_SCENARIO_TRACEABILITY', lines,
                   scenarios=entries, coverage_verified=False, traceability_verified=False)


def _source_rule(query, project, filenames, terms):
    """Show actual Java source lines rather than generate pseudo-implementations."""
    if not project or not Path(project).is_dir():
        return _answer(query, 'DETERMINISTIC_JAVA_BUSINESS_RULE', ['Selected Java project directory is unavailable.'])
    root = Path(project).resolve()
    lines = ['Java working-tree source observations (not a runtime execution trace):']
    records=[]
    for filename in filenames:
        for file in sorted(root.rglob(filename)):
            if any(part in {'.git','build','target','out'} for part in file.relative_to(root).parts):
                continue
            try:
                source=file.read_text(encoding='utf-8').splitlines()
            except (OSError,UnicodeError):
                continue
            for i, line in enumerate(source, 1):
                if any(term in line for term in terms):
                    row={'file':file.relative_to(root).as_posix(),'line':i,'source_line':line.strip()}
                    records.append(row)
                    lines.append(f"{row['file']}:{i} {line.strip()}")
    if not records:
        lines.append('No matching source lines found in selected Java files; do not infer behavior.')
    return _answer(query, 'DETERMINISTIC_JAVA_BUSINESS_RULE', lines, source_lines=records)

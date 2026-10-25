"""Project-scoped mapping contract checks and conservative Java caller candidates.

Lexical Java inspection is NOT a type-resolved call graph. Each relationship is
labelled with its evidence strength and no execution/test success is asserted.
"""
from __future__ import annotations

from pathlib import Path
import re


def _methods(source: str):
    pattern = re.compile(r'(?m)^\s*(?:(?:public|private|protected|static|final|synchronized|default)\s+)*(?:[\w<>\[\],.?]+\s+)+(?P<name>[A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w.,\s]+)?\{')
    for match in pattern.finditer(source):
        begin = match.end() - 1
        depth = 0
        for pos in range(begin, len(source)):
            if source[pos] == '{':
                depth += 1
            elif source[pos] == '}':
                depth -= 1
                if depth == 0:
                    yield match.group('name'), source[begin:pos+1], source.count('\n', 0, match.start())+1
                    break


def _norm(path):
    return '/' + str(path or '').strip().strip('/').replace('\\', '/')


def inspect_mapping_contract(root: Path, attribute: str, mapping_rows: list, db) -> dict:
    result = {'mapping_contract_checks': [], 'reverse_call_paths': [], 'scenario_candidates': [], 'contract_warnings': []}
    src = root / 'src' / 'main' / 'java'
    if not src.is_dir():
        result['contract_warnings'].append('Java source tree not found; contract checks unavailable.')
        return result
    files = {}
    methods = []
    for file in sorted(src.rglob('*.java')):
        try:
            content = file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        files[file.stem] = (file, content)
        for name, body, line in _methods(content):
            methods.append({'class': file.stem, 'name': name, 'body': body, 'line': line, 'file': file})

    # Compare only an explicit setter assignment and an explicit trim contract.
    # Other mapping rules remain unverified rather than guessed.
    setter = re.compile(r'\bset' + re.escape(attribute[0].upper()+attribute[1:]) + r'\s*\(\s*(?P<expr>(?:\([^()]*\)|[^;])+?)\s*\)\s*;', re.I)
    relevant = []
    for row in mapping_rows:
        target = str(row.get('target_attribute') or '')
        if target.casefold() == attribute.casefold() or target.casefold().endswith('.'+attribute.casefold()):
            relevant.append(row)
    expected_trim = [r for r in relevant if re.search(r'\btrim\b', str(r.get('mapping_rule') or '') + ' ' + str(r.get('validation_rule') or ''), re.I)]
    for method in methods:
        if not expected_trim:
            break
        for found in setter.finditer(method['body']):
            expression = found.group('expr').strip()
            # Only explicit trim invocation is evidence of local trim; validation
            # elsewhere in the call chain is a separate question.
            is_trim = bool(re.search(r'\btrim\s*\(', expression))
            status = 'LOCAL_TRIM_PRESENT' if is_trim else 'POTENTIAL_MISMATCH_NO_LOCAL_TRIM'
            result['mapping_contract_checks'].append({
                'status': status, 'expected': 'trim', 'actual': expression,
                'method': method['class']+'.'+method['name'],
                'file': str(method['file'].relative_to(root)).replace('\\','/'), 'line': method['line'],
                'evidence_type': 'SOURCE_SETTER_EXPRESSION_VS_IMPORTED_MAPPING_RULE',
                'mapping_provenance': [r.get('provenance') for r in expected_trim]})
    # Find reverse call-site candidates using a Java invocation and an
    # in-file type mention. This is NOT type-resolved and must not be labelled verified.
    seeds = [x for x in methods if any(x['class']+'.'+x['name'] == c['method'] for c in result['mapping_contract_checks'])]
    if not seeds:
        result['contract_warnings'].append('No direct Java setter assignment was matched to a trim mapping contract.')
    paths = []
    seen = set()
    for seed in seeds[:20]:
        for caller in methods:
            if caller is seed or caller['class'] == seed['class']:
                continue
            if not re.search(r'\b'+re.escape(seed['name'])+r'\s*\(', caller['body']):
                continue
            caller_file_text = files[caller['class']][1]
            if not re.search(r'\b'+re.escape(seed['class'])+r'\b', caller_file_text):
                continue
            key = (caller['class'], caller['name'], seed['class'], seed['name'])
            if key in seen: continue
            seen.add(key)
            paths.append({'chain':[caller['class']+'.'+caller['name'], seed['class']+'.'+seed['name']],
                          'evidence_type':'LEXICAL_CALL_SITE_WITH_CLASS_REFERENCE',
                          'file':str(caller['file'].relative_to(root)).replace('\\','/'), 'line':caller['line']})
    # Extend one hop when a controller source refers to the service type and
    # invokes the service method. Do not infer a call path from class names alone.
    try:
        from services.flow.endpoint_flow_service import EndpointFlowService
        from config import settings
        configured = Path(str(getattr(settings, 'JAVA_PROJECT_PATH', '') or '')).resolve()
        endpoints = EndpointFlowService().discover_endpoints() if configured == root else []
    except Exception as exc:
        endpoints = []
        result['contract_warnings'].append('Endpoint discovery unavailable: '+type(exc).__name__)
    for ep in endpoints:
        controller = ep.get('class_name')
        name = ep.get('method_name')
        controller_method = next((m for m in methods if m['class'] == controller and m['name'] == name), None)
        if not controller_method:
            continue
        for path in paths:
            service_class, service_method = path['chain'][0].split('.', 1)
            # Never manufacture a self-hop or repeat a node in a candidate chain.
            chain = [controller+'.'+name] + path['chain']
            if len(chain) != len(set(chain)):
                continue
            if re.search(r'\b'+re.escape(service_method)+r'\s*\(', controller_method['body']) and re.search(r'\b'+re.escape(service_class)+r'\b', files[controller][1]):
                result['reverse_call_paths'].append({**path, 'chain':chain,
                    'http_method':ep.get('http_method'), 'endpoint':ep.get('endpoint'),
                    'evidence_type':'LEXICAL_MULTI_HOP_CALL_CANDIDATE'})
    for path in paths:
        if not any(p['chain'][-2:] == path['chain'] for p in result['reverse_call_paths']):
            result['reverse_call_paths'].append(path)
    result['reverse_call_paths'] = result['reverse_call_paths'][:60]
    # Scenario association requires BOTH a matching endpoint and project path;
    # it remains an endpoint-match candidate, not verified test coverage.
    if any(x.get('endpoint') for x in result['reverse_call_paths']):
        try:
            from db_models import Scenario
            project_key = str(root).replace('\\','/').casefold().rstrip('/')
            candidates = db.query(Scenario).all()
            endpoints_found = {(str(x.get('http_method') or '').upper(), _norm(x.get('endpoint')).casefold())
                               for x in result['reverse_call_paths'] if x.get('endpoint')}
            for scenario in candidates:
                scoped = str(scenario.project_path or '').replace('\\','/').casefold().rstrip('/')
                if scoped != project_key: continue
                key = (str(scenario.http_method or '').upper(), _norm(scenario.endpoint).casefold())
                if key in endpoints_found:
                    result['scenario_candidates'].append({'scenario_code':scenario.scenario_code,
                        'http_method':scenario.http_method, 'endpoint':scenario.endpoint,
                        'evidence_type':'PROJECT_AND_ENDPOINT_MATCH_NOT_TEST_EXECUTION'})
        except Exception as exc:
            result['contract_warnings'].append('Scenario lookup unavailable: '+type(exc).__name__)
    result['contract_warnings'].append('Reverse caller paths are lexical candidates, not type-resolved AST/CFG verified paths. No test execution is asserted.')
    return result

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


def _trim_evidence(expression: str, owner_class: str, methods: list[dict]) -> tuple[str, str]:
    """Conservative same-class, one-hop helper inspection (not interprocedural AST proof)."""
    if re.search(r"\.trim\s*\(", expression):
        return "LOCAL_TRIM_PRESENT", "direct .trim() in setter expression"
    call = re.fullmatch(r"(?:this\.)?([A-Za-z_$][\w$]*)\s*\(.*\)", expression, re.S)
    if call:
        helper = call.group(1)
        matching = [m for m in methods if m['class'] == owner_class and m['name'] == helper]
        if len(matching) == 1 and re.search(r"\.trim\s*\(", matching[0]['body']):
            return "HELPER_TRIM_PRESENT_CANDIDATE", "same-class helper %s contains trim; argument flow not verified" % helper
    return "POTENTIAL_MISMATCH_NO_LOCAL_TRIM", "no direct trim or uniquely resolved same-class trim helper"


def _receiver_call_candidate(caller: dict, target: dict, source: str) -> bool:
    """Require a typed receiver, not merely a matching method name in a file.

    Conservative lexical guard, NOT type-resolved AST analysis. Ambiguous calls
    are omitted instead of being presented as proven dependencies.
    """
    method = re.escape(target['name'])
    clazz = re.escape(target['class'])
    # Explicit constructor receiver: new Target(...).method(...)
    if re.search(r'\bnew\s+' + clazz + r'\s*\([^;{}]*?\)\s*\.\s*' + method + r'\s*\(', caller['body']):
        return True
    # A declared receiver of the target class, including fields, parameters and locals.
    declarations = re.finditer(r'\b' + clazz + r'\s+([A-Za-z_$][\w$]*)\b', source)
    for declaration in declarations:
        receiver = re.escape(declaration.group(1))
        if re.search(r'\b' + receiver + r'\s*\.\s*' + method + r'\s*\(', caller['body']):
            return True
    # Static class invocation, still a candidate until Java symbol resolution.
    return bool(re.search(r'\b' + clazz + r'\s*\.\s*' + method + r'\s*\(', caller['body']))


def _deduplicate_edges(edges: list[dict]) -> list[dict]:
    """Collapse identical edges, preserving distinct source locations."""
    unique = []
    seen = set()
    for edge in edges:
        key = (edge.get('from'), edge.get('to'), edge.get('status'), edge.get('file'), edge.get('line'))
        if key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique


def inspect_mapping_contract(root: Path, attribute: str, mapping_rows: list, db) -> dict:
    result = {'mapping_contract_checks': [], 'reverse_call_paths': [], 'scenario_candidates': [], 'contract_warnings': [], 'field_evidence': [], 'field_flow_evidence': [], 'impact_graph': {'nodes': [], 'edges': []}, 'regression_recommendations': [], 'risk_summary': {}}
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

    # Attribute references are source observations, not proof of runtime dataflow.
    # Keep evidence separate from type-resolved or executed claims.
    getter = re.compile(r'\b(?:get|set|is)' + re.escape(attribute[0].upper() + attribute[1:]) + r'\s*\(', re.I)
    field_token = re.compile(r'(?<![\w$])' + re.escape(attribute) + r'(?![\w$])', re.I)
    for method in methods:
        matched = getter.search(method['body']) or field_token.search(method['body'])
        if matched:
            result['field_evidence'].append({
                'method': method['class'] + '.' + method['name'],
                'file': str(method['file'].relative_to(root)).replace('\\', '/'),
                'line': method['line'] + method['body'][:matched.start()].count('\n'),
                'kind': 'ACCESSOR_CALL' if getter.search(method['body']) else 'FIELD_TOKEN',
                'status': 'SOURCE_OBSERVED_NOT_DATAFLOW_VERIFIED'})
    result['field_evidence'] = result['field_evidence'][:120]
    from services.agent.java_field_flow_evidence import inspect_method_field
    for method in methods:
        result['field_flow_evidence'].extend(inspect_method_field(method, attribute, root))
    result['field_flow_evidence'] = result['field_flow_evidence'][:160]
    # JDK AST evidence is additive: existing lexical paths remain candidates.
    # Only a javac-resolved method symbol is considered symbol-resolved.
    from services.agent.java_ast_evidence import inspect_java_ast
    result['java_ast_evidence'] = inspect_java_ast(root, attribute)


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
            status, reason = _trim_evidence(expression, method['class'], methods)
            result['mapping_contract_checks'].append({
                'status': status, 'expected': 'trim', 'actual': expression, 'reason': reason,
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
            caller_file_text = files[caller['class']][1]
            if not _receiver_call_candidate(caller, seed, caller_file_text):
                continue
            key = (caller['class'], caller['name'], seed['class'], seed['name'])
            if key in seen: continue
            seen.add(key)
            paths.append({'chain':[caller['class']+'.'+caller['name'], seed['class']+'.'+seed['name']],
                          'evidence_type':'LEXICAL_TYPED_RECEIVER_CANDIDATE',
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
            target_method = next((m for m in methods if m['class'] == service_class and m['name'] == service_method), None)
            if target_method and _receiver_call_candidate(controller_method, target_method, files[controller][1]):
                result['reverse_call_paths'].append({**path, 'chain':chain,
                    'http_method':ep.get('http_method'), 'endpoint':ep.get('endpoint'),
                    'evidence_type':'LEXICAL_TYPED_RECEIVER_MULTI_HOP_CANDIDATE'})
    # Field-sensitive conservative guard for explicitly field-specific entry points.
    # A controller/service method named updateOtherField is not evidence of
    # target-field impact just because it calls a shared mapper that reads target.
    # Keep broad create/replace paths as candidates until AST dataflow is available.
    def _conflicting_field_update(chain: list[str]) -> bool:
        for qualified in chain[:-1]:
            method_name = qualified.rsplit('.', 1)[-1]
            match = re.fullmatch(r'(?:update|patch|set)([A-Z][A-Za-z0-9_]*)', method_name)
            if match:
                field_name = match.group(1)
                field_name = field_name[0].lower() + field_name[1:]
                if field_name.casefold() != attribute.casefold():
                    return True
        return False

    omitted = [p for p in result['reverse_call_paths'] if _conflicting_field_update(p.get('chain') or [])]
    if omitted:
        result['contract_warnings'].append(
            f'Excluded {len(omitted)} field-specific candidate call paths targeting other attributes; '
            'this is a lexical exclusion, not a verified absence of side effects.')
    result['reverse_call_paths'] = [p for p in result['reverse_call_paths']
                                    if not _conflicting_field_update(p.get('chain') or [])]
    # Preserve an orphan mapper call only if its caller itself has source evidence
    # for this field. This avoids reintroducing unrelated updateAccountType hops
    # after a controller path was correctly excluded.
    field_methods = {item['method'] for item in result['field_evidence']}
    for path in paths:
        if path['chain'][0] not in field_methods:
            continue
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
    # Build a bounded, provenance-bearing graph for the IDE. Graph edges are
    # explicitly candidates; they must not be mistaken for resolved Java calls.
    node_map = {}
    edges = []
    def node(key, kind, label, status):
        if key not in node_map:
            node_map[key] = {'id': key, 'kind': kind, 'label': label, 'status': status}
    attribute_id = 'attribute:' + attribute
    node(attribute_id, 'attribute', attribute, 'MAPPING_SOURCE')
    observed = {f['method'] for f in result['field_evidence']}
    for item in result['field_evidence']:
        mid = 'method:' + item['method']
        node(mid, 'method', item['method'], 'SOURCE_OBSERVED')
        edges.append({'from': attribute_id, 'to': mid, 'status': 'SOURCE_REFERENCE_ONLY', 'line': item['line'], 'file': item['file']})
    for path in result['reverse_call_paths']:
        chain = path.get('chain') or []
        for name in chain:
            node('method:' + name, 'method', name, 'CALL_CANDIDATE')
        for a, b in zip(chain, chain[1:]):
            edges.append({'from': 'method:' + a, 'to': 'method:' + b, 'status': 'LEXICAL_NOT_TYPE_RESOLVED'})
        if path.get('endpoint'):
            endpoint_label = str(path.get('http_method') or '') + ' ' + str(path['endpoint'])
            endpoint_id = 'api:' + endpoint_label
            node(endpoint_id, 'api', endpoint_label, 'NOT_VERIFIED')
            if chain:
                edges.append({'from': endpoint_id, 'to': 'method:' + chain[0], 'status': 'ENDPOINT_SOURCE_CANDIDATE'})
    for scenario in result['scenario_candidates']:
        sid = 'scenario:' + str(scenario['scenario_code'])
        node(sid, 'scenario', str(scenario['scenario_code']), 'NOT_EXECUTED')
        endpoint_id = 'api:' + str(scenario.get('http_method') or '') + ' ' + str(scenario.get('endpoint') or '')
        if endpoint_id in node_map:
            edges.append({'from': endpoint_id, 'to': sid, 'status': 'ENDPOINT_MATCH_ONLY'})
        result['regression_recommendations'].append({'scenario_code': scenario['scenario_code'],
            'reason': 'Candidate API endpoint matches a project-scoped scenario; confirm field flow before prioritizing.',
            'execution_status': 'NOT_EXECUTED', 'recommendation': 'REVIEW_AND_RUN_IF_CONFIRMED'})
    result['impact_graph'] = {'nodes': list(node_map.values())[:150], 'edges': _deduplicate_edges(edges)[:300],
                              'verification': 'CANDIDATE_GRAPH_NOT_TYPE_RESOLVED'}
    result['risk_summary'] = {'classification': 'INSUFFICIENT_VERIFIED_EVIDENCE',
        'candidate_api_count': len({(p.get('http_method'), p.get('endpoint')) for p in result['reverse_call_paths'] if p.get('endpoint')}),
        'scenario_candidate_count': len(result['scenario_candidates']),
        'observed_method_count': len(observed), 'verified_dataflow_paths': 0,
        'note': 'Candidate volume is not a numerical risk score. Validate Java dataflow and execution before release sign-off.'}
    result['contract_warnings'].append('Field-flow and conditional observations are lexical, not Java AST/CFG verified. Use a symbol resolver for definitive impact.')
    result['contract_warnings'].append('Reverse caller paths require a lexical typed receiver but are not type-resolved AST/CFG verified paths. No test execution is asserted.')
    return result

"""Project-bounded, source-backed attribute impact for IntelliJ Ask.

Code references are lexical candidates; regression scenarios are reported only
when the selected Git comparison actually changed the requested attribute.
"""
from pathlib import Path
import re


def project_attribute_impact(db, query: str, attribute: str, project_path: str | None = None) -> dict:
    from config import settings
    from services.agent.ask_mapping_documents_service import mapping_documents_answer

    root = Path(project_path or getattr(settings, 'JAVA_PROJECT_PATH', '') or '').resolve()
    result = {'query': query, 'attribute': attribute, 'source': 'PROJECT_SCOPED_ATTRIBUTE_IMPACT',
              'project_path': str(root), 'status': 'PARTIAL_MATCH', 'execution_verified': False,
              'affected_methods': [], 'affected_scenarios': [], 'mapping_rows': [],
              'mapping_documents': [], 'evidence_warnings': []}
    if not root.is_dir() or not (root / 'src').is_dir():
        result['status'] = 'PROJECT_UNAVAILABLE'
        result['answer'] = 'The selected Java project source directory is unavailable. No cross-project fallback was used.'
        return result

    mapping = mapping_documents_answer(db, f'mapping documents for {attribute}')
    # Mapping search uses the configured active project. If an explicitly selected
    # different workspace is passed, do not mix its code with global mapping rows.
    configured = Path(getattr(settings, 'JAVA_PROJECT_PATH', '') or '').resolve()
    if configured == root:
        result['mapping_rows'] = mapping.get('mapping_rows') or []
        result['mapping_documents'] = mapping.get('mapping_documents') or []
    else:
        result['evidence_warnings'].append('Mapping evidence omitted: selected project differs from configured mapping project.')

    token = re.compile(r'(?<![\w])' + re.escape(attribute) + r'(?![\w])', re.I)
    # Extract lexical Java method declarations only, and only report a method
    # if the attribute appears inside its bounded method body.
    declaration = re.compile(r'(?m)^\s*(?:(?:public|private|protected|static|final|synchronized|abstract|default)\s+)*'
                             r'(?:[\w<>\[\],.?]+\s+)+(?P<method>[A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w.,\s]+)?\{')
    src = root / 'src' / 'main' / 'java'
    for file in sorted(src.rglob('*.java')) if src.is_dir() else []:
        if len(result['affected_methods']) >= 120:
            result['evidence_warnings'].append('Source matches truncated to 120 methods.')
            break
        try:
            code = file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        matches = list(declaration.finditer(code))
        for index, match in enumerate(matches):
            start = match.end() - 1
            depth, end = 0, len(code)
            # Brace-balanced lexical range; Java comments and strings may still
            # confuse this scanner, so never label this AST-verified.
            for pos in range(start, len(code)):
                if code[pos] == '{': depth += 1
                elif code[pos] == '}':
                    depth -= 1
                    if depth == 0:
                        end = pos + 1
                        break
            body = code[start:end]
            hits = list(token.finditer(body))
            if not hits:
                continue
            line = code.count('\n', 0, start) + 1
            result['affected_methods'].append({
                'class_name': file.stem, 'method_name': match.group('method'),
                'file': str(file.relative_to(root)).replace('\\', '/'),
                'line': line, 'attribute_mentions': len(hits),
                'evidence_type': 'PROJECT_SOURCE_LEXICAL_REFERENCE',
                'relationship': 'CANDIDATE_NOT_VERIFIED_CALL_PATH'})

    # Mapping-contract checks and bounded reverse caller discovery use only the
    # selected workspace's current Java source. No global RAG/LLM fallback.
    from services.agent.mapping_contract_flow_service import inspect_mapping_contract
    contract = inspect_mapping_contract(root, attribute, result['mapping_rows'], db)
    result.update(contract)
    # An endpoint in the question is a constraint to investigate, not an
    # attribute synonym and not evidence that this field reaches the endpoint.
    requested_endpoint = re.search(r'\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s,;]+)', query, re.I)
    if requested_endpoint:
        http_method, endpoint = requested_endpoint.group(1).upper(), requested_endpoint.group(2).rstrip('?.!')
        result['requested_endpoint'] = {'http_method': http_method, 'endpoint': endpoint}
        matching_paths = [p for p in result.get('reverse_call_paths', [])
                          if str(p.get('http_method') or '').upper() == http_method
                          and str(p.get('endpoint') or '').rstrip('/') == endpoint.rstrip('/')]
        result['requested_endpoint_evidence'] = 'LEXICAL_CANDIDATE_ONLY' if matching_paths else 'NOT_VERIFIED'
        result['requested_endpoint_paths'] = matching_paths
        result['evidence_warnings'].append(
            'Requested endpoint is a query constraint; lexical caller paths cannot prove field-level impact.')

    # A Git-based regression result describes only the comparison, never an
    # unconditional impact from a hypothetical mapping edit.
    if re.search(r'\b(?:last commit|latest commit|git|commit|recent code change)\b', query, re.I):
        try:
            from services.regression.regression_impact_service import RegressionImpactService
            comparison = RegressionImpactService().analyse(db, mode='last_commit')
            changed = comparison.get('changed_attributes') or []
            if any(str(x).casefold() == attribute.casefold() for x in changed):
                result['affected_scenarios'] = comparison.get('affected_scenarios') or []
                result['scenario_evidence_type'] = 'LAST_COMMIT_REGRESSION_ANALYSIS'
            else:
                result['evidence_warnings'].append('Last commit does not identify this attribute as changed.')
        except Exception as exc:
            result['evidence_warnings'].append(f'Git regression comparison unavailable: {type(exc).__name__}')

    lines = [f'Project-scoped attribute impact: {attribute}', f'Workspace: {root}',
             f'Mapping rows: {len(result["mapping_rows"])}',
             f'Java method candidates: {len(result["affected_methods"])}']
    for item in result['affected_methods'][:30]:
        lines.append(f'- SOURCE CANDIDATE {item["class_name"]}.{item["method_name"]} ({item["file"]}:{item["line"]})')
    if result.get('requested_endpoint'):
        endpoint = result['requested_endpoint']
        lines.append(f"Requested endpoint: {endpoint['http_method']} {endpoint['endpoint']} | field-level impact: NOT_VERIFIED (lexical matches are candidates only)")
    lines.append('Mapping contract checks: ' + str(len(result.get('mapping_contract_checks') or [])))
    for check in result.get('mapping_contract_checks') or []:
        lines.append(f"- {check['status']} {check['method']} | expected={check['expected']} | actual={check['actual']} | {check['file']}:{check['line']}")
    lines.append('Reverse caller evidence: ' + str(len(result.get('reverse_call_paths') or [])))
    for path in result.get('reverse_call_paths') or []:
        lines.append(f"- {path['evidence_type']} " + ' -> '.join(path['chain']) + (f" | {path['http_method']} {path['endpoint']}" if path.get('endpoint') else ''))
    lines.append('Endpoint-matched scenario candidates: ' + str(len(result.get('scenario_candidates') or [])))
    for sc in result.get('scenario_candidates') or []:
        lines.append(f"- {sc['scenario_code']} | {sc['http_method']} {sc['endpoint']} | {sc['evidence_type']}")
    lines.extend(result.get('contract_warnings') or [])
    lines.append(f'Git-comparison affected scenarios: {len(result["affected_scenarios"])}')
    for item in result['affected_scenarios'][:20]:
        lines.append(f'- {item.get("http_method")} {item.get("endpoint")} | {item.get("scenario_code")} | {item.get("impact_status")}')
    lines.append('Source mentions are candidates, not verified call paths. A hypothetical mapping change cannot be treated as an actual Git diff.')
    lines.append('No cross-project methods or unverified scenario associations were added.')
    result['answer'] = '\n'.join(lines)
    result['status'] = 'FOUND' if result['mapping_rows'] or result['affected_methods'] or result['affected_scenarios'] else 'NOT_FOUND'
    return result

"""On-demand, source-backed Java method call evidence for the selected project.

Unresolved Java dependencies are reported as candidates, NEVER verified edges.
No repository-wide indexing or graph database writes are performed.
"""
from __future__ import annotations
import re
from pathlib import Path
from services.agent.java_ast_evidence import inspect_java_ast

SYMBOL = re.compile(r"\b([A-Z][A-Za-z0-9_$]*)\.([a-zA-Z_$][A-Za-z0-9_$]*)\s*\(")
INTENT = re.compile(r"\b(?:call\s*graph|call\s*flow|execution\s*flow|callers?\s+and\s+callees?|trace\s+(?:the\s+)?(?:complete\s+)?call\s+flow)\b", re.I)

def is_method_graph_question(question: str) -> bool:
    return bool(INTENT.search(question or '') and SYMBOL.search(question or ''))

def retrieve_method_graph(question: str, project_path: str | None) -> dict:
    symbol = SYMBOL.search(question or '')
    if not symbol:
        return {'status': 'NOT_RESOLVED', 'answer': 'Specify ClassName.methodName() to inspect its call graph.'}
    cls, method = symbol.groups()
    root = Path(project_path).resolve() if project_path else None
    if root is None or not root.is_dir():
        return {'status': 'PROJECT_NOT_SELECTED', 'answer': 'Select an existing project before requesting a method call graph.'}
    source_root = root / 'src' / 'main' / 'java'
    if not source_root.is_dir():
        return {'status': 'SOURCE_UNAVAILABLE', 'answer': 'Selected project has no src/main/java directory.'}
    matches = sorted(source_root.rglob(cls + '.java'))
    if not matches:
        return {'status': 'CLASS_NOT_FOUND', 'answer': f'{cls}.java was not found in the selected project.'}
    if len(matches) > 1:
        return {'status': 'AMBIGUOUS_CLASS', 'answer': 'Multiple classes match; specify the fully qualified class name.',
                'candidate_files': [str(p.relative_to(root)) for p in matches[:20]]}
    file = matches[0]
    evidence = inspect_java_ast(root, '', timeout=40, source_file=file)
    calls = [c for c in evidence.get('calls', []) if c.get('method') == f'{cls}.{method}']
    # Only the inspected class is searched. Cross-class callers require a separate
    # indexed graph traversal and must not be reported as absent.
    verified, candidates = [], []
    for call in calls:
        row = {'expression': call.get('expression'), 'target': call.get('target') or None,
               'file': call.get('file'), 'line': call.get('line'), 'status': call.get('status')}
        (verified if call.get('status') == 'SYMBOL_RESOLVED' and call.get('target') else candidates).append(row)
    lines = [f'Source-backed method call evidence: {cls}.{method}()',
             f'Source: {file.relative_to(root).as_posix()}',
             f'AST inspection: {evidence.get("status")}',
             f'Verified symbol-resolved calls: {len(verified)}; unresolved call candidates: {len(candidates)}']
    for item in verified:
        lines.append(f'VERIFIED {item["file"]}:{item["line"]} — {item["expression"]} → {item["target"]}')
    for item in candidates:
        lines.append(f'UNRESOLVED {item["file"]}:{item["line"]} — {item["expression"]} (not a verified graph edge)')
    lines.append('Callers outside this class: NOT CHECKED (requires project-wide indexed graph).')
    if evidence.get('warnings'):
        lines.append('Warnings: ' + '; '.join(evidence['warnings']))
    if not calls:
        lines.append('No matching call observations. This does not establish that the method has no calls.')
    return {'status': 'AST_INSPECTED' if evidence.get('status') == 'AST_INSPECTED' else 'AST_UNAVAILABLE',
            'source': 'JAVA_AST_METHOD_CALLS', 'project_path': str(root),
            'method': f'{cls}.{method}', 'source_file': str(file.relative_to(root)),
            'verified_callees': verified, 'unresolved_call_candidates': candidates,
            'callers_status': 'NOT_CHECKED', 'warnings': evidence.get('warnings', []),
            'answer': '\n'.join(lines)}

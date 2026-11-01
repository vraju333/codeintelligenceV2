"""Read-only, project-scoped graph retrieval independent of ASK answer phrasing.

LLM identifies an entity; source symbols validate it. Graph edges remain evidence-labelled.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from config import settings


def _symbols(root: Path):
    """Conservative source symbol inventory; no cross-project global lookup."""
    attrs, methods = set(), set()
    source = root / 'src'
    for path in list(source.rglob('*.java'))[:1500] if source.is_dir() else []:
        try:
            body = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        # This inventory is a lexical candidate, NOT a verified Java symbol table.
        for name in re.findall(r'\b(?:private|public|protected)\s+(?:(?:static|final|volatile|transient)\s+)*(?:[\w<>?,\[\].]+)\s+([A-Za-z_$][\w$]*)\s*(?:=|;)', body):
            attrs.add(name)
        for name in re.findall(r'\b(?:public|private|protected)\s+(?:(?:static|final|synchronized)\s+)*(?:[\w<>?,\[\].]+)\s+([A-Za-z_$][\w$]*)\s*\(', body):
            methods.add(f'{path.stem}.{name}')
    return attrs, methods


def _llm_choice(question: str, attrs: set[str], methods: set[str]):
    """LLM is an intent/entity resolver only; it cannot generate graph evidence."""
    try:
        from services.agent.knowledge_agent_service import KnowledgeAgentService
        model = KnowledgeAgentService._build_model()
        instruction = ('Return ONLY JSON {"subject_type":"attribute|method|unknown","subject":"..."}. '
                       'Identify the main source-code symbol being asked about. Never invent a symbol. '
                       'Prefer a symbol from the supplied project inventory. '
                       'If none match, return unknown. Question: ' + json.dumps(question) +
                       '\nKnown attributes: ' + json.dumps(sorted(attrs)[:350]) +
                       '\nKnown methods: ' + json.dumps(sorted(methods)[:350]))
        reply = model.invoke(instruction)
        raw = str(getattr(reply, 'content', reply)).strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        data = json.loads(raw)
        return str(data.get('subject_type') or ''), str(data.get('subject') or '')
    except Exception:
        return '', ''


def retrieve(question: str, project_path: str | None, db):
    root = Path(project_path or getattr(settings, 'JAVA_PROJECT_PATH', '') or '').resolve()
    if not root.is_dir() or not (root / 'src').is_dir():
        return {'status': 'PROJECT_UNAVAILABLE', 'graph_subject': '', 'predictive_impact_graph': {'nodes': [], 'edges': []},
                'graph_warnings': ['Selected project source directory unavailable; no cross-project fallback.']}
    attrs, methods = _symbols(root)
    # Fast exact matching against project symbols; no fixed English question templates.
    words = set(re.findall(r'[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?', question))
    attribute_hits = [x for x in attrs if x in words]
    method_hits = [x for x in methods if x in words]
    kind, subject = '', ''
    if len(attribute_hits) + len(method_hits) == 1:
        kind = 'attribute' if attribute_hits else 'method'
        subject = (attribute_hits or method_hits)[0]
    else:
        kind, subject = _llm_choice(question, attrs, methods)
    # A model-proposed symbol MUST match the indexed project's source inventory.
    if kind == 'attribute':
        subject = next((x for x in attrs if x.casefold() == subject.casefold()), '')
    elif kind == 'method':
        subject = next((x for x in methods if x.casefold() == subject.casefold()), '')
    else:
        subject = ''
    if not subject:
        return {'status': 'NOT_RESOLVED', 'graph_subject': '', 'predictive_impact_graph': {'nodes': [], 'edges': []},
                'graph_warnings': ['No unambiguous project source symbol resolved. No graph relationship was invented.']}
    if kind == 'attribute':
        from routers.knowledge_agent_router import _enrich_intelligence
        # Existing AST reconciliation and project-scoped graph retrieval remain authoritative.
        evidence = _enrich_intelligence(db, question, subject, str(root))
        graph = evidence.get('predictive_impact_graph') or {'nodes': [], 'edges': []}
        if not graph.get('nodes'):
            graph = {'nodes': [{'id': 'attribute:' + subject, 'label': subject, 'kind': 'attribute'}], 'edges': []}
    else:
        # Use the same javac symbol-resolved evidence as attribute graphs.
        # Method names in source inventory are lexical; only resolved CALLs become verified edges.
        from services.agent.java_ast_evidence import inspect_java_ast
        ast = inspect_java_ast(root, '')
        nodes = {'method:' + subject: {'id': 'method:' + subject, 'label': subject, 'kind': 'method'}}
        edges = []
        for call in ast.get('calls') or []:
            caller = str(call.get('method') or '')
            target = str(call.get('target') or '')
            if not caller or not target or call.get('status') != 'SYMBOL_RESOLVED':
                continue
            # Exact simple class.method suffix match, never method-name-only linkage.
            if caller != subject and not (target == subject or target.endswith('.' + subject)):
                continue
            callee = next((m for m in methods if target == m or target.endswith('.' + m)), '')
            if not callee:
                continue
            for name in (caller, callee):
                nodes.setdefault('method:' + name, {'id': 'method:' + name, 'label': name, 'kind': 'method'})
            edges.append({'from': 'method:' + caller, 'to': 'method:' + callee,
                          'status': 'AST_CALL_VERIFIED', 'file': call.get('file') or '',
                          'line': call.get('line') or 0})
        graph = {'nodes': list(nodes.values()), 'edges': edges}
    return {'status': 'FOUND', 'graph_subject': subject, 'graph_subject_type': kind,
            'graph_project_path': str(root), 'predictive_impact_graph': graph,
            'graph_warnings': [] if graph.get('edges') else ['No verified or candidate relationships returned for this subject.']}

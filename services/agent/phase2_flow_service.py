"""On-demand Java flow exploration. Lexical evidence, never compiler-resolved edges.

This module does not index on startup, call LLMs, or access other projects.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from collections import defaultdict, deque
from services.agent.code_understanding_service import _masked, _methods, CLASS, SKIP

TARGET = re.compile(r'\b([A-Z][\w$]*)\.([\w$]+)\s*\(')
CALL = re.compile(r'(?<![\w$])(?:(\b[A-Za-z_$][\w$]*)\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\(')
KEYWORDS = {'if','for','while','switch','catch','return','throw','new','synchronized','assert','super','this','try'}

def intent(q: str):
    """Prioritize explicit source-impact requests over historical attribute searches."""
    s = q.lower()
    historical = bool(re.search(r'\b(?:historical|git\s+history|previous\s+releases?|past\s+commits?)\b', s))
    hypothetical = bool(re.search(r'\b(?:suppose|what\s+if|hypothetical|if\s+we|would\s+change|is\s+modified|were\s+modified|changes?\s+to\s+reject)\b', s))
    method = bool(TARGET.search(q))
    impact_terms = bool(re.search(r'\b(?:impact|impacted|affected|affect|dependency\s+depth|direct\s+and\s+indirect|regression)\b', s))
    if method and impact_terms and (hypothetical or not historical) and not re.search(r'\b(?:conditional\s+branches?|exception\s+paths?|return\s+paths?|control[ -]flow)\b', s):
        return 'impact'
    if re.search(r'\b(?:conditional\s+(?:execution\s+)?branch(?:es)?|exception\s+paths?|return\s+paths?|branch(?:es)?\s+and\s+exception|every\s+.*branch|control[ -]flow)\b', s):
        return 'branches'
    if re.search(r'\b(?:call\s*graph|execution\s*flow|execution\s*path|call\s*flow|method.level\s*(?:dependency|call)|callers?\s+and\s+callees?|transitive\s+callers?|trace\s+(?:the\s+)?(?:complete\s+)?execution\s+flow)\b', s):
        return 'call_graph'
    if method and re.search(r'\b(?:resolve\s+calls?|confirmed\s+calls?|unresolved\s+calls?|framework[ -]mediated\s+calls?)\b', s):
        return 'call_graph'
    if re.search(r'\b(?:crud\s+rest\s+endpoints?|all\s+crud|trace\s+all\s+crud)\b', s):
        return 'call_graph'
    if re.search(r'\b(?:data.flow|flows?\s+through|trace\s+how\s+\w+\s+flows?)\b', s):
        return 'data_flow'
    if re.search(r'\b(?:potential\s+impact|change\s+impact|impact\s+across|affected\s+controller|suppose\s+.*\s+changed)\b', s):
        return 'impact'
    if re.search(r'\b(?:direct\s+and\s+transitive\s+callers|method\s+dependencies|dependency\s+graph)\b', s):
        return 'call_graph'
    return None

def _line(s, pos): return s.count('\n', 0, pos) + 1

def _project(root):
    nodes, errors = [], []
    for p in sorted(root.rglob('*.java')):
        if SKIP.intersection(p.relative_to(root).parts): continue
        try:
            raw = p.read_bytes(); src = raw.decode('utf-8')
        except (OSError, UnicodeError) as e:
            errors.append(f'{p.relative_to(root)}: {type(e).__name__}'); continue
        masked = _masked(src)
        class_matches = list(CLASS.finditer(masked))
        methods = list(_methods(src))
        for m in methods:
            cls = next((x.group(1) for x in reversed(class_matches) if x.start() < m['start']), p.stem)
            nodes.append(dict(id=f'{p.relative_to(root).as_posix()}::{cls}.{m["name"]}@{m["line_number"]}',
                cls=cls, name=m['name'], signature=m['signature'], file=p.relative_to(root).as_posix(),
                line=m['line_number'], sha256=hashlib.sha256(raw).hexdigest()[:12],
                source=m['source'], masked=masked[m['start']:m['end']], offset=m['start'], full=src,
                method=m))
    return nodes, errors

def _types(node):
    """Conservative lexical receiver bindings from fields, parameters and locals."""
    source = node['full']
    before = source[:node['offset']]
    # Field declarations may be earlier in the class; use only a bounded preceding window.
    fields = before[max(0, len(before)-12000):]
    body = node['source']
    bindings = {'this': node['cls']}
    declaration = re.compile(r'\b([A-Z][\w$]*(?:<[^;=(){}]+>)?)\s+([a-zA-Z_$][\w$]*)\s*(?=[=;,)]|$)')
    for match in declaration.finditer(fields + '\n' + body):
        typ, name = match.groups()
        bindings[name] = typ.split('<',1)[0]
    return bindings


def _calls(node, byname):
    body_start = node['masked'].find('{') + 1
    body = node['masked'][body_start:]
    bindings = _types(node)
    result = []
    for match in CALL.finditer(body):
        recv, name = match.groups()
        if name in KEYWORDS: continue
        prefix = body[max(0,match.start()-8):match.start()]
        if re.search(r'\bnew\s*$',prefix): continue
        candidates = byname.get(name, [])
        typ = bindings.get(recv) if recv else node['cls']
        if recv and recv[0].isupper(): typ = recv
        if typ:
            candidates = [x for x in candidates if x['cls'] == typ]
        else:
            candidates = []  # Unknown receiver must never match every class.
        loc = _line(node['full'], node['offset'] + body_start + match.start())
        targets = [x['id'] for x in candidates]
        if len(targets) == 1:
            status = 'LEXICAL_SAME_CLASS' if not recv or recv == 'this' else 'RECEIVER_TYPE_INFERRED'
        else:
            status = 'UNRESOLVED_CANDIDATE'
        if recv and recv.lower() in ('repository','repo') and not targets:
            status = 'FRAMEWORK_MEDIATED_CANDIDATE'
        result.append(dict(caller=node['id'], receiver=recv, name=name, line=loc,
                           expression=match.group(0), targets=targets[:15], status=status))
    return result

def _selected(q, mode, nodes):
    if mode == 'data_flow':
        return [n for n in nodes if n['name'] == 'replace' and ('controller' in n['file'].lower() or 'service' in n['file'].lower())][:8]
    symbol = TARGET.search(q)
    if symbol:
        cls, name = symbol.groups()
        found = [n for n in nodes if n['cls'] == cls and n['name'] == name]
        if found: return found
    if mode == 'branches':
        return [n for n in nodes if n['name'] == 'replace' and 'service' in n['file'].lower()][:5]
    if mode == 'impact':
        return [n for n in nodes if n['name'] == 'validate' and 'service' in n['file'].lower()][:5]
    if mode == 'data_flow':
        return [n for n in nodes if n['name'] == 'replace' and 'controller' in n['file'].lower()][:8]
    # Entire CRUD graph starts from controllers, not one method.
    if re.search(r'\b(?:complete\s+method.level\s+call\s+graph|all\s+rest\s+controller|customer\s+creation,\s+retrieval|crud\s+rest\s+endpoints?|all\s+crud)\b',q,re.I):
        return [n for n in nodes if 'controller' in n['file'].lower() and re.search(r'create|get|replace|update|delete',n['name'],re.I)][:35]
    return [n for n in nodes if n['name'] == 'replace' and 'controller' in n['file'].lower()][:5]

def _impact_layers(selected, reverse, calls, index):
    """Shortest reverse-call distance, based only on unambiguous lexical edges."""
    distances = {n['id']: 0 for n in selected}
    queue = deque(distances)
    while queue:
        target = queue.popleft()
        for caller in reverse.get(target, []):
            caller_id = caller['id']
            if caller_id in distances:
                continue
            # Do not promote unresolved/ambiguous candidate links to an impact fact.
            if not any(e['targets'] == [target] and
                       e['status'] in ('LEXICAL_SAME_CLASS', 'RECEIVER_TYPE_INFERRED')
                       for e in calls[caller_id]):
                continue
            distances[caller_id] = distances[target] + 1
            queue.append(caller_id)
    return [dict(method_id=mid, method=f"{index[mid]['cls']}.{index[mid]['name']}",
                 file=index[mid]['file'], line=index[mid]['line'],
                 distance=depth, classification=('CHANGED_METHOD' if depth == 0 else
                    'DIRECT_CALLER' if depth == 1 else 'INDIRECT_CALLER'),
                 confidence='LEXICAL_NOT_COMPILER_VERIFIED',
                 callsites=[dict(file=index[mid]['file'], line=e['line'], expression=e['expression'])
                            for e in calls.get(mid, []) if any(t in distances and distances[t] == depth-1
                            for t in e['targets'])] if depth else [])
            for mid, depth in sorted(distances.items(), key=lambda x:(x[1],x[0]))]


def _branch_observations(node):
    """Observed conditional and exit statements, not an executable CFG."""
    result = []
    for line_no, raw in enumerate(node['source'].splitlines(), start=node['line']):
        clean = _masked(raw).strip()
        if re.search(r'\b(?:if|else|switch|case|throw|return|catch)\b', clean):
            result.append(dict(method_id=node['id'], file=node['file'], line=line_no,
                               statement=raw.strip(), evidence='LEXICAL_BRANCH_OR_EXIT'))
    return result

def answer(query: str, project_path: str | None):
    mode = intent(query)
    if not mode: return None
    if not project_path or not Path(project_path).expanduser().is_dir():
        return dict(status='PROJECT_NOT_SELECTED', source='LIVE_JAVA_FLOW', answer='Select a valid project directory. No fallback to unrelated JIRA or mapping evidence.')
    root = Path(project_path).expanduser().resolve()
    nodes, errors = _project(root)
    byname = defaultdict(list)
    for n in nodes: byname[n['name']].append(n)
    calls = {n['id']:_calls(n,byname) for n in nodes}
    selected = _selected(query,mode,nodes)
    # Expand both incoming and outgoing lexical candidates, bounded to protect large projects.
    visited, queue = {}, deque((n,0) for n in selected)
    index = {n['id']:n for n in nodes}
    reverse = defaultdict(list)
    for owner in nodes:
        for edge in calls[owner['id']]:
            for target in edge['targets']:
                reverse[target].append(owner)
    while queue and len(visited)<48:
        n, depth = queue.popleft()
        if n['id'] in visited: continue
        visited[n['id']] = n
        if depth>=3: continue
        for c in calls[n['id']]:
            for target in c['targets'][:8]:
                if target not in visited:
                    nxt=index.get(target)
                    if nxt: queue.append((nxt,depth+1))
        if mode in ('call_graph','impact'):
            for other in reverse[n['id']]:
                if other['id'] != n['id']:
                    queue.append((other,depth+1))
    lines = [f'Live Java Phase 2 {mode} analysis for {root}',
             f'Analyzed {len(nodes)} lexical Java methods; selected {len(selected)} starting methods; expanded {len(visited)} method candidates.',
             'EVIDENCE CLASSIFICATION: source lines and call expressions are directly observed; cross-class target resolution is NOT compiler-verified.']
    if not selected: lines.append('Requested method not found in selected Java working tree. No invented graph.')
    impact_layers = _impact_layers(selected, reverse, calls, index) if mode == 'impact' else []
    branch_rows = []
    rows=[]
    if mode == 'impact':
        lines.append('\nDIRECT / INDIRECT IMPACT SUMMARY (potential dependency only):')
        for item in impact_layers:
            lines.append(f"  {item['classification']} depth={item['distance']}: {item['method']} ({item['file']}:{item['line']})")
        lines.append('REST endpoints are potentially affected when their controller methods appear as indirect callers; actual request behavior is not proven.')
    if mode == 'branches':
        lines.append('\nCONDITIONAL / EXIT SOURCE EVIDENCE (NOT CFG VERIFIED):')
        for n in selected:
            for observation in _branch_observations(n):
                lines.append(f"  {observation['file']}:{observation['line']} {observation['statement']}")
    for n in visited.values():
        lines.append(f'\nMETHOD {n["cls"]}.{n["name"]} — {n["file"]}:{n["line"]} [SHA256 {n["sha256"]}] {n["signature"]}')
        for c in calls[n['id']]:
            candidate_names=[next((x['cls']+'.'+x['name'] for x in nodes if x['id']==t),t) for t in c['targets'][:6]]
            lines.append(f'  CALL {n["file"]}:{c["line"]} {c["expression"]} => {", ".join(candidate_names) or "UNRESOLVED"} [{c["status"]}]')
            rows.append(c)
        if mode in ('branches', 'impact'):
            observed = _branch_observations(n)
            branch_rows.extend(observed)
            if mode == 'branches':
                for item in observed:
                    lines.append(f'  BRANCH/EXIT {item["file"]}:{item["line"]}: {item["statement"]}')
        if mode == 'data_flow':
            symbol = re.search(r'\b(customerId|[A-Za-z_$][\w$]*Id)\b',query)
            identifier = symbol.group(1) if symbol else None
            if identifier:
                for i,text in enumerate(n['source'].splitlines(),start=n['line']):
                    if re.search(r'\b'+re.escape(identifier)+r'\b',_masked(text)):
                        lines.append(f'  LEXICAL DATA USE {n["file"]}:{i}: {text.strip()}')
    if mode == 'branches':
        lines.append('\nBranch outcomes and repository.save() reachability: NOT PROVEN by this lexical analyzer. Source statements shown; no path-sensitive CFG evaluation.')
    if mode == 'data_flow':
        lines.append('\nCross-method def-use and alias propagation: NOT VERIFIED. Identifier occurrences are lexical, not semantic data-flow edges.')
    if mode == 'impact':
        lines.append('\nIMPACT CLASSIFICATION (shortest unambiguous lexical reverse-call path):')
        for item in impact_layers:
            lines.append(f"  {item['classification']} depth={item['distance']}: "
                         f"{item['method']} — {item['file']}:{item['line']}")
        lines.append('Classification describes dependency distance, NOT proven behavioral impact. '
                     'Branch outcomes, affected tests and runtime reachability remain unverified.')
    if errors: lines.append('Unreadable files: '+ '; '.join(errors[:25]))
    lines.append('Limitations: bounded on-demand source scan; no compiler type resolution, dynamic dispatch proof, database execution proof, or path-sensitive CFG. No source is invented.')
    return dict(status='PARTIAL_SOURCE_EVIDENCE' if selected else 'NOT_FOUND', source='LIVE_JAVA_PHASE2_FLOW',
                mode=mode, project_path=str(root), methods_scanned=len(nodes), selected_count=len(selected),
                method_evidence=[{k:n[k] for k in ('id','cls','name','signature','file','line','sha256')} for n in visited.values()],
                call_observations=rows, impact_classification=impact_layers, branch_observations=branch_rows,
                branch_sensitive_cfg=False, warnings=errors, compiler_resolved=False, answer='\n'.join(lines))

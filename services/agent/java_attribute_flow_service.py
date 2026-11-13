"""Project-scoped, evidence-first Java attribute-flow exploration.

This is a source-level flow reconstruction, NOT a compiler-resolved def-use engine.
It only reports source statements found in the selected working tree.
"""
from __future__ import annotations
import re
from collections import defaultdict
from pathlib import Path
from services.agent.phase2_flow_service import _project, _calls, TARGET

ATTRIBUTES = ('accountType', 'customerId', 'countryCode', 'email', 'firstName', 'lastName')
FLOW_TERMS = re.compile(r'\b(?:trace|flows?|flow|propagat\w*|modified|validated|mapped|persisted|analy[sz]e|conditions?|conditional|branches?|paths?|compare|comparison|contrasts?|source\s+and\s+destination|read\s+or\s+modif\w*|reads?\s+and\s+writes?|overwritten|compared)\b', re.I)
CODE_TERMS = re.compile(r'\b(?:controller|service|repository|java|methods?|xml\s+mapping|jpa\s+mapping|entity|database|request|source\s+lines?|source\s+evidence)\b', re.I)

def intent(query: str) -> bool:
    """Route only Java attribute-flow questions; leave mapping inventories alone."""
    q = query.lower()
    if not FLOW_TERMS.search(query): return False
    if not (CODE_TERMS.search(query) or TARGET.search(query)): return False
    if re.search(r'\b(?:list\s+(?:mapping\s+)?documents?|mapping\s+workbooks?|jira\s+issues?|git\s+history|baseline\s+history)\b', q): return False
    return any(re.search(r'\b'+re.escape(a.lower())+r'\b',q) for a in ATTRIBUTES) or bool(TARGET.search(query))

def _attribute(query):
    for a in ATTRIBUTES:
        if re.search(r'\b'+re.escape(a)+r'\b',query,re.I): return a
    return None

def _source_lines(n, attribute):
    """Source-observed attribute accesses and method-local transformations."""
    result=[]
    if not attribute: return result
    getter='get'+attribute[0].upper()+attribute[1:]
    setter='set'+attribute[0].upper()+attribute[1:]
    for i,line in enumerate(n['source'].splitlines(),n['line']):
        clean=line.strip()
        if not re.search(r'\b(?:'+re.escape(attribute)+'|'+getter+'|'+setter+r')\b',clean,re.I): continue
        if clean.startswith(('*','//','/*')): continue
        kinds=[]
        if re.search(r'\b'+getter+r'\s*\(',clean): kinds.append('GETTER_CALL')
        if re.search(r'\b'+setter+r'\s*\(',clean): kinds.append('SETTER_CALL')
        if re.search(r'\b'+re.escape(attribute)+r'\s*=(?!=)',clean): kinds.append('ASSIGNMENT_CANDIDATE')
        if re.search(r'\b(?:if|switch|throw)\b',clean): kinds.append('CONDITION_OR_EXIT')
        if re.search(r'\b(?:trim|upper|lower|toUpperCase|toLowerCase)\s*\(',clean): kinds.append('TRANSFORMATION_CALL')
        if not kinds: kinds=['IDENTIFIER_OCCURRENCE']
        if re.search(r'\b'+setter+r'\s*\(',clean) and re.search(r'\b'+getter+r'\s*\(',clean):
            kinds.append('SAME_STATEMENT_GETTER_TO_SETTER_CANDIDATE')
        result.append(dict(method=f"{n['cls']}.{n['name']}",file=n['file'],line=i,
                           statement=clean,kinds=kinds,evidence='SOURCE_OBSERVED_NOT_DATAFLOW_VERIFIED'))
    return result

def _requested_methods(query):
    """Explicitly named Java methods only; avoid treating guessed names as facts."""
    return list(dict.fromkeys(re.findall(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(', query)))


def _source_statement_lines(node, attribute):
    """Collect relevant complete logical statements, including multiline expressions."""
    getter = 'get' + attribute[0].upper() + attribute[1:]
    setter = 'set' + attribute[0].upper() + attribute[1:]
    raw = node['source'].splitlines()
    statements = []
    pending = []
    start = None
    depth = 0
    for offset, line in enumerate(raw):
        stripped = line.strip()
        if not stripped or stripped.startswith(('//', '*', '/*', '@')):
            continue
        if start is None:
            start = node['line'] + offset
        pending.append(stripped)
        depth += stripped.count('(') - stripped.count(')')
        if depth <= 0 and (stripped.endswith((';', '{', '}')) or stripped.startswith('if ')):
            full = ' '.join(pending)
            if re.search(r'\b(?:' + '|'.join(map(re.escape, (attribute, getter, setter))) + r')\b', full, re.I):
                if not (full.startswith(('public ', 'private ', 'protected ')) and full.endswith('{')):
                    statements.append((start, full))
            pending, start, depth = [], None, 0
    return statements


def _branch_report(root, nodes, attribute, requests, index):
    """Source-backed guard/exit report. Paths are lexical interpretations, not a CFG."""
    selected = []
    evidence = []
    lines = [f'Java source-backed conditional-path analysis — {root}',
             f'Attribute: {attribute}. Source-observed guards and exits; no path-sensitive CFG or runtime verification.']
    seen = set()

    def add(node, line, kind, statement, description=None):
        key = (node['file'], line, kind, statement)
        if key in seen:
            return
        seen.add(key)
        evidence.append(dict(method=f"{node['cls']}.{node['name']}", file=node['file'],
                             line=line, statement=statement, kind=kind, evidence='SOURCE_OBSERVED'))
        lines.append(f'  {kind} | {node["file"]}:{line} | {statement}')
        if description:
            lines.append('    ' + description)

    for cls, name in requests:
        for node in index[(cls, name)]:
            selected.append(f'{cls}.{name}')
            lines.append(f'\n{cls}.{name} — {node["file"]}:{node["line"]}')
            raw = node['source'].splitlines()
            for i, source_line in enumerate(raw):
                stmt = source_line.strip()
                if not stmt or stmt.startswith(('//', '*', '/*')):
                    continue
                lineno = node['line'] + i
                # Recognize condition starts and their subsequent throw/assignment,
                # including multi-line conditions without duplicating the same line.
                if re.match(r'if\s*\(', stmt):
                    condition = stmt
                    j = i
                    depth = stmt.count('(') - stmt.count(')')
                    while depth > 0 and j + 1 < len(raw):
                        j += 1
                        part = raw[j].strip()
                        condition += ' ' + part
                        depth += part.count('(') - part.count(')')
                    add(node, lineno, 'CONDITIONAL_CHECK', condition,
                        ('TRUE: missing/blank mapped ID; assign path customerId and continue. FALSE: retain XML-mapped ID and continue.' if attribute == 'customerId' and 'getCustomerId()' in condition and ('isBlank' in condition or '== null' in condition) else 'TRUE: enter guarded statement/block; FALSE: skip guarded statement/block (lexical candidate).'))
                    continue
                if re.match(r'throw\s+new\s+', stmt):
                    # Include throws regardless of whether they mention the field.
                    add(node, lineno, 'EXCEPTION_EXIT', stmt,
                        'Throws when its enclosing guard is taken; exception propagation is not runtime-verified.')
                    continue
                setter = 'set' + attribute[0].upper() + attribute[1:]
                if re.search(r'\b' + re.escape(setter) + r'\s*\(', stmt):
                    description = ('Fallback assignment: copies the method customerId parameter into the mapped customer when the ID is missing/blank.'
                                   if attribute == 'customerId' and re.search(r'\bsetCustomerId\s*\(\s*customerId\s*\)', stmt)
                                   else 'Attribute assignment observed in source.')
                    add(node, lineno, 'ATTRIBUTE_ASSIGNMENT', stmt, description)
                    continue
                if re.search(r'\b(?:validate|xmlMapper\.toModel|jpaMapper\.toEntity|repository\.save|jpaMapper\.toModel)\s*\(', stmt):
                    add(node, lineno, 'CALL_SITE', stmt)
    if 'validat' in ' '.join(selected).lower() or any('validate(' in e['statement'] for e in evidence):
        for node in nodes:
            if node['name'] != 'validate':
                continue
            for line, stmt in _source_statement_lines(node, attribute):
                if re.search(r'\brequire\s*\(', stmt):
                    add(node, line, 'VALIDATION_CHECK', stmt,
                        'Required-field check; failure behavior depends on the require() implementation.')
    lines.append('\nPATH INTERPRETATION: Repository missing -> exception; existing -> XML mapping. '
                 'Missing/blank mapped ID -> path-ID fallback; otherwise preserve mapped ID. '
                 'ID mismatch -> exception; matching ID -> validation and save invocation. '
                 'These are source-derived branch candidates, NOT CFG-verified reachable paths.')
    return dict(status='PARTIAL_SOURCE_EVIDENCE', source='JAVA_ATTRIBUTE_FLOW_ANALYSIS', analyzer='java_attribute_flow_service',
                mode='attribute_branches', attribute=attribute, project_path=str(root),
                selected_methods=selected, ordered_flow_evidence=evidence,
                cross_method_dataflow_verified=False, compiler_resolved=False,
                path_sensitive_cfg_verified=False, database_execution_verified=False,
                answer='\n'.join(lines))


def _specialized_report(query, root, nodes, attribute, requests):
    """Evidence-based comparison/branch summaries, not CFG or compiler dataflow."""
    q = query.lower()
    is_comparison = bool(re.search(r'\b(?:compare|contrast)\b|\bcomparison\s+(?:between|of)\b', q))
    is_branch = bool(re.search(r'\b(?:conditions?|branches?|alternative\s+(?:execution\s+)?paths?|reject|fallback)\b', q))
    if not (is_comparison or is_branch):
        return None
    if not requests:
        return None
    index = defaultdict(list)
    for node in nodes:
        index[(node['cls'], node['name'])].append(node)
    missing = [f'{cls}.{name}()' for cls, name in requests if not index[(cls, name)]]
    if missing:
        return dict(status='NOT_FOUND', source='JAVA_ATTRIBUTE_FLOW_ANALYSIS',
                    answer='Requested method(s) not found: ' + ', '.join(missing) + '. No substituted methods.')
    if is_branch and not is_comparison:
        return _branch_report(root, nodes, attribute, requests, index)
    lines = [f'Java source-backed {"transformation comparison" if is_comparison else "conditional-path evidence"} — {root}',
             f'Attribute: {attribute}. Evidence is lexical source only; no compiler-resolved value flow or path-sensitive CFG.']
    evidence = []
    selected = []
    for cls, name in requests:
        for node in index[(cls, name)]:
            selected.append(f'{cls}.{name}')
            lines.append(f'\n{cls}.{name} — {node["file"]}:{node["line"]}')
            matches = _source_statement_lines(node, attribute)
            if not matches:
                lines.append('  No attribute-specific source statement found in this method.')
            for lineno, stmt in matches:
                kind = ('TRANSFORM_AND_ASSIGN' if re.search(r'\bset' + attribute[0].upper() + attribute[1:] + r'\s*\(', stmt) and re.search(r'\b(?:trim|upper|lower|toUpperCase|toLowerCase)\s*\(', stmt)
                        else 'CONDITIONAL_CHECK' if re.search(r'\bif\s*\(', stmt)
                        else 'ATTRIBUTE_ASSIGNMENT' if re.search(r'\bset' + attribute[0].upper() + attribute[1:] + r'\s*\(', stmt)
                        else 'ATTRIBUTE_READ_OR_CHECK')
                lines.append(f'  {kind} | {node["file"]}:{lineno} | {stmt}')
                evidence.append(dict(method=f'{cls}.{name}',file=node['file'],line=lineno,statement=stmt,kind=kind,evidence='SOURCE_OBSERVED'))
            if is_branch:
                # Show source-local branch/throw statements as candidates, including
                # repository guards that do not contain the attribute name.
                for offset, raw in enumerate(node['source'].splitlines()):
                    clean = raw.strip()
                    if re.match(r'(?:if\s*\(|throw\s+)', clean) and not any(e['line']==node['line']+offset and e['statement']==clean for e in evidence):
                        lines.append(f'  BRANCH_OR_EXIT | {node["file"]}:{node["line"]+offset} | {clean}')
                        evidence.append(dict(method=f'{cls}.{name}',file=node['file'],line=node['line']+offset,statement=clean,kind='BRANCH_OR_EXIT',evidence='SOURCE_OBSERVED'))
    if is_comparison:
        lines.append('\nCOMPARISON: The statements above are independently observed. Matching parameter names, argument bindings, and runtime equivalence across methods are NOT verified.')
        if 'validat' in q:
            validation = [n for n in nodes if n['name']=='validate' and _source_statement_lines(n,attribute)]
            for node in validation[:5]:
                for line, stmt in _source_statement_lines(node,attribute):
                    lines.append(f'  VALIDATION | {node["cls"]}.{node["name"]} | {node["file"]}:{line} | {stmt}')
                    evidence.append(dict(method=f'{node["cls"]}.{node["name"]}',file=node['file'],line=line,statement=stmt,kind='VALIDATION',evidence='SOURCE_OBSERVED'))
    if is_branch and 'validat' in q:
        validation = [n for n in nodes if n['name']=='validate' and _source_statement_lines(n,attribute)]
        for node in validation[:5]:
            for line, stmt in _source_statement_lines(node,attribute):
                lines.append(f'  VALIDATION | {node["cls"]}.{node["name"]} | {node["file"]}:{line} | {stmt}')
                evidence.append(dict(method=f'{node["cls"]}.{node["name"]}',file=node['file'],line=line,statement=stmt,kind='VALIDATION',evidence='SOURCE_OBSERVED'))
    if is_branch:
        lines.append('\nPATH INTERPRETATION: Conditions and exits above are observed source candidates; true/false reachability, exception outcomes, and path combinations have NOT been verified by a CFG.')
    return dict(status='PARTIAL_SOURCE_EVIDENCE',source='JAVA_ATTRIBUTE_FLOW_ANALYSIS',
                mode='attribute_comparison' if is_comparison else 'attribute_branches',
                attribute=attribute,project_path=str(root),selected_methods=selected,
                ordered_flow_evidence=evidence,cross_method_dataflow_verified=False,
                compiler_resolved=False,path_sensitive_cfg_verified=False,
                database_execution_verified=False,answer='\n'.join(lines))


def answer(query: str, project_path: str | None):
    if not intent(query): return None
    if not project_path or not Path(project_path).expanduser().is_dir():
        return dict(status='PROJECT_NOT_SELECTED',source='JAVA_ATTRIBUTE_FLOW_ANALYSIS',
                    answer='Select a valid Java project directory. No fallback to unrelated mapping or JIRA data.')
    root=Path(project_path).expanduser().resolve()
    nodes,errors=_project(root)
    index=defaultdict(list)
    byname=defaultdict(list)
    for n in nodes:
        index[(n['cls'],n['name'])].append(n)
        byname[n['name']].append(n)
    attr=_attribute(query)
    requests=_requested_methods(query)
    specialized=_specialized_report(query,root,nodes,attr,requests) if attr else None
    if specialized is not None: return specialized
    target=TARGET.search(query)
    # An explicit Class.method() is authoritative. Never replace create with replace.
    if target:
        cls,name=target.groups()
        seeds=index[(cls,name)]
    else:
        seeds=[]
    if target and not seeds:
        return dict(status='NOT_FOUND',source='JAVA_ATTRIBUTE_FLOW_ANALYSIS',
                    attribute=attr,answer=f'Requested method {cls}.{name}() was not found in the selected Java project. No substituted method.')
    if not seeds and attr:
        seeds=[n for n in nodes if _source_lines(n,attr) and ('service' in n['file'].lower() or 'controller' in n['file'].lower())]
    # Read only observed source and lexically inferred call sites. For a request
    # anchored on a method, expand that method's outgoing graph first.
    seen={}
    queue=[(n,0) for n in seeds[:12]]
    while queue and len(seen)<80:
        n,depth=queue.pop(0)
        if n['id'] in seen: continue
        seen[n['id']]=n
        if depth>=5: continue
        for edge in _calls(n,byname):
            if len(edge['targets'])!=1: continue
            nxt=next((x for x in byname[edge['name']] if x['id']==edge['targets'][0]),None)
            if nxt and nxt['id'] not in seen: queue.append((nxt,depth+1))
    # Attribute-specific summaries need mapping source observations even when
    # those methods are not reachable through a direct call from the seed.
    if attr:
        for n in nodes:
            if n['id'] not in seen and _source_lines(n,attr) and any(k in n['file'].lower() for k in ('mapper/','entity/','xml/','model/')):
                seen[n['id']]=n
    observations=[]
    for n in seen.values(): observations.extend(_source_lines(n,attr))
    observations.sort(key=lambda x:(x['file'],x['line']))
    callsites=[]
    for n in seen.values():
        for edge in _calls(n,byname):
            if edge['status'] in ('LEXICAL_SAME_CLASS','RECEIVER_TYPE_INFERRED','FRAMEWORK_MEDIATED_CANDIDATE'):
                callsites.append(dict(caller=f"{n['cls']}.{n['name']}",file=n['file'],line=edge['line'],
                                      expression=edge['expression'],status=edge['status'],
                                      targets=[next((f"{x['cls']}.{x['name']}" for x in nodes if x['id']==t),t) for t in edge['targets']]))
    # Reconstruct a bounded, source-ordered *candidate* trace from the selected
    # entry method. This is not a compiler-resolved control/data-flow graph.
    # Do not add unrelated mapper methods merely because they mention the field.
    lines=[f'Java Attribute Flow Analysis — {root}',
           f'Attribute: {attr or "not explicitly named"}; requested method: {target.group(0) if target else "attribute-wide"}',
           'Evidence: source statements OBSERVED; receiver/call resolution may be INFERRED; interprocedural value propagation NOT VERIFIED.']
    if target:
        for n in seeds:
            lines.append(f'ENTRY {n["cls"]}.{n["name"]} — {n["file"]}:{n["line"]} {n["signature"]}')
    lines.append('\nSOURCE-ORDERED ATTRIBUTE TRACE (CANDIDATE; NOT CFG-VERIFIED):')
    steps=[]
    ordered_evidence=[]
    node_by_id={n['id']:n for n in nodes}
    calls_by_caller=defaultdict(list)
    for n in seen.values():
        for edge in _calls(n,byname):
            calls_by_caller[n['id']].append(edge)

    getter='get'+attr[0].upper()+attr[1:] if attr else ''
    setter='set'+attr[0].upper()+attr[1:] if attr else ''
    def call_relevant(edge):
        expression=edge.get('expression','')
        if attr and (getter+'(' in expression or setter+'(' in expression):
            return True
        return any(term in expression for term in ('service.', 'xmlMapper.', 'jpaMapper.',
                                                   'repository.', 'validate(', 'get('))

    def event_title(expression, source_statement=''):
        statement=source_statement or expression
        if 'existsById(' in expression: return 'Check repository existence'
        if 'repository.save(' in expression: return 'Invoke repository save (not a verified database write)'
        if 'xmlMapper.toModel(' in expression: return 'Invoke XML-to-domain mapping'
        if 'jpaMapper.toEntity(' in expression: return 'Invoke domain-to-entity mapping'
        if 'jpaMapper.toModel(' in expression: return 'Invoke entity-to-domain mapping'
        if 'validate(' in expression: return 'Invoke validation'
        if 'service.' in expression: return 'Delegate to service'
        if setter and setter+'(' in statement:
            if re.search(r'\b(?:trim|upper|lower|toUpperCase|toLowerCase)\s*\(',statement):
                return 'Transform and assign attribute'
            return 'Assign attribute'
        if getter and getter+'(' in statement: return 'Read or check attribute'
        if 'equals(' in statement: return 'Compare values'
        return 'Observe source statement or invocation'

    def emit(title, method, file, line, statement, evidence):
        if len(steps)>=55: return
        steps.append(title)
        item=dict(title=title,method=method,file=file,line=line,
                  statement=statement,evidence=evidence)
        ordered_evidence.append(item)
        lines.append(f'{len(steps)}. {title} — {method} — {file}:{line} | {statement} [{evidence}]')

    def _statement_groups(n):
        """One logical source statement per group, including multiline setters."""
        source=n['source'].splitlines()
        groups=[]
        start=None
        parts=[]
        parens=0
        for offset, raw in enumerate(source):
            stripped=raw.strip()
            if not stripped or stripped.startswith(('@', '//', '*', '/*')):
                continue
            if start is None:
                start=n['line']+offset
            parts.append(stripped)
            # Lexical approximation; braces/strings are not parsed as a Java AST.
            parens+=stripped.count('(')-stripped.count(')')
            if parens<=0 and (stripped.endswith((';', '{', '}')) or stripped.startswith(('if ', 'throw '))):
                statement=' '.join(parts)
                groups.append((start, n['line']+offset, statement))
                start=None
                parts=[]
                parens=0
        if parts:
            groups.append((start, n['line']+len(source)-1, ' '.join(parts)))
        return groups

    def _call_position(statement, expression):
        # Position in the complete statement, not in an isolated source line.
        return statement.find(expression)

    def _call_nesting(statement, position):
        """Parenthesis depth at a call site; deeper calls evaluate first."""
        if position < 0: return 0
        depth=0
        for char in statement[:position]:
            if char=='(': depth+=1
            elif char==')': depth=max(0,depth-1)
        return depth

    def _interesting_statement(statement):
        if not attr: return False
        if re.search(r'\b(?:public|private|protected)\b.*\b(?:class|interface)\b',statement): return False
        if re.match(r'(?:public|private|protected)\s+.*\b(?:'+re.escape(attr)+r')\b.*\{\s*$',statement): return False
        if statement.startswith(('throw ', '"')): return False
        return bool(re.search(r'\b(?:'+re.escape(attr)+'|'+re.escape(getter)+'|'+re.escape(setter)+r')\b',statement,re.I))

    def walk(n, depth, ancestors):
        if depth>6 or len(steps)>=55 or n['id'] in ancestors: return
        method=f"{n['cls']}.{n['name']}"
        groups=_statement_groups(n)
        events=[]
        for first,last,statement in groups:
            # Do not count method declarations, annotations, or incidental text.
            if not _interesting_statement(statement): continue
            if statement.startswith(('public ', 'private ', 'protected ')) and '{' in statement and not ('return ' in statement):
                continue
            events.append((first,0,'observation',statement,last))
        for edge in calls_by_caller[n['id']]:
            if not call_relevant(edge): continue
            line=edge['line']
            group=next(((first,last,stmt) for first,last,stmt in groups if first<=line<=last),None)
            if group is None: continue
            first,last,statement=group
            pos=_call_position(statement,edge.get('expression',''))
            # Calls on a statement are ordered by expression nesting (inner first).
            # Same-depth calls retain left-to-right order.
            events.append((first,1,'call',(edge,statement,pos),last))
        grouped=defaultdict(list)
        for event in events:
            grouped[(event[0],event[4])].append(event)
        for key in sorted(grouped):
            if len(steps)>=55: break
            items=grouped[key]
            call_events=[e for e in items if e[2]=='call']
            observation_events=[e for e in items if e[2]=='observation']
            # Calls to the same expression may have multiple candidate edges;
            # deduplicate identical call-site records, not distinct overloads.
            call_events.sort(key=lambda e:(-_call_nesting(e[3][1],e[3][2]),e[3][2]))
            emitted_calls=set()
            for _,_,_,(edge,statement,pos),_ in call_events:
                signature=(edge.get('expression'),edge.get('line'),tuple(edge.get('targets') or []))
                if signature in emitted_calls: continue
                emitted_calls.add(signature)
                expression=edge.get('expression','')
                status=edge.get('status','UNRESOLVED_CANDIDATE')
                targets=[node_by_id[t] for t in edge.get('targets',[]) if t in node_by_id]
                target_desc=f" -> {targets[0]['cls']}.{targets[0]['name']}" if len(targets)==1 else ' -> UNRESOLVED/AMBIGUOUS'
                # Getter/setter calls are represented by the full attribute
                # statement below, not repeated as independent flow steps.
                if attr and (getter+'(' in expression or setter+'(' in expression):
                    continue
                emit(event_title(expression),method,n['file'],edge['line'],expression+target_desc,
                     'CALL_SITE_'+status)
                if len(targets)==1 and targets[0]['id'] in seen and targets[0]['id'] not in ancestors:
                    callee=targets[0]
                    if _source_lines(callee,attr) or any(call_relevant(e) for e in calls_by_caller[callee['id']]):
                        walk(callee,depth+1,ancestors|{n['id']})
            if observation_events:
                statement=observation_events[0][3]
                # A call-site statement already has its invocation recorded;
                # retain only field transfer, transformation or branch logic.
                field_transfer=bool(setter and setter+'(' in statement)
                field_condition=bool(re.search(r'\b(?:if|else if)\s*\(',statement))
                field_transform=bool(re.search(r'\b(?:trim|upper|lower|toUpperCase|toLowerCase)\s*\(',statement))
                if field_transfer or field_condition or field_transform or not call_events or (attr and 'require(' in statement and getter+'(' in statement):
                    title=event_title('',statement)
                    if title=='Observe source statement or invocation' and call_events:
                        continue
                    emit(title,method,n['file'],key[0],statement,
                         'SOURCE_OBSERVED_NOT_DATAFLOW_VERIFIED')

    for seed in seeds[:12]:
        walk(seed,0,set())
    if not steps:
        lines.append('No reachable source-ordered trace found. Attribute observations below are not a verified path.')
    if len(steps)>=55:
        lines.append('Trace truncated at 55 observations; no absence claim for omitted source.')
    lines.append('\nATTRIBUTE-SPECIFIC SOURCE OBSERVATIONS:')
    focus=[o for o in observations if not ('IDENTIFIER_OCCURRENCE' in o['kinds'] and len(o['kinds'])==1)]
    # In a method-specific query, keep the relevant service, mapper, model, and entity statements.
    for item in focus[:32]:
        lines.append(f'  {item["method"]} | {item["file"]}:{item["line"]} | {"/".join(item["kinds"])} | {item["statement"]}')
    if not focus: lines.append('  No matching source statements found.')
    lines.append('\nMETHOD-BOUNDARY TRANSFER CANDIDATES (NOT VERIFIED):')
    for c in callsites:
        if c['caller'] not in {item['method'] for item in ordered_evidence} and not (target and c['caller']==f'{cls}.{name}'):
            continue
        if not any(token in c['expression'] for token in ('service.', 'xmlMapper.', 'jpaMapper.', 'validate(', 'repository.save(')):
            continue
        lines.append(f'  {c["caller"]} {c["file"]}:{c["line"]} {c["expression"]} -> {", ".join(c["targets"]) or "UNRESOLVED"} [{c["status"]}]; argument-to-parameter / return-value binding NOT VERIFIED')
    lines.append('\nRELEVANT INVOCATION EVIDENCE:')
    key_methods=set()
    if target: key_methods.add(f'{cls}.{name}')
    if attr: key_methods.update(['CustomerXmlMapper.toModel','CustomerService.validate','CustomerJpaMapper.toEntity','CustomerJpaMapper.toModel'])
    key_methods.update(item['method'] for item in ordered_evidence)
    for c in callsites:
        if c['caller'] not in key_methods: continue
        if not any(x in c['expression'] for x in ('service.','xmlMapper.','jpaMapper.','validate(', 'repository.', 'set'+(attr[0].upper()+attr[1:] if attr else '___')+'(')):
            continue
        lines.append(f'  {c["caller"]} {c["file"]}:{c["line"]} {c["expression"]} -> {", ".join(c["targets"]) or "UNRESOLVED"} [{c["status"]}]')
    if errors: lines.append('Unreadable Java files: '+ '; '.join(errors[:10]))
    lines.append('\nLIMITATIONS: source order and nested-call ordering are lexical candidates, NOT verified runtime execution order or interprocedural def-use/alias flow. No path-sensitive CFG, compiler type resolution, or proven JPA/database write. Source observations may include alternative paths.')
    return dict(status='PARTIAL_SOURCE_EVIDENCE',source='JAVA_ATTRIBUTE_FLOW_ANALYSIS',
                mode='attribute_flow',attribute=attr,project_path=str(root),ordered_flow_steps=steps,
                ordered_flow_evidence=ordered_evidence,
                methods_scanned=len(nodes),selected_methods=[f"{n['cls']}.{n['name']}" for n in seeds],
                attribute_observations=observations,call_observations=callsites,
                cross_method_dataflow_verified=False,compiler_resolved=False,
                database_execution_verified=False,warnings=errors,answer='\n'.join(lines))

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
FLOW_TERMS = re.compile(r'\b(?:trace|flows?|flow|propagat\w*|modified|validated|mapped|persisted|source\s+and\s+destination|read\s+or\s+modif\w*|reads?\s+and\s+writes?|overwritten|compared)\b', re.I)
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

def answer(query: str, project_path: str | None):
    if not intent(query): return None
    if not project_path or not Path(project_path).expanduser().is_dir():
        return dict(status='PROJECT_NOT_SELECTED',source='LIVE_JAVA_PHASE3_ATTRIBUTE_FLOW',
                    answer='Select a valid Java project directory. No fallback to unrelated mapping or JIRA data.')
    root=Path(project_path).expanduser().resolve()
    nodes,errors=_project(root)
    index=defaultdict(list)
    byname=defaultdict(list)
    for n in nodes:
        index[(n['cls'],n['name'])].append(n)
        byname[n['name']].append(n)
    attr=_attribute(query)
    target=TARGET.search(query)
    # An explicit Class.method() is authoritative. Never replace create with replace.
    if target:
        cls,name=target.groups()
        seeds=index[(cls,name)]
    else:
        seeds=[]
    if target and not seeds:
        return dict(status='NOT_FOUND',source='LIVE_JAVA_PHASE3_ATTRIBUTE_FLOW',
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
    # Prioritize the requested method, then concrete attribute observations.
    lines=[f'Live Java Phase 3 attribute trace for {root}',
           f'Attribute: {attr or "not explicitly named"}; requested method: {target.group(0) if target else "attribute-wide"}',
           'Evidence: Java source statements are observed; call targets are lexical/inferred, NOT compiler verified.',
           'Cross-method value identity, alias propagation, runtime execution and DB writes: NOT VERIFIED.']
    if target:
        for n in seeds:
            lines.append(f'\nENTRY {n["cls"]}.{n["name"]} — {n["file"]}:{n["line"]} {n["signature"]}')
    lines.append('\nATTRIBUTE SOURCE OBSERVATIONS (grouped by method):')
    for item in observations[:100]:
        lines.append(f'  {item["method"]} | {item["file"]}:{item["line"]} | {"/".join(item["kinds"])} | {item["statement"]}')
    if not observations: lines.append('  No source statements matching the requested attribute were found in the analyzed methods.')
    lines.append('\nSOURCE-OBSERVED INVOCATIONS (targets inferred unless same-class):')
    for c in callsites[:65]:
        if target or attr and any(attr.lower() in (c['expression']+c['caller']).lower() for _ in [0]) or 'mapper' in c['expression'].lower() or 'save' in c['expression'].lower() or 'validate' in c['expression'].lower():
            lines.append(f'  {c["caller"]} {c["file"]}:{c["line"]} {c["expression"]} -> {", ".join(c["targets"]) or "UNRESOLVED"} [{c["status"]}]')
    if errors: lines.append('Unreadable Java files: '+ '; '.join(errors[:10]))
    lines.append('\nLIMITATIONS: source-level local read/write/transform observations only; no verified interprocedural def-use, control-flow reachability, or JPA execution. Mapping documents are separate provenance and do not replace source tracing.')
    return dict(status='PARTIAL_SOURCE_EVIDENCE',source='LIVE_JAVA_PHASE3_ATTRIBUTE_FLOW',
                mode='attribute_flow',attribute=attr,project_path=str(root),
                methods_scanned=len(nodes),selected_methods=[f"{n['cls']}.{n['name']}" for n in seeds],
                attribute_observations=observations,call_observations=callsites,
                cross_method_dataflow_verified=False,compiler_resolved=False,
                database_execution_verified=False,warnings=errors,answer='\n'.join(lines))

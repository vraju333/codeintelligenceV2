"""Commit-scoped Java source evidence; conservative, read-only static call paths."""
from __future__ import annotations
import re
from services.agent.jira_commit_trace_service import JiraCommitTraceService


def _lex(source: str) -> list[str]:
    """Tokenize Java without whitespace/comments; keep literals intact."""
    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_$][\w$]*|\d+|[^\s]'
    return [x for x in re.findall(pattern, source) if not x.startswith(('//', '/*'))]


def tokens(java: str) -> str:
    return '\x1f'.join(_lex(java))


def _mask(source: str) -> str:
    """Blank comments and literals while preserving offsets and newlines."""
    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: re.sub(r'[^\n]', ' ', m.group()), source)


def _methods(source: str) -> dict:
    """Return method metadata with positions for annotation extraction."""
    masked = _mask(source)
    result = {}
    # Exclude invocations by requiring declaration shape and line-start anchoring.
    pat = re.compile(r'(?m)^[ \t]*(?:(?:public|private|protected|static|final|synchronized|abstract|native|default|strictfp)\s+)*(?:<[^\n{};]+>\s+)?(?:[\w$.?<>\[\],]+\s+)?([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w., \t]+)?\{')
    for m in pat.finditer(masked):
        name = m.group(1)
        if name in {'if','while','for','switch','catch','try','else','do','return','new','synchronized'}:
            continue
        start = m.end()-1
        depth = 0
        end = None
        for i in range(start, len(masked)):
            if masked[i] == '{': depth += 1
            elif masked[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i+1
                    break
        if end:
            result[name] = {'start':m.start(), 'end':end, 'line':source.count('\n',0,m.start())+1,
                            'body':source[m.start():end], 'masked':masked[m.start():end]}
    return result


def semantic_changes(trace: JiraCommitTraceService, commit: dict) -> list[dict]:
    out = []
    sha, parent = commit['sha'], commit.get('parent')
    for item in commit.get('java_files', []):
        path = item['file']
        if item.get('is_test'): continue
        new = trace.git('show', f'{sha}:{path}')
        old = trace.git('show', f'{parent}:{path}') if parent else ''
        old_m, new_m = _methods(old), _methods(new)
        for name in sorted(set(old_m) | set(new_m)):
            before, after = old_m.get(name), new_m.get(name)
            if tokens(before['body'] if before else '') != tokens(after['body'] if after else ''):
                out.append({'file':path,'method':name,
                            'kind':'BODY_CHANGED' if before and after else ('METHOD_ADDED' if after else 'METHOD_REMOVED'),
                            'line':after['line'] if after else None})
        pattern = r'\b(?:static\s+)?final\s+Pattern\s+(\w+)\s*=\s*Pattern\.compile\s*\(\s*("(?:\\.|[^"\\])*")'
        before_constants, after_constants = dict(re.findall(pattern,old)), dict(re.findall(pattern,new))
        for name in sorted(set(before_constants)|set(after_constants)):
            if before_constants.get(name)==after_constants.get(name):continue
            out.append({'file':path,'constant':name,'kind':'PATTERN_CHANGED',
                        'before':before_constants.get(name),'after':after_constants.get(name)})
            for method,meta in new_m.items():
                if re.search(r'\b'+re.escape(name)+r'\b',meta['masked']):
                    out.append({'file':path,'method':method,'kind':'DEPENDS_ON_CHANGED_PATTERN','constant':name})
    return list({(v['file'],v.get('method'),v['kind'],v.get('constant')):v for v in out}.values())


def _annotations(source: str, meta: dict, prior_end: int) -> str:
    """Annotations immediately preceding a declaration (not a previous method)."""
    prefix = source[prior_end:meta['start']]
    # Prevent class-level annotations from being attributed to first method.
    return prefix


def _mapping(annotation_text: str, base: str) -> tuple[str,str] | None:
    matches = list(re.finditer(r'@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\b\s*(?:\(([^)]*)\))?', annotation_text, re.S))
    if not matches: return None
    kind,args = matches[-1].group(1),matches[-1].group(2) or ''
    verbs = {'GetMapping':'GET','PostMapping':'POST','PutMapping':'PUT','PatchMapping':'PATCH','DeleteMapping':'DELETE'}
    verb=verbs.get(kind)
    if not verb:
        match=re.search(r'RequestMethod\.(GET|POST|PUT|PATCH|DELETE)',args)
        verb=match.group(1) if match else None
    if not verb:return None
    match=re.search(r'(?:\b(?:value|path)\s*=\s*)?"([^"\n]*)"',args)
    suffix=match.group(1) if match else ''
    path='/'+'/'.join(x.strip('/') for x in (base,suffix) if x.strip('/'))
    return verb,path


def verified_paths(trace: JiraCommitTraceService, commit: dict, semantic: list[dict]) -> list[dict]:
    sha=commit['sha']
    # Only changed methods in production service files, not similarly named methods in other classes.
    changed={(x['file'],x['method']) for x in semantic if x.get('method') and x['kind'] in ('BODY_CHANGED','DEPENDS_ON_CHANGED_PATTERN','METHOD_ADDED')}
    if not changed:return []
    files=trace.git('ls-tree','-r','--name-only',sha).splitlines()
    services={}
    controllers=[]
    for f in files:
        if not f.endswith('.java') or '/src/main/' not in '/'+f:continue
        if '/service/' not in '/'+f and '/controller/' not in '/'+f and 'Controller' not in f:continue
        src=trace.git('show',f'{sha}:{f}')
        class_match=re.search(r'\bclass\s+(\w+)',_mask(src))
        if not class_match:continue
        entry=(f,src,_methods(src))
        if '/service/' in '/'+f:services[class_match.group(1)]=entry
        if ('Controller' in f or '/controller/' in '/'+f) and re.search(r'@(?:RestController|Controller)\b',src):
            controllers.append(entry)
    results=[]
    for f,src,methods in controllers:
        # Look for controller-level RequestMapping before class declaration, not method mappings.
        class_pos=re.search(r'\bclass\s+\w+',_mask(src))
        header=src[:class_pos.start()] if class_pos else ''
        bases=re.findall(r'@RequestMapping\s*\(\s*(?:(?:value|path)\s*=\s*)?"([^"\n]*)"',header)
        base=bases[-1] if bases else ''
        # Both field and constructor injection: typed variable declarations anywhere in class.
        fields={var:typ for typ,var in re.findall(r'\b(\w+Service)\s+(\w+)\b',_mask(src)) if typ in services}
        previous=class_pos.end() if class_pos else 0
        for name,meta in sorted(methods.items(),key=lambda kv:kv[1]['start']):
            ann=_annotations(src,meta,previous)
            previous=max(previous,meta['end'])
            mapping=_mapping(ann,base)
            if not mapping:continue
            verb,url=mapping
            # Only explicit calls on a typed service variable count as the first edge.
            for variable,operation in re.findall(r'\b(\w+)\s*\.\s*(\w+)\s*\(',meta['masked']):
                typ=fields.get(variable)
                if typ not in services:continue
                service_file,_,service_methods=services[typ]
                if operation not in service_methods:continue
                # Traverse same-class direct calls, including intermediate helper methods.
                queue=[(operation,[operation])]
                visited=set()
                while queue:
                    current,chain=queue.pop(0)
                    if current in visited:continue
                    visited.add(current)
                    if (service_file,current) in changed:
                        results.append({'endpoint':f'{verb} {url}','controller_file':f,'controller_method':name,
                                        'service_file':service_file,'service_method':operation,'changed_method':current,
                                        'evidence':'SOURCE_VERIFIED_DIRECT_CALLS',
                                        'path':f'{name} -> '+ ' -> '.join(f'{typ}.{step}' for step in chain)})
                    body=service_methods[current]['masked']
                    for called in service_methods:
                        if called not in visited and (re.search(r'(?<![\w.])'+re.escape(called)+r'\s*\(',body)
                            or re.search(r'\bthis\s*\.\s*'+re.escape(called)+r'\s*\(',body)):
                            queue.append((called,chain+[called]))
    return list({(x['endpoint'],x['path']):x for x in results}.values())


def analyse_commit_paths(trace: JiraCommitTraceService, git_trace: dict) -> dict:
    results=[]
    for commit in git_trace.get('commits',[]):
        semantic=semantic_changes(trace,commit)
        paths=verified_paths(trace,commit,semantic)
        results.append({'commit':commit['sha'],'semantic_changes':semantic,'verified_source_paths':paths,
                        'proof_level':'SOURCE_VERIFIED_DIRECT_CALLS' if paths else 'NO_VERIFIED_CALL_PATH'})
    return {'commits':results,'scope':'explicit JIRA commit and parent; working tree excluded',
            'limitations':'Conservative Java source direct-call checks; not polymorphic/dynamic call graph or test execution proof'}


def format_commit_paths(result: dict) -> str:
    lines=['COMMIT-SCOPED SEMANTIC CHANGES AND SOURCE CALL PATHS']
    for c in result['commits']:
        lines.append('Commit: '+c['commit'])
        for change in c['semantic_changes']:
            label=change.get('method') or change.get('constant') or '?'
            lines.append(f"  {change['kind']}: {label} ({change['file']})")
            if change.get('before') is not None:
                lines.append(f"    Before: {change['before']} | After: {change.get('after')}")
        if not c['verified_source_paths']:
            lines.append('  Controller call paths: NOT VERIFIED from direct source calls')
        for path in c['verified_source_paths']:
            lines.append(f"  SOURCE-VERIFIED {path['endpoint']}: {path['path']}")
    lines.append('Static source calls do not prove runtime execution; QA execution remains NOT_VERIFIED.')
    return '\n'.join(lines)

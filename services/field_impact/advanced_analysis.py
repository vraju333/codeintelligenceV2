"""Conservative control-flow and interprocedural candidate analysis.

This is static evidence, not a runtime trace or sound whole-program points-to analysis.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path

TRANSFORM = re.compile(r"\b(trim|strip|normalize|format|toUpperCase|toLowerCase)\s*\(")
JAVA_METHOD = re.compile(r"\b(?:public|private|protected)\s+(?:(?:static|final|synchronized)\s+)*(?:[\w<>?,.\[\]]+\s+)?(\w+)\s*\(([^)]*)\)")
JAVA_INVOKE = re.compile(r"(?:(\w+)\s*\.)?(\w+)\s*\(")
EXCLUDE = {'if','for','while','switch','catch','return','new','super','this','throw'}

def _python_nodes(source, relative):
    tree=ast.parse(source)
    methods=[]; calls=[]; branches=[]; transfers=[]
    for node in ast.walk(tree):
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        owner={'file':relative,'method':node.name,'line':node.lineno}
        methods.append(owner)
        for child in ast.walk(node):
            if isinstance(child,(ast.If,ast.For,ast.AsyncFor,ast.While,ast.Try,ast.Match)):
                branches.append({**owner,'line':child.lineno,'kind':type(child).__name__.upper(),'evidence_level':'DIRECT_AST'})
            if isinstance(child,ast.Call):
                callee=child.func.attr if isinstance(child.func,ast.Attribute) else child.func.id if isinstance(child.func,ast.Name) else None
                if callee:calls.append({**owner,'line':child.lineno,'callee':callee,'receiver':ast.unparse(child.func.value) if isinstance(child.func,ast.Attribute) else None})
            if isinstance(child,(ast.Assign,ast.AnnAssign,ast.Return)):
                value=child.value
                if value is None:continue
                expression=ast.unparse(value)
                if isinstance(child,ast.Return):target='return'
                else:target=ast.unparse(child.targets[0]) if isinstance(child,ast.Assign) and child.targets else ast.unparse(child.target)
                transfers.append({**owner,'line':child.lineno,'source':expression,'target':target,'transformations':TRANSFORM.findall(expression),'evidence_level':'SAME_METHOD_AST'})
    return methods,calls,branches,transfers

def _java_nodes(source,relative):
    # Tree-sitter AST identifies real method and branch boundaries, avoiding regex brace heuristics.
    from tree_sitter import Language,Parser
    import tree_sitter_java
    tree=Parser(Language(tree_sitter_java.language())).parse(source.encode())
    methods=[];calls=[];branches=[];transfers=[]
    raw=source.encode()
    def txt(node):return raw[node.start_byte:node.end_byte].decode('utf-8','replace') if node else ''
    def walk(node,owner=None):
        nonlocal methods,calls,branches,transfers
        if node.type in ('method_declaration','constructor_declaration'):
            name=node.child_by_field_name('name')
            owner={'file':relative,'method':txt(name),'line':node.start_point.row+1}
            methods.append(owner)
        if owner:
            line=node.start_point.row+1
            if node.type in ('if_statement','switch_expression','switch_statement','for_statement','enhanced_for_statement','while_statement','do_statement','try_statement','catch_clause'):
                branches.append({**owner,'line':line,'kind':node.type.upper(),'evidence_level':'DIRECT_AST'})
            if node.type=='method_invocation':
                name=txt(node.child_by_field_name('name'))
                receiver=txt(node.child_by_field_name('object')) or None
                if name:calls.append({**owner,'line':line,'callee':name,'receiver':receiver})
            if node.type in ('assignment_expression','return_statement'):
                left=node.child_by_field_name('left');right=node.child_by_field_name('right')
                expr=txt(node)
                transfers.append({**owner,'line':line,'source':txt(right) if right else expr,'target':txt(left) if left else 'return','transformations':TRANSFORM.findall(expr),'evidence_level':'SAME_METHOD_AST'})
        for child in node.named_children:walk(child,owner)
    walk(tree.root_node)
    return methods,calls,branches,transfers

def advanced_evidence(result:dict,max_items:int=2000)->dict:
    root=Path(result['project_path']).resolve()
    entries=result.get('writers',[])+result.get('readers',[])+result.get('declarations',[])
    relevant={x['file'] for x in entries}
    # Include project source files for candidate callee discovery; cap scanning.
    paths=[f for f in root.rglob('*') if f.suffix in ('.java','.py') and not any(x in f.parts for x in ('.git','.venv','target','build','__pycache__'))]
    paths=sorted(paths)[:500]
    methods=[];calls=[];branches=[];transfers=[];warnings=[]
    for path in paths:
        if path.stat().st_size>1_000_000:continue
        rel=path.relative_to(root).as_posix()
        try:
            src=path.read_text(encoding='utf-8',errors='replace')
            parts=_java_nodes(src,rel) if path.suffix=='.java' else _python_nodes(src,rel)
            for dst,values in zip((methods,calls,branches,transfers),parts):dst.extend(values)
        except (SyntaxError,ImportError,ValueError) as exc:warnings.append(f'{rel}: {type(exc).__name__}')
    by_name={}
    for method in methods:by_name.setdefault(method['method'],[]).append(method)
    impacted={(x['file'],x.get('method')) for x in entries if x.get('method')}
    candidate=[]
    for call in calls:
        for target in by_name.get(call['callee'],[]):
            if (call['file'],call['method']) not in impacted and (target['file'],target['method']) not in impacted:continue
            candidate.append({'caller':call,'callee':target,'evidence_level':'UNRESOLVED_CALL_CANDIDATE','verified_cross_method':False})
            if len(candidate)>=max_items:break
        if len(candidate)>=max_items:break
    result['control_flow']={'branch_sites':[b for b in branches if (b['file'],b['method']) in impacted][:max_items],
                            'level':'BRANCH_AST_NOT_COMPLETE_CFG','verified_cfg_edges':False}
    result['data_flow']={'same_method_transfers':[t for t in transfers if (t['file'],t['method']) in impacted][:max_items],
                         'candidate_cross_method_calls':candidate,'verified_cross_method':False,
                         'alias_analysis':'NOT_AVAILABLE'}
    result['advanced_warnings']=warnings[:40]
    result['evidence_assessment']={'direct_syntax':len(entries),'same_expression':len(result.get('same_expression_flows',[])),
                                    'unresolved_call_candidates':len(candidate),'branch_sites':len(result['control_flow']['branch_sites']),
                                    'confidence_policy':'EVIDENCE_LEVELS_NOT_NUMERIC_SCORES'}
    return result

"""Field impact: syntax-tree-based field references and branch evidence.

Conservative static evidence, NOT complete interprocedural points-to analysis.
"""
from __future__ import annotations
import ast
from pathlib import Path
from collections import defaultdict

SKIP = {'.git','.venv','venv','node_modules','target','build','.gradle','__pycache__'}

def _line(source: bytes, offset: int) -> int:
    return source.count(b'\n', 0, offset) + 1

def _evidence(path, node, kind, field, method, detail, confidence='DIRECT_SYNTAX'):
    return {'file':str(path),'line':node.start_point.row+1 if hasattr(node,'start_point') else getattr(node,'lineno',1),
            'field':field,'method':method,'kind':kind,'detail':detail[:240], 'evidence_level':confidence}

def analyze_java(path: Path, field: str):
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError as exc:
        raise RuntimeError('Install tree-sitter>=0.23 and tree-sitter-java>=0.23') from exc
    parser=Parser(Language(tree_sitter_java.language()))
    source=path.read_bytes()
    tree=parser.parse(source)
    out=[]
    def text(node):return source[node.start_byte:node.end_byte].decode('utf-8','replace')
    def contains(node):
        return any(text(n)==field for n in walk(node) if n.type=='identifier')
    def walk(node):
        yield node
        for c in node.named_children:yield from walk(c)
    def visit(node, method='', conditions=()):
        if node.type in ('method_declaration','constructor_declaration'):
            name=node.child_by_field_name('name')
            method=text(name) if name else method
        if node.type in ('if_statement','switch_expression','switch_statement','for_statement','enhanced_for_statement','while_statement','do_statement','try_statement','catch_clause'):
            conditions=conditions+(f'{node.type}@{node.start_point.row+1}',)
        if node.type=='assignment_expression':
            left=node.child_by_field_name('left');right=node.child_by_field_name('right')
            if left and contains(left):
                out.append(_evidence(path,node,'WRITE',field,method,text(node)))
            elif right and contains(right):
                out.append(_evidence(path,node,'READ',field,method,text(node)))
        elif node.type=='method_invocation':
            name=node.child_by_field_name('name')
            called=text(name) if name else ''
            bean=field[0].upper()+field[1:]
            if called in ('set'+bean,'get'+bean,'is'+bean):
                out.append(_evidence(path,node,'WRITE' if called.startswith('set') else 'READ',field,method,text(node)))
        elif node.type=='field_declaration' and contains(node):
            out.append(_evidence(path,node,'DECLARATION',field,method,text(node)))
        if conditions and out and out[-1]['line']==node.start_point.row+1:
            out[-1]['conditions']=list(conditions)
        for child in node.named_children:visit(child,method,conditions)
    visit(tree.root_node)
    return out,tree.root_node.has_error

def analyze_python(path: Path, field: str):
    source=path.read_text(encoding='utf-8',errors='replace')
    try:tree=ast.parse(source)
    except SyntaxError:return [],True
    out=[]
    class Visitor(ast.NodeVisitor):
        method=''
        conditions=()
        def visit_FunctionDef(self,node):
            prev=self.method;self.method=node.name;self.generic_visit(node);self.method=prev
        visit_AsyncFunctionDef=visit_FunctionDef
        def visit_If(self,node):
            prev=self.conditions;self.conditions=prev+(f'if@{node.lineno}',)
            self.generic_visit(node);self.conditions=prev
        def visit_For(self,node):
            prev=self.conditions;self.conditions=prev+(f'for@{node.lineno}',)
            self.generic_visit(node);self.conditions=prev
        visit_AsyncFor=visit_For
        def visit_While(self,node):
            prev=self.conditions;self.conditions=prev+(f'while@{node.lineno}',)
            self.generic_visit(node);self.conditions=prev
        def visit_Name(self,node):
            if node.id==field:self.record(node,'WRITE' if isinstance(node.ctx,ast.Store) else 'READ')
        def visit_Attribute(self,node):
            if node.attr==field:self.record(node,'WRITE' if isinstance(node.ctx,ast.Store) else 'READ')
            self.generic_visit(node)
        def record(self,node,kind):
            item=_evidence(path,node,kind,field,self.method,ast.get_source_segment(source,node) or field)
            item['conditions']=list(self.conditions);out.append(item)
    Visitor().visit(tree)
    return out,False

def analyze_project(project_path: str, field: str, max_files: int=1500):
    root=Path(project_path).expanduser().resolve()
    if not root.is_dir():raise ValueError('Project directory not found')
    if not field.isidentifier() or len(field)>128:raise ValueError('field must be a valid identifier')
    evidence=[];errors=[];scanned=0
    for path in sorted(root.rglob('*')):
        if path.suffix not in ('.java','.py') or any(part in SKIP for part in path.relative_to(root).parts):continue
        if scanned>=max_files:errors.append('FILE_LIMIT_REACHED');break
        if path.stat().st_size>1_000_000:continue
        scanned+=1
        try:
            items,parse_error=(analyze_java(path.relative_to(root) if False else path,field) if path.suffix=='.java' else analyze_python(path,field))
            for item in items:item['file']=str(path.relative_to(root)).replace('\\','/')
            evidence.extend(items)
            if parse_error:errors.append(f'PARSE_ERROR:{path.relative_to(root)}')
        except Exception as exc:errors.append(f'{path.relative_to(root)}:{type(exc).__name__}:{exc}')
    writers=[x for x in evidence if x['kind']=='WRITE']
    readers=[x for x in evidence if x['kind']=='READ']
    return {'project_path':str(root),'field':field,'files_scanned':scanned,'writers':writers,'readers':readers,
            'declarations':[x for x in evidence if x['kind']=='DECLARATION'],
            'multiple_writers':len({(x['file'],x['method'],x['line']) for x in writers})>1,
            'branch_qualified_references':[x for x in evidence if x.get('conditions')],
            'warnings':errors,'limitations':['Syntax-based field matching; aliasing and polymorphic dispatch not fully resolved',
            'No verified database trigger or cross-service execution trace','Source paths are possible evidence, not executed traces']}


def enrich_field_impact(result: dict) -> dict:
    """Classify direct syntax evidence without claiming resolved interprocedural flow."""
    from pathlib import PurePosixPath
    import re
    evidence = result.get('writers', []) + result.get('readers', [])
    for item in evidence:
        detail = item.get('detail', '')
        file = item.get('file', '').replace('\\', '/')
        is_test = '/test/' in '/' + file or PurePosixPath(file).name.startswith('test_')
        item['source_scope'] = 'TEST' if is_test else 'PRODUCTION'
        if item['kind'] == 'WRITE':
            item['operation_type'] = ('TEST_FIXTURE' if is_test else
                'SETTER_IMPLEMENTATION' if item.get('method', '').startswith('set') and 'this.' in detail else
                'TRANSFORMED_ASSIGNMENT' if re.search(r'\b(?:trim|strip|normalize|format|toUpperCase|toLowerCase)\s*\(', detail) else
                'MAPPER_ASSIGNMENT' if 'Mapper' in file else 'ASSIGNMENT')
        else:
            item['operation_type'] = 'FIELD_READ'
    # Same-expression relationships are verified syntax; types and receiver aliases are not resolved.
    flows=[]
    for writer in result.get('writers', []):
        expression=writer.get('detail','')
        if not expression or writer['source_scope']=='TEST':continue
        for reader in result.get('readers', []):
            if (reader.get('file')==writer.get('file') and reader.get('method')==writer.get('method')
                and reader.get('line')==writer.get('line') and reader.get('detail') in expression):
                transformations=re.findall(r'\b(trim|strip|normalize|format|toUpperCase|toLowerCase)\s*\(',expression)
                flows.append({'file':writer['file'],'line':writer['line'],'method':writer['method'],
                              'source_expression':reader['detail'],'target_expression':expression,
                              'transformations':list(dict.fromkeys(transformations)),
                              'evidence_level':'SAME_EXPRESSION_SYNTAX',
                              'verified_cross_method':False})
    result['same_expression_flows']=flows
    result['production_writers']=sum(x['source_scope']=='PRODUCTION' for x in result.get('writers',[]))
    result['test_writers']=sum(x['source_scope']=='TEST' for x in result.get('writers',[]))
    result['analysis_level']='SYNTAX_WITH_SAME_EXPRESSION_FLOW'
    return result

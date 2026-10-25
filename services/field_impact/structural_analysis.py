"""Conservative structural evidence. Call edges are candidates, never runtime traces."""
from __future__ import annotations
import ast
import re
from pathlib import Path

JAVA_CALL = re.compile(r"(?<![\w.])(?:(?:[A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*\(")
JAVA_METHOD = re.compile(r"\b(?:public|private|protected|static|final|synchronized|abstract|native|default)\s+(?:[\w<>?,\[\].]+\s+)+([A-Za-z_]\w*)\s*\(")
JAVA_BRANCH = re.compile(r"\b(if|else|switch|case|for|while|catch)\s*(?:\(|:|\{|->)")
EXCLUDED_CALLS = {'if','for','while','switch','catch','return','new','throw','super','this'}

def structural_evidence(result: dict, max_edges: int = 2000) -> dict:
    root=Path(result['project_path'])
    evidence=result.get('writers',[])+result.get('readers',[])+result.get('declarations',[])
    files={e['file'] for e in evidence}
    definitions={}; calls=[]; branches=[]
    for relative in sorted(files):
        path=root/relative
        if not path.is_file(): continue
        source=path.read_text(encoding='utf-8',errors='replace')
        if path.suffix=='.py':
            try: tree=ast.parse(source)
            except SyntaxError: continue
            class Scan(ast.NodeVisitor):
                current=None
                def visit_FunctionDef(self,node):
                    definitions.setdefault(node.name,[]).append((relative,node.lineno))
                    previous=self.current;self.current=node.name
                    self.generic_visit(node);self.current=previous
                visit_AsyncFunctionDef=visit_FunctionDef
                def visit_Call(self,node):
                    name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else None
                    if name and self.current: calls.append((relative,self.current,node.lineno,name))
                    self.generic_visit(node)
                def visit_If(self,node): self.branch(node,'if')
                def visit_For(self,node): self.branch(node,'for')
                def visit_While(self,node): self.branch(node,'while')
                def visit_Try(self,node): self.branch(node,'try')
                def branch(self,node,kind):
                    branches.append({'file':relative,'line':node.lineno,'kind':kind,'method':self.current,'evidence_level':'DIRECT_SYNTAX'})
                    self.generic_visit(node)
            Scan().visit(tree)
        elif path.suffix=='.java':
            current=None
            for line_no,line in enumerate(source.splitlines(),1):
                found=JAVA_METHOD.search(line)
                if found:
                    current=found.group(1);definitions.setdefault(current,[]).append((relative,line_no))
                if JAVA_BRANCH.search(line):branches.append({'file':relative,'line':line_no,'kind':'BRANCH_CANDIDATE','method':current,'evidence_level':'LEXICAL_CANDIDATE'})
                for match in JAVA_CALL.finditer(line):
                    name=match.group(1)
                    if current and name not in EXCLUDED_CALLS and name!=current:
                        calls.append((relative,current,line_no,name))
    edges=[];seen=set()
    for file,caller,line,callee in calls:
        for target_file,target_line in definitions.get(callee,[]):
            # Same-file call is a stronger candidate; cross-file dispatch is unresolved.
            key=(file,caller,line,target_file,callee,target_line)
            if key in seen:continue
            seen.add(key)
            edges.append({'caller_file':file,'caller':caller,'line':line,'callee_file':target_file,'callee':callee,
                          'callee_line':target_line,'evidence_level':'CALL_NAME_CANDIDATE',
                          'verified_cross_method':False})
            if len(edges)>=max_edges:break
        if len(edges)>=max_edges:break
    impacted_methods={(e['file'],e['method']) for e in evidence if e.get('method')}
    result['candidate_call_edges']=[e for e in edges if (e['caller_file'],e['caller']) in impacted_methods or (e['callee_file'],e['callee']) in impacted_methods]
    result['branch_sites']=branches[:max_edges]
    result['analysis_capabilities']={'branch_sites':'SYNTAX_OR_LEXICAL','cross_method_calls':'UNRESOLVED_CANDIDATES','cfg':'NOT_FULLY_RESOLVED','alias_analysis':'NOT_AVAILABLE'}
    result['limitations'].append('Call-name matches may be ambiguous across classes; no proven cross-method value propagation')
    return result

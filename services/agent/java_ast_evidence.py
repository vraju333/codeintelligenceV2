"""Optional JDK javac AST observations; only resolved symbols are marked resolved.

No dependencies downloaded, and project source is never executed. Java code is parsed
by javac, which may report unresolved external dependencies without classpath setup.
"""
from __future__ import annotations
import base64
from pathlib import Path
import subprocess
import tempfile


def inspect_java_ast(root: Path, attribute: str, timeout: int = 40) -> dict:
    result = {'status': 'UNAVAILABLE', 'calls': [], 'conditions': [], 'local_transfers': [], 'warnings': []}
    source_root = root / 'src' / 'main' / 'java'
    if not source_root.is_dir():
        result['warnings'].append('Java source directory unavailable')
        return result
    java_source = Path(__file__).with_name('JavaAstEvidence.java')
    if not java_source.is_file():
        result['warnings'].append('JDK AST helper unavailable')
        return result
    try:
        with tempfile.TemporaryDirectory(prefix='ci-javac-') as tmp:
            compile_result = subprocess.run(['javac', '-d', tmp, str(java_source)], capture_output=True, text=True, timeout=timeout)
            if compile_result.returncode:
                result['warnings'].append('JDK AST helper compilation failed: ' + compile_result.stderr[-400:])
                return result
            execution = subprocess.run(['java', '-cp', tmp, 'JavaAstEvidence', str(source_root), tmp], capture_output=True, text=True, timeout=timeout)
            if execution.returncode:
                result['warnings'].append('JDK AST inspection failed: ' + execution.stderr[-400:])
                return result
            result['status'] = 'AST_INSPECTED'
            for line in execution.stdout.splitlines():
                try:
                    fields = [base64.b64decode(v).decode('utf-8') for v in line.split('\t')]
                    if len(fields) != 8:
                        continue
                    kind, file, owner, method, line_no, expression, confidence, target = fields
                    path = Path(file)
                    try:
                        relative = str(path.relative_to(root)).replace('\\', '/')
                    except ValueError:
                        continue
                    item = {'method': owner + '.' + method, 'file': relative, 'line': int(line_no), 'expression': expression, 'status': confidence}
                    if kind == 'CALL':
                        item['target'] = target
                        result['calls'].append(item)
                    elif kind == 'CONDITION' and attribute.casefold() in expression.casefold():
                        item['condition_type'] = target
                        item['branch_structure'] = ('TRUE_AND_ELSE' if target == 'IF_TRUE_ELSE' else 'TRUE_ONLY' if target == 'IF_TRUE_ONLY' else 'LOOP_OR_SWITCH')
                        item['status'] = 'AST_CONDITION_NOT_CFG_VERIFIED'
                        result['conditions'].append(item)
                    elif kind == 'TRANSFER' and attribute.casefold() in (expression + ' ' + target).casefold():
                        item['source_expression'] = expression
                        item['target_setter'] = target
                        item['status'] = 'LOCAL_DIRECT_ARGUMENT_AST'
                        result['local_transfers'].append(item)
                except (ValueError, UnicodeError):
                    continue
            result['calls'] = result['calls'][:500]
            result['conditions'] = result['conditions'][:160]
            result['local_transfers'] = result['local_transfers'][:160]
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['warnings'].append('JDK AST inspection unavailable: ' + type(exc).__name__)
    return result
